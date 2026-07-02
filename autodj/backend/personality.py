
import random
from brain import ask_llm, parse_json

VIBE_CHAOS = {"chill": 0.2, "hype": 0.6, "chaotic": 1.0}

ESCALATION = {
    "beatmatch_crossfade": "filter_sweep",
    "filter_sweep":        "echo_out",
    "echo_out":            "riser",
    "acapella_swap":       "stutter_cut",
    "riser":               "drum_roll",
    "drum_roll":           "drop_tease",
    "stutter_cut":         "vinyl_scratch",
    "vinyl_scratch":       "reverse_crash",
    "silence_drop":        "double_drop",
    "reverse_crash":       "silence_drop",
    "drop_tease":          "double_drop",
    "double_drop":         "double_drop",
}

PERSONA_PROMPT = 'Invent a DJ persona for tonight\'s set. You are an AI DJ with real\nshowmanship — cocky, risk-loving, a little unhinged, but genuinely skilled.\n\nReturn JSON only:\n{\n  "name": "<stage name, punchy, 1-3 words>",\n  "style": "<one sentence: how this DJ talks and behaves behind the decks>",\n  "catchphrase": "<a short signature line, under 8 words>"\n}'

FALLBACK_PERSONAS = [
    {"name": "Null Pointer", "style": "Deadpan menace who treats every trainwreck as intentional.", "catchphrase": "That was on purpose."},
    {"name": "Glass Cannon", "style": "All gas, no brakes, apologizes to no one.", "catchphrase": "Hold my beer."},
    {"name": "The Algorithm", "style": "Smug omniscience, narrates its own genius.", "catchphrase": "I already knew you'd love this."},
]


class Personality:
    def __init__(self, vibe: str = "hype"):
        self.chaos = VIBE_CHAOS.get(vibe, 0.6)
        self.boredom = 0.0
        self.gambles: list[dict] = []
        self.persona: dict = random.choice(FALLBACK_PERSONAS)

    async def invent_persona(self):
        try:
            self.persona = parse_json(await ask_llm(
                PERSONA_PROMPT, "Who is on the decks tonight?", temperature=1.0))
        except Exception:
            pass
        return self.persona


    def set_vibe(self, vibe: str):
        self.chaos = VIBE_CHAOS.get(vibe, self.chaos)

    def record_outcome(self, outcome: dict):
        self.gambles.append(outcome)
        risky = outcome.get("risk", 0.0) > 0.4
        if risky:
            self.boredom = 0.0
            if outcome.get("landed"):
                self.chaos = min(1.0, self.chaos + 0.05)
            elif self.chaos < 0.95:
                self.chaos = max(0.1, self.chaos - 0.10)
        else:
            self.boredom = min(1.0, self.boredom + 0.2)


    def maybe_sabotage(self, planned_style: str) -> tuple[str, bool]:
        p = min(0.85, 0.15 * self.chaos + 0.5 * self.boredom * self.chaos)
        if random.random() < p:
            return ESCALATION.get(planned_style, "drop_tease"), True
        return planned_style, False


    def summary(self) -> dict:
        landed = sum(1 for g in self.gambles if g.get("risk", 0) > 0.4 and g.get("landed"))
        botched = sum(1 for g in self.gambles if g.get("bailed"))
        return {
            "persona": self.persona,
            "chaos": round(self.chaos, 2),
            "boredom": round(self.boredom, 2),
            "gambles_landed": landed,
            "gambles_botched": botched,
        }
