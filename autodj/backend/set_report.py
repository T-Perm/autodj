
from datetime import datetime
from pathlib import Path

from brain import ask_llm

SETS_DIR = Path(__file__).parent.parent / "sets"

RECAP_PROMPT = "You are this DJ persona writing a post-set self-review, in character:\n{persona}\n\nBelow is the raw journal of tonight's set. Write a markdown recap titled with\nyour stage name and the date. Include: the tracklist as played, the risks you\ntook and whether they landed, anything you botched (own it in your own style),\nwhere you went off-script from the plan, and a one-paragraph self-review with\na self-assigned grade. Stay in character throughout. 300 words max."


class SetReport:
    def __init__(self):
        self.started_at = datetime.now()
        self.events: list[str] = []

    def log(self, text: str):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.events.append(f"[{stamp}] {text}")

    def track(self, track: dict):
        self.log(f"PLAYED: {track.get('artist')} - {track.get('title')} "
                 f"[{track.get('bpm', 0):.0f} BPM {track.get('key')}]")

    def transition(self, style: str, risk: float, outcome: dict, sabotaged: bool):
        tags = []
        if sabotaged:
            tags.append("OFF-SCRIPT")
        if outcome.get("bailed"):
            tags.append("REFLEX BAILED IT OUT")
        elif risk > 0.4:
            tags.append("GAMBLE LANDED")
        self.log(f"TRANSITION: {style} (risk {risk:.2f}, max drift "
                 f"{outcome.get('max_drift', 0):.2f} beats) {' | '.join(tags)}")


    async def write(self, persona: dict, plan: dict, summary: dict) -> Path:
        SETS_DIR.mkdir(exist_ok=True)
        name = str(persona.get("name", "autodj")).replace(" ", "_").lower()
        path = SETS_DIR / f"{self.started_at:%Y-%m-%d_%H%M}_{name}.md"

        journal = "\n".join(self.events) or "(nothing happened)"
        phases = ", ".join(f"{p['name']}({p.get('tracks', '?')})"
                           for p in plan.get("phases", []))
        raw = (f"PLAN: {phases}\n"
               f"FINAL STATE: chaos {summary.get('chaos')}, "
               f"{summary.get('gambles_landed')} gambles landed, "
               f"{summary.get('gambles_botched')} botched\n\n{journal}")
        try:
            body = await ask_llm(RECAP_PROMPT.format(persona=persona), raw,
                                 temperature=0.9, max_tokens=800)
        except Exception:
            body = (f"# Set journal - {persona.get('name', 'AutoDJ')}\n\n"
                    f"*(LLM offline - raw journal)*\n\n```\n{raw}\n```")

        path.write_text(body, encoding="utf-8")
        print(f"\n[report] Set recap written -> {path}")
        return path
