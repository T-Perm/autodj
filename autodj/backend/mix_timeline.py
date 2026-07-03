
BLEND_METHODS = {"crossfader", "eq_swap", "filter_blend"}

MOVE_VOCAB = {
    "kill_bass", "bring_bass", "swap_mids", "swap_highs",
    "open_filter", "close_filter", "loop_extend", "fx_send",
}

_BASS_MOVES = {"bring_bass"}


class TimelineError(Exception):
    pass


def validate_timeline(blend_method: str, moves: list, duration_bars: int) -> list[dict]:
    if blend_method not in BLEND_METHODS:
        raise TimelineError(f"unknown blend_method {blend_method!r}")
    if not isinstance(moves, list) or not moves:
        raise TimelineError("moves must be a non-empty list")

    cleaned = []
    saw_bass_handoff = False
    for m in moves:
        if not isinstance(m, dict):
            raise TimelineError(f"move is not an object: {m!r}")
        name = m.get("move")
        deck = m.get("deck")
        at_bar = m.get("at_bar")
        if name not in MOVE_VOCAB:
            raise TimelineError(f"unknown move {name!r}")
        if deck not in ("a", "b"):
            raise TimelineError(f"invalid deck {deck!r} for move {name!r}")
        try:
            at_bar = float(at_bar)
        except (TypeError, ValueError):
            raise TimelineError(f"non-numeric at_bar {at_bar!r} for move {name!r}")
        if not (0.0 <= at_bar <= duration_bars):
            raise TimelineError(
                f"at_bar {at_bar} outside [0, {duration_bars}] for move {name!r}")
        if name in _BASS_MOVES and deck == "b":
            saw_bass_handoff = True
        cleaned.append({"at_bar": at_bar, "move": name, "deck": deck})

    if not saw_bass_handoff:
        raise TimelineError(
            "timeline never brings the incoming deck's bass in — no terminal handoff")

    cleaned.sort(key=lambda mv: mv["at_bar"])
    return cleaned


def pick_fallback_method(current: dict, nxt: dict) -> str:
    bpm_a = current.get("bpm") or 120.0
    bpm_b = nxt.get("bpm") or 120.0
    bpm_gap = abs(bpm_a - bpm_b) / bpm_a
    same_genre = (current.get("genre_hint") and
                 current.get("genre_hint") == nxt.get("genre_hint"))
    if same_genre and bpm_gap <= 0.06:
        return "eq_swap"
    return "crossfader"
