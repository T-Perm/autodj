
import random

from beatgrid import snap_phrase_ms

PACE_MODES = ("auto", "club", "party", "full")

_PLAY_BUDGET_S = {
    "club":  (150, 180),
    "party": (90, 120),
}
_AUTO_BUDGET_BY_VIBE = {
    "chill":   (180, 240),
    "hype":    (100, 150),
    "chaotic": (80, 180),
}

_BLEND_BARS = {
    "club":  (16, 32),
    "party": (4, 8),
    "full":  (4, 16),
}
_AUTO_BLEND_BY_VIBE = {
    "chill":   (16, 32),
    "hype":    (8, 16),
    "chaotic": (4, 16),
}


def blend_bars_range(mode: str, vibe: str) -> tuple[int, int]:
    if mode == "auto":
        return _AUTO_BLEND_BY_VIBE.get(vibe, (8, 16))
    return _BLEND_BARS.get(mode, (8, 16))


def clamp_blend_bars(mode: str, vibe: str, bars: int) -> int:
    lo, hi = blend_bars_range(mode, vibe)
    return max(lo, min(hi, bars))


def mix_out_at_ms(mode: str, vibe: str, phase_energy: float, track: dict):
    if mode not in PACE_MODES or mode == "full":
        return None

    duration_ms = track.get("duration_ms") or 0
    bpm = track.get("bpm") or 120.0
    if duration_ms <= 0:
        return None

    if mode == "auto":
        lo, hi = _AUTO_BUDGET_BY_VIBE.get(vibe, (120, 180))
        budget_s = random.uniform(lo, hi)
        budget_s *= 1.25 - 0.5 * max(0.0, min(1.0, phase_energy))
    else:
        lo, hi = _PLAY_BUDGET_S[mode]
        budget_s = random.uniform(lo, hi)

    mix_in = track.get("mix_in_ms") or 0
    analyzer_out = track.get("mix_out_ms") or max(duration_ms - 30000, 0)
    target = min(analyzer_out, mix_in + budget_s * 1000)

    target = max(target, mix_in + 60000)
    target = min(target, analyzer_out, duration_ms - 10000)

    snapped = snap_phrase_ms(track.get("first_beat_ms"), bpm, target, mode="floor")
    if snapped < mix_in + 45000:
        snapped = target
    return snapped
