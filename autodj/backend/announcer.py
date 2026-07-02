
import asyncio
import hashlib
import time
from pathlib import Path

from brain import ask_llm, parse_json

TAGS_DIR = Path(__file__).parent / "tags"
MIN_GAP_S = 120.0
TAG_SECONDS_GUESS = 3.0

LINES_PROMPT = 'You write spoken drop lines for a DJ set — the short shouted tags a\nhype DJ plays over the mix ("DJ SNAKE!", "are you ready!", "this one\'s dangerous").\n\nThe DJ persona tonight:\n{persona}\n\nWrite 8 lines in this persona\'s voice. Keep each under 10 words, punchy,\nspeakable. Cover these situations (one line each, in order):\n1. set_start   — opening the night\n2. name_drop   — just the DJ announcing itself\n3. hype        — generic energy raiser\n4. gamble_win  — it just landed a risky move and wants credit\n5. gamble_loss — it just botched a move and owns it\n6. moment      — right before a planned showpiece move\n7. phase_peak  — entering the peak of the set\n8. phase_cool  — bringing the energy down\n\nReturn JSON only: {{"set_start": "...", "name_drop": "...", "hype": "...",\n"gamble_win": "...", "gamble_loss": "...", "moment": "...",\n"phase_peak": "...", "phase_cool": "..."}}'

FALLBACK_LINES = {
    "set_start":   "Buckle up. The machine is live.",
    "name_drop":   "You are listening to the algorithm.",
    "hype":        "Hands up. Right now.",
    "gamble_win":  "Told you I had it.",
    "gamble_loss": "That was on purpose.",
    "moment":      "Watch this.",
    "phase_peak":  "This is the summit. Jump.",
    "phase_cool":  "Breathe. We're not done.",
}


class Announcer:
    def __init__(self, midi, grid=None):
        self.midi = midi
        self.lines: dict[str, str] = dict(FALLBACK_LINES)
        self.wavs: dict[str, Path] = {}
        self._last_tag = 0.0
        self._enabled = True

    async def prepare(self, persona: dict):
        try:
            got = parse_json(await ask_llm(
                LINES_PROMPT.format(persona=persona), "Write tonight's tags.",
                temperature=1.0))
            self.lines.update({k: v for k, v in got.items() if isinstance(v, str) and v})
        except Exception:
            pass
        try:
            await asyncio.get_running_loop().run_in_executor(None, self._render_all)
        except Exception as e:
            print(f"[announcer] TTS unavailable ({e}) — running silent")
            self._enabled = False

    def _render_all(self):
        import pyttsx3
        TAGS_DIR.mkdir(exist_ok=True)
        engine = None
        for kind, text in self.lines.items():
            wav = TAGS_DIR / f"{hashlib.sha1(text.encode()).hexdigest()[:12]}.wav"
            if not wav.exists():
                if engine is None:
                    engine = pyttsx3.init()
                    engine.setProperty("rate", 160)
                engine.save_to_file(text, str(wav))
            self.wavs[kind] = wav
        if engine is not None:
            engine.runAndWait()


    def can_speak(self) -> bool:
        return self._enabled and (time.monotonic() - self._last_tag) >= MIN_GAP_S

    async def say(self, kind: str, deck: str | None = None, force: bool = False):
        wav = self.wavs.get(kind)
        if wav is None or not wav.exists() or not self._enabled:
            return
        if not force and not self.can_speak():
            return
        self._last_tag = time.monotonic()
        print(f'   🎙  "{self.lines.get(kind, "")}"')
        try:
            import winsound
            winsound.PlaySound(str(wav), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception as e:
            print(f"[announcer] playback failed: {e}")
            return
        if deck and self.midi:
            self.midi.set_filter(deck, 0.38)
            await asyncio.sleep(TAG_SECONDS_GUESS)
            self.midi.set_filter(deck, 0.5)
