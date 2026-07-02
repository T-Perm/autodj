import json
import os
import httpx
from dotenv import load_dotenv
from analyzer import CAMELOT_COMPATIBLE

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

SYSTEM_PROMPT = 'You are an expert AI DJ controlling a party. Your job is to pick the perfect next track and transition style to keep the crowd engaged.\n\nHow a real DJ mixes (you follow these mechanics):\n- Music is built in 32-beat (8-bar) phrases. Every blend opens, swaps basslines, and closes on phrase boundaries — never mid-phrase.\n- You never play whole tracks: you mix out after the last chorus/second drop and bring the next one in early, overlapping both tracks (intro-over-outro).\n- The incoming track enters at a deliberate point:\n  * "intro" — its natural mix-in point, ridden in under the outgoing outro (the default, safest)\n  * "breakdown" — two phrases before its drop, so its build plays under the outgoing track and the drop lands right as the old track leaves (energy UP)\n  * "drop" — one phrase before the drop, for drop-timed blends (silence_drop, double_drop, drop_tease)\n- Longer blends (16-32 bars) read as confident and smooth; short ones (4-8 bars) keep energy punchy. Match the blend length to the moment.\n\nRules:\n- Avoid playing the same genre back-to-back unless building energy intentionally\n- For genre switches, prefer echo_out or filter_sweep to smooth the change\n- Match transition style to genre context:\n  * hip-hop/trap → vinyl_scratch, stutter_cut, silence_drop\n  * electronic/house/techno → filter_sweep, riser, beatmatch_crossfade\n  * pop → drum_roll, riser, beatmatch_crossfade\n  * ambient/chill → echo_out, acapella_swap, filter_sweep\n- Chaotic vibe: allow silence_drop, stutter_cut, unexpected genre clashes, short transitions\n- Chill vibe: prefer smooth blends, longer transition durations, harmonic keys\n- Hype vibe: build energy progressively, use drum_roll and riser for peaks\n- drop_tease and double_drop are showpiece moves — save them for high-energy moments, never twice in a row\n- ALWAYS return valid JSON only, no explanation outside the JSON block'

USER_TEMPLATE = 'Current vibe setting: {vibe}\n{directive}\n\nNow playing:\n{current_track}\n\nLast 5 tracks played:\n{history}\n\nCandidate next tracks (harmonically compatible with current key {current_key}):\n{candidates}\n\nBlend length must be between {blend_min} and {blend_max} bars (current pacing).\n\nPick the best next track and transition. Return JSON only:\n{{\n  "next_track_id": "<id from candidates>",\n  "transition_style": "<one of: {styles}>",\n  "transition_duration_bars": <int, {blend_min}-{blend_max}>,\n  "entry_point": "<intro|breakdown|drop>",\n  "reasoning": "<one sentence>"\n}}'


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
    )

    content = await ask_llm(SYSTEM_PROMPT, user_msg, max_tokens=256)
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

    return result


def get_compatible_candidates(tracks: list[dict], current_key: str) -> list[dict]:
    compatible_keys = set(CAMELOT_COMPATIBLE.get(current_key, [current_key]))
    return [t for t in tracks if t.get("key") in compatible_keys]
