
import asyncio
import time


class BeatmatchEngine:
    def __init__(self, midi, grid):
        self.midi = midi
        self.grid = grid
        self._rate_cc = {"A": 0.5, "B": 0.5}
        self._lock_state = {"A": False, "B": False}
        self._lock_quality = {"A": None, "B": None}
        self._tasks: dict[str, asyncio.Task] = {}

    def reset_deck(self, deck: str):
        self._cancel(deck)
        self._rate_cc[deck] = 0.5
        self._lock_state[deck] = False
        self._lock_quality[deck] = None

    def is_locked(self, deck: str) -> bool:
        return self._lock_state.get(deck, False)

    def lock_quality(self, deck: str):
        return self._lock_quality.get(deck)

    def _cancel(self, deck: str):
        task = self._tasks.get(deck)
        if task and not task.done():
            task.cancel()

    async def start_match(self, target: str, reference: str,
                          tempo_tolerance_bpm: float = 0.15,
                          phase_tolerance_beats: float = 0.03,
                          timeout_s: float = 20.0,
                          tempo_only: bool = False) -> bool:
        self._cancel(target)
        task = asyncio.create_task(self._run_match(
            target, reference, tempo_tolerance_bpm, phase_tolerance_beats,
            timeout_s, tempo_only))
        self._tasks[target] = task
        try:
            return await task
        except asyncio.CancelledError:
            return False

    async def rephase(self, target: str, reference: str,
                      phase_tolerance_beats: float = 0.03,
                      timeout_s: float = 5.0) -> bool:
        self._cancel(target)
        task = asyncio.create_task(self._run_rephase(
            target, reference, phase_tolerance_beats, timeout_s))
        self._tasks[target] = task
        try:
            return await task
        except asyncio.CancelledError:
            return False

    async def _run_rephase(self, target: str, reference: str,
                           phase_tol: float, timeout_s: float) -> bool:
        m, g = self.midi, self.grid
        deadline = time.monotonic() + timeout_s
        if not m.deck_state[target]["playing"]:
            m.play(target)
        if await self._converge_phase(target, reference, phase_tol, deadline):
            self._lock_state[target] = True
            self._lock_quality[target] = g.drift_beats(reference, target)
            return True
        print(f"[Beatmatch] Phase re-lock on deck {target} timed out — falling back to beatsync")
        m.enable_sync(target)
        await asyncio.sleep(0.3)
        self._lock_state[target] = True
        self._lock_quality[target] = g.drift_beats(reference, target)
        return False

    async def _run_match(self, target: str, reference: str,
                         tempo_tol: float, phase_tol: float, timeout_s: float,
                         tempo_only: bool = False) -> bool:
        m, g = self.midi, self.grid
        self._lock_state[target] = False
        deadline = time.monotonic() + timeout_s

        if not m.deck_state[target]["playing"]:
            m.play(target)

        locked_tempo = await self._converge_tempo(target, reference, tempo_tol, deadline)
        if tempo_only:
            self._lock_quality[target] = g.drift_beats(reference, target)
            return locked_tempo
        locked_phase = locked_tempo and await self._converge_phase(target, reference, phase_tol, deadline)

        if locked_tempo and locked_phase:
            self._lock_state[target] = True
            self._lock_quality[target] = g.drift_beats(reference, target)
            return True

        print(f"[Beatmatch] Manual match on deck {target} timed out — falling back to beatsync")
        m.enable_sync(target)
        await asyncio.sleep(0.3)
        self._lock_state[target] = True
        self._lock_quality[target] = g.drift_beats(reference, target)
        return False

    async def _converge_tempo(self, target: str, reference: str,
                              tolerance_bpm: float, deadline: float) -> bool:
        m = self.midi
        step = 0.08
        MIN_STEP = 0.0005
        prev_err = None
        while time.monotonic() < deadline:
            target_bpm = m.deck_state[reference]["bpm"] or 120.0
            current_bpm = m.deck_state[target]["bpm"] or 120.0
            err = target_bpm - current_bpm
            if abs(err) <= tolerance_bpm:
                return True
            if prev_err is not None and (err > 0) != (prev_err > 0):
                step = max(MIN_STEP, step * 0.5)
            direction = 1 if err > 0 else -1
            self._rate_cc[target] = max(0.0, min(1.0, self._rate_cc[target] + direction * step))
            m.set_rate(target, self._rate_cc[target])
            prev_err = err
            await asyncio.sleep(0.15)
        return False

    async def _converge_phase(self, target: str, reference: str,
                              tolerance_beats: float, deadline: float) -> bool:
        m, g = self.midi, self.grid
        NUDGE_BURST_S = 0.06
        SETTLE_S = 0.20
        while time.monotonic() < deadline:
            if g.drift_beats(reference, target) <= tolerance_beats:
                return True
            phase_err = (g.beat_phase(target) - g.beat_phase(reference) + 0.5) % 1.0 - 0.5
            direction = "down" if phase_err > 0 else "up"
            m.rate_nudge(target, direction, True)
            await asyncio.sleep(NUDGE_BURST_S)
            m.rate_nudge(target, direction, False)
            await asyncio.sleep(SETTLE_S)
        return False
