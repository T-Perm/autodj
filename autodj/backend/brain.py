import json
import os
import httpx
from dotenv import load_dotenv
from analyzer import CAMELOT_COMPATIBLE
from mix_timeline import (
    BLEND_METHODS, MOVE_VOCAB, TimelineError, validate_timeline, pick_fallback_method,
)

load_dotenv()

NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct")

TRANSITION_STYLES = [
    "beatmatch_crossfade",
    "filter_sweep",
    "echo_out",
    "drum_roll",
    "riser",
    "stutter_cut",
    "vinyl_scratch",
    "silence_drop",
    "reverse_crash",
    "acapella_swap",
    "drop_tease",
    "double_drop",
]


async def ask_llm(system: str, user: str, temperature: float = 0.7,
                  max_tokens: int = 512) -> str:
    if not NVIDIA_API_KEY:
        raise RuntimeError(
            "NVIDIA_API_KEY is not set. Get a key at build.nvidia.com and "
            "add it to autodj/.env as NVIDIA_API_KEY=..."
        )
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            NVIDIA_URL,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {NVIDIA_API_KEY}",
            },
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def parse_json(content: str) -> dict:
    if "```" in content:
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    return json.loads(content)

SYSTEM_PROMPT = 'You are an expert AI DJ controlling a party. Your job is to pick the perfect next track and direct exactly how to mix into it - the way a real DJ works the mixer, not just crossfading two songs.\n\nHow a real DJ mixes (you follow these mechanics):\n- Music is built in 32-beat (8-bar) phrases. Every blend opens, swaps basslines, and closes on phrase boundaries - never mid-phrase.\n- You never play whole tracks: you mix out after the last chorus/second drop and bring the next one in early, overlapping both tracks (intro-over-outro).\n- The bassline always trades in one quick swap right on a downbeat (the "1") - a slow mid-phrase bass fade is the amateur tell. This happens automatically at the right moment no matter what else you direct.\n\nChoosing a blend_method - this is the single most important decision, and it depends on how compatible the two tracks are:\n- "eq_swap" - the correct default for same-genre, tempo-close pairs (house-into-house, hip-hop-into-hip-hop, <6% BPM gap). A real DJ barely touches the crossfader here: they leave both channels open and blend entirely on the 3-band EQ - kill the outgoing track\'s bass, bring the incoming track\'s bass in on the phrase, then trade mids/highs. Pick this whenever the pairing is clean.\n- "filter_blend" - for EDM/techno drop transitions or when you want a sweeping, building feel: a low-pass/high-pass filter sweep is the primary tool instead of the EQ, crossfader stays mostly parked until the very end.\n- "crossfader" - for big energy or tempo gaps, or genre switches where the tracks shouldn\'t overlap cleanly for long - this is the classic long blend, crossfader does the work.\n\nDirecting the blend (moves) - after picking blend_method, give a short list of the real moves you want, each a bar offset (0 = blend start, measured in bars) + a move name + which deck (a=outgoing, b=incoming):\n  kill_bass, bring_bass, swap_mids, swap_highs, open_filter, close_filter, loop_extend, fx_send\n- ALWAYS include at least one "bring_bass" move on deck "b" - the incoming track\'s bass must land, that\'s the whole point of the swap.\n- For eq_swap: typically kill_bass(a) near bar 0, bring_bass(b) near the swap point (around 2/3 through the blend), then swap_mids(b)/swap_highs(b) shortly after.\n- For filter_blend: open_filter(b) and close_filter(a) across the build, bring_bass(b) at the swap point.\n- loop_extend is for fixing awkward phrase alignment - loop a bar of the outgoing track to buy time before the blend proper starts. Use sparingly.\n- fx_send adds an echo/reverb tail - good on the outgoing deck right before it leaves.\n\nOther rules:\n- Avoid playing the same genre back-to-back unless building energy intentionally.\n- drop_tease and double_drop transition_style picks are showpiece moves - save them for high-energy moments, never twice in a row.\n- ALWAYS return valid JSON only, no explanation outside the JSON block.'

USER_TEMPLATE = 'Current vibe setting: {vibe}\n{directive}\n\nNow playing:\n{current_track}\n\nLast 5 tracks played:\n{history}\n\nCandidate next tracks (harmonically compatible with current key {current_key}):\n{candidates}\n\nBlend length must be between {blend_min} and {blend_max} bars (current pacing).\n\nPick the best next track and direct the blend. Return JSON only:\n{{\n  "next_track_id": "<id from candidates>",\n  "transition_style": "<one of: {styles}>",\n  "transition_duration_bars": <int, {blend_min}-{blend_max}>,\n  "entry_point": "<intro|breakdown|drop>",\n  "blend_method": "<one of: {blend_methods}>",\n  "moves": [{{"at_bar": <number 0-transition_duration_bars>, "move": "<one of: {move_vocab}>", "deck": "a"|"b"}}, ...],\n  "reasoning": "<one sentence>"\n}}'


def _format_track(t: dict) -> str:
    return f"  [{t['id']}] {t['artist']} - {t['title']} | BPM:{t['bpm']} Key:{t['key']} Energy:{t['energy']:.2f} Mood:{t['mood']} Genre:{t['genre_hint']}"


async def decide_next(
    current_track: dict,
    history: list[dict],
    candidates: list[dict],
    vibe: str = "hype",
    directive: str = "",
    blend_range: tuple[int, int] = (4, 16),
) -> dict:
    current_str = _format_track(current_track)
    history_str = "\n".join(_format_track(t) for t in history[-5:]) or "  (none yet)"
    candidates_str = "\n".join(_format_track(t) for t in candidates[:20])

    user_msg = USER_TEMPLATE.format(
        vibe=vibe,
        directive=directive,
        current_track=current_str,
        history=history_str,
        current_key=current_track.get("key", "1A"),
        candidates=candidates_str,
        styles="|".join(TRANSITION_STYLES),
        blend_min=blend_range[0],
        blend_max=blend_range[1],
        blend_methods="|".join(sorted(BLEND_METHODS)),
        move_vocab="|".join(sorted(MOVE_VOCAB)),
    )

    content = await ask_llm(SYSTEM_PROMPT, user_msg, max_tokens=512)
    result = parse_json(content)

    if result.get("transition_style") not in TRANSITION_STYLES:
        result["transition_style"] = "beatmatch_crossfade"

    try:
        bars = int(result.get("transition_duration_bars"))
    except (TypeError, ValueError):
        bars = (blend_range[0] + blend_range[1]) // 2
    result["transition_duration_bars"] = max(blend_range[0], min(blend_range[1], bars))

    if result.get("entry_point") not in ("intro", "breakdown", "drop"):
        result["entry_point"] = "intro"

    result["moves"] = result.get("moves")
    result["blend_method"] = result.get("blend_method")

    return result


def get_compatible_candidates(tracks: list[dict], current_key: str) -> list[dict]:
    compatible_keys = set(CAMELOT_COMPATIBLE.get(current_key, [current_key]))
    return [t for t in tracks if t.get("key") in compatible_keys]
