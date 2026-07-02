
from brain import ask_llm, parse_json


def _coerce_int(value, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except (ValueError, TypeError):
            return default
    if isinstance(value, (list, tuple)) and value:
        return _coerce_int(value[0], default)
    return default


def _coerce_float(value, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (ValueError, TypeError):
            return default
    if isinstance(value, (list, tuple)) and value:
        return _coerce_float(value[0], default)
    return default

PLAN_PROMPT = 'You are planning tonight\'s DJ set arc. Given the library summary and\nvibe, design a set plan.\n\nReturn JSON only:\n{\n  "phases": [\n    {"name": "warmup", "tracks": <int>, "energy": <0.0-1.0>, "note": "<one line>"},\n    ... 4-5 phases total (e.g. warmup, build, peak, cooldown, encore)\n  ],\n  "moments": [\n    {"after_track": <int, absolute track number>, "move": "<one of: double_drop|drop_tease|silence_drop|reverse_crash>", "why": "<one line>"}\n  ]\n}\n2-3 moments max. Put the flashiest at the peak.'

FALLBACK_PLAN = {
    "phases": [
        {"name": "warmup",   "tracks": 3, "energy": 0.4, "note": "ease them in"},
        {"name": "build",    "tracks": 4, "energy": 0.6, "note": "raise the floor"},
        {"name": "peak",     "tracks": 4, "energy": 0.9, "note": "full send"},
        {"name": "cooldown", "tracks": 3, "energy": 0.5, "note": "let them breathe"},
        {"name": "encore",   "tracks": 2, "energy": 0.8, "note": "one last hit"},
    ],
    "moments": [
        {"after_track": 8,  "move": "double_drop",  "why": "peak impact"},
        {"after_track": 12, "move": "silence_drop", "why": "reset before cooldown"},
    ],
}


class Director:
    def __init__(self):
        self.plan: dict = FALLBACK_PLAN
        self.tracks_played = 0
        self.went_off_script: list[str] = []

    async def plan_set(self, library: list[dict], vibe: str):
        moods = {}
        for t in library:
            moods[t.get("mood", "?")] = moods.get(t.get("mood", "?"), 0) + 1
        bpms = [t.get("bpm") or 120 for t in library]
        summary = (f"{len(library)} tracks, BPM {min(bpms):.0f}-{max(bpms):.0f}, "
                   f"moods: {', '.join(f'{k}×{v}' for k, v in sorted(moods.items()))}")
        try:
            plan = parse_json(await ask_llm(
                PLAN_PROMPT, f"Library: {summary}\nVibe: {vibe}", temperature=0.9))
            if plan.get("phases"):
                self.plan = plan
        except Exception:
            pass
        return self.plan


    def current_phase(self) -> dict:
        n = self.tracks_played
        for phase in self.plan["phases"]:
            n -= _coerce_int(phase.get("tracks"), 3)
            if n < 0:
                return phase
        return self.plan["phases"][-1]

    def due_moment(self) -> dict | None:
        for m in self.plan.get("moments", []):
            if not m.get("done") and self.tracks_played >= _coerce_int(m.get("after_track"), 0):
                return m
        return None

    def get_directive(self) -> dict:
        phase = self.current_phase()
        return {
            "phase": phase.get("name", "?"),
            "energy_target": _coerce_float(phase.get("energy"), 0.6),
            "note": phase.get("note", ""),
            "moment": self.due_moment(),
        }

    def directive_text(self) -> str:
        d = self.get_directive()
        text = (f"Set phase: {d['phase']} — target energy {d['energy_target']:.1f} "
                f"({d['note']})")
        if d["moment"]:
            text += (f"\nSCHEDULED MOMENT DUE: perform a {d['moment']['move']} "
                     f"({d['moment'].get('why', '')}) — pick a track that sets it up.")
        return text


    def advance(self, moment_used: str | None = None, sabotaged: str | None = None):
        self.tracks_played += 1
        if moment_used:
            for m in self.plan.get("moments", []):
                if not m.get("done") and m.get("move") == moment_used:
                    m["done"] = True
                    break
        if sabotaged:
            self.went_off_script.append(
                f"track {self.tracks_played}: went off-script with {sabotaged}")
