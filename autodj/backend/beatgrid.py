
import asyncio
import time
from typing import Optional

PHRASE_BEATS = 32


def snap_phrase_ms(first_beat_ms: Optional[int], bpm: Optional[float],
                   ms: float, mode: str = "floor") -> float:
    first = first_beat_ms or 0
    phrase_ms = PHRASE_BEATS * 60000.0 / (bpm or 120.0)
    n = (ms - first) / phrase_ms
    n = round(n) if mode == "nearest" else int(n // 1)
    return max(first, first + n * phrase_ms)


class BeatGrid:
    def __init__(self, midi):
        self.midi = midi
        self._first_beat_s = {"A": 0.0, "B": 0.0}

    def set_track(self, deck: str, first_beat_ms: Optional[int]):
        self._first_beat_s[deck] = (first_beat_ms or 0) / 1000.0


    def position_s(self, deck: str) -> float:
        st = self.midi.deck_state[deck]
        pos = st["playposition"] * st["duration_s"]
        if st["playing"] and st["pos_wall"] > 0:
            pos += time.monotonic() - st["pos_wall"]
        return pos


    def beat_len_s(self, deck: str) -> float:
        bpm = self.midi.deck_state[deck]["bpm"] or 120.0
        return 60.0 / bpm

    def beat_index(self, deck: str) -> float:
        return (self.position_s(deck) - self._first_beat_s[deck]) / self.beat_len_s(deck)

    def beat_phase(self, deck: str) -> float:
        return self.beat_index(deck) % 1.0

    def bar_phase(self, deck: str) -> float:
        return (self.beat_index(deck) / 4.0) % 1.0

    def seconds_until_beat(self, deck: str, subdivision: float = 1.0) -> float:
        unit = self.beat_len_s(deck) * subdivision
        elapsed = self.position_s(deck) - self._first_beat_s[deck]
        return (-elapsed) % unit or 0.0

    async def wait_for_beat(self, deck: str, subdivision: float = 1.0,
                            max_wait_s: float = 10.0):
        wait = min(self.seconds_until_beat(deck, subdivision), max_wait_s)
        if wait > 0:
            await asyncio.sleep(wait)

    def phrase_beat(self, deck: str) -> float:
        return self.beat_index(deck) % PHRASE_BEATS

    async def wait_for_phrase(self, deck: str, max_wait_s: Optional[float] = None):
        if max_wait_s is None:
            max_wait_s = self.beat_len_s(deck) * PHRASE_BEATS + 0.5
        wait = min(self.seconds_until_beat(deck, subdivision=PHRASE_BEATS), max_wait_s)
        if wait > 0:
            await asyncio.sleep(wait)

    def drift_beats(self, deck_a: str, deck_b: str) -> float:
        diff = abs(self.beat_phase(deck_a) - self.beat_phase(deck_b))
        return min(diff, 1.0 - diff)
