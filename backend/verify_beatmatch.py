
import asyncio
import time

from beatgrid import BeatGrid
from beatmatch_engine import BeatmatchEngine

RATE_RANGE = 0.08
NUDGE_GAIN = 3.0


class FakeMidi:
    def __init__(self, bpm_a: float, bpm_b: float, phase_offset_beats: float = 0.4):
        now = time.monotonic()
        self.deck_state = {
            "A": {"playposition": 0.0, "bpm": bpm_a, "duration_s": 300.0, "playing": True, "pos_wall": now},
            "B": {"playposition": 0.0, "bpm": bpm_b, "duration_s": 300.0, "playing": False, "pos_wall": now},
        }
        self._file_bpm = {"A": bpm_a, "B": bpm_b}
        self._rate_cc = {"A": 0.5, "B": 0.5}
        self._sim_pos_s = {"A": 0.0, "B": phase_offset_beats * (60.0 / bpm_b)}
        self._nudge_start = {}
        self._nudge_extra_beats = {"A": 0.0, "B": 0.0}
        self.sync_calls = []

    def set_rate(self, deck, value):
        raw = max(0, min(16383, round(value * 16383)))
        self._rate_cc[deck] = raw / 16383.0

    def rate_nudge(self, deck, direction, active):
        key = (deck, direction)
        if active:
            self._nudge_start[key] = time.monotonic()
        else:
            start = self._nudge_start.pop(key, None)
            if start is not None:
                dt = time.monotonic() - start
                sign = 1 if direction == "up" else -1
                self._nudge_extra_beats[deck] += sign * dt * NUDGE_GAIN

    def enable_sync(self, deck):
        self.sync_calls.append(deck)
        other = "A" if deck == "B" else "B"
        self.deck_state[deck]["bpm"] = self.deck_state[other]["bpm"]
        self._sim_pos_s[deck] = self._sim_pos_s[other]

    def play(self, deck):
        self.deck_state[deck]["playing"] = True
        self.deck_state[deck]["pos_wall"] = time.monotonic()

    async def tick_loop(self):
        last = time.monotonic()
        while True:
            await asyncio.sleep(0.05)
            now = time.monotonic()
            dt = now - last
            last = now
            for deck in ("A", "B"):
                st = self.deck_state[deck]
                if not st["playing"]:
                    st["pos_wall"] = now
                    continue
                effective_bpm = self._file_bpm[deck] * (1 + (self._rate_cc[deck] - 0.5) * 2 * RATE_RANGE)
                st["bpm"] = effective_bpm
                self._sim_pos_s[deck] += dt
                extra = self._nudge_extra_beats[deck]
                if extra:
                    self._sim_pos_s[deck] += extra * (60.0 / effective_bpm)
                    self._nudge_extra_beats[deck] = 0.0
                st["playposition"] = min(0.999, self._sim_pos_s[deck] / st["duration_s"])
                st["pos_wall"] = now


async def run_case(name: str, bpm_a: float, bpm_b: float, phase_offset_beats: float,
                    expect_lock: bool = True):
    midi = FakeMidi(bpm_a, bpm_b, phase_offset_beats)
    grid = BeatGrid(midi)
    grid.set_track("A", 0)
    grid.set_track("B", 0)
    engine = BeatmatchEngine(midi, grid)

    tick = asyncio.create_task(midi.tick_loop())
    try:
        locked = await engine.start_match("B", "A", timeout_s=15.0)
    finally:
        tick.cancel()

    bpm_err = abs(midi.deck_state["A"]["bpm"] - midi.deck_state["B"]["bpm"])
    drift = grid.drift_beats("A", "B")
    fell_back = bool(midi.sync_calls)
    status = "locked" if locked else "fell back to sync"
    print(f"[{name}] {status} - bpm_err={bpm_err:.3f} drift={drift:.3f} beats "
          f"fallback={fell_back}")

    assert locked == expect_lock, f"{name}: expected lock={expect_lock}, got {locked}"
    if expect_lock:
        assert not fell_back, f"{name}: should not have fallen back to beatsync"
        assert bpm_err < 0.2, f"{name}: bpm error too high ({bpm_err})"
        assert drift < 0.05, f"{name}: phase drift too high ({drift})"


async def main():
    await run_case("close-bpm, phase-offset", bpm_a=128.0, bpm_b=130.5, phase_offset_beats=0.4)
    await run_case("already-matched", bpm_a=124.0, bpm_b=124.0, phase_offset_beats=0.02)
    print("PASS")


if __name__ == "__main__":
    asyncio.run(main())
