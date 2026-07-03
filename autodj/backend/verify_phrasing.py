
import asyncio
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from beatgrid import BeatGrid, snap_phrase_ms, PHRASE_BEATS
from pacing import mix_out_at_ms, blend_bars_range, clamp_blend_bars
from verify_beatmatch import FakeMidi
from performer import Performer, FLAVORS, _phase_bars



def check_math():
    phrase = PHRASE_BEATS * 60000.0 / 128.0
    assert abs(snap_phrase_ms(500, 128.0, 31000, "floor") - (500 + phrase * 2)) < 1
    assert abs(snap_phrase_ms(500, 128.0, 22000, "nearest") - (500 + phrase)) < 1
    assert snap_phrase_ms(500, 128.0, 100, "floor") == 500

    track = {"duration_ms": 300000, "mix_in_ms": 15000, "mix_out_ms": 270000,
             "first_beat_ms": 500, "bpm": 128.0}

    assert mix_out_at_ms("full", "hype", 0.6, track) is None

    for mode, vibe in (("party", "hype"), ("club", "hype"), ("auto", "chill"),
                       ("auto", "hype"), ("auto", "chaotic")):
        v = mix_out_at_ms(mode, vibe, 0.9, track)
        assert v is not None
        assert 60000 <= v <= 270000, f"{mode}/{vibe}: {v} out of range"
        rem = (v - 500) % phrase
        assert min(rem, phrase - rem) < 1 or v > 60000, f"{mode}: not phrase-aligned"

    party = max(mix_out_at_ms("party", "hype", 0.6, track) for _ in range(20))
    chill = min(mix_out_at_ms("auto", "chill", 0.2, track) for _ in range(20))
    assert party < chill, f"party ({party}) should mix out before chill-auto ({chill})"

    assert blend_bars_range("club", "hype") == (16, 32)
    assert clamp_blend_bars("party", "hype", 32) == 8
    assert clamp_blend_bars("club", "hype", 4) == 16
    print("[math] phrase snap, pacing budgets, blend clamps — OK")



TRACK_A = {
    "id": "a" * 64, "title": "Track A", "artist": "x", "bpm": 128.0, "key": "8A",
    "energy": 0.7, "mood": "hype", "genre_hint": "house",
    "duration_ms": 300000, "mix_in_ms": 10000, "mix_out_ms": 270000,
    "first_beat_ms": 500, "drop_ms": 60000,
}
TRACK_B = {
    "id": "b" * 64, "title": "Track B", "artist": "y", "bpm": 126.0, "key": "8A",
    "energy": 0.8, "mood": "hype", "genre_hint": "house",
    "duration_ms": 300000, "mix_in_ms": 15000, "mix_out_ms": 270000,
    "first_beat_ms": 500, "drop_ms": 60000,
}
TRACK_B_MATCHED = dict(TRACK_B, bpm=128.0, drop_ms=60500)


class HarnessMidi(FakeMidi):

    def __init__(self, library: dict):
        super().__init__(bpm_a=128.0, bpm_b=126.0, phase_offset_beats=0.0)
        self.library = library
        self.mixxx_id_map = {tid: i + 1 for i, tid in enumerate(library)}
        self.calls: list = []
        self._hotcues: dict = {}
        self.loads: list = []
        self._t0 = time.monotonic()
        self.xf_trace: list = []
        self.vol_trace = {"A": [], "B": []}
        self.pause_times = {"A": [], "B": []}
        for d in ("A", "B"):
            self.deck_state[d]["duration_s"] = 0.0
            self.deck_state[d]["playing"] = False

    def _t(self) -> float:
        return time.monotonic() - self._t0

    def load_track(self, deck, track_id):
        t = self.library[track_id]
        self.deck_state[deck]["duration_s"] = t["duration_ms"] / 1000.0
        self.deck_state[deck]["playing"] = False
        self._file_bpm[deck] = t["bpm"]
        self.deck_state[deck]["bpm"] = t["bpm"]
        self._sim_pos_s[deck] = 0.0
        self.deck_state[deck]["playposition"] = 0.0
        self.loads.append((deck, track_id))

    def pause(self, deck):
        self.pause_times[deck].append(self._t())
        self.deck_state[deck]["playing"] = False

    def set_crossfader(self, value):
        self.xf_trace.append((self._t(), value))

    def set_deck_volume(self, deck, value):
        self.vol_trace[deck].append((self._t(), value))

    def seek(self, deck, position):
        self._sim_pos_s[deck] = position * self.deck_state[deck]["duration_s"]
        self.deck_state[deck]["playposition"] = position

    def hotcue_set(self, deck, n):
        self._hotcues[(deck, n)] = self._sim_pos_s[deck]

    def hotcue_jump(self, deck, n):
        p = self._hotcues.get((deck, n))
        if p is not None:
            self._sim_pos_s[deck] = p
            self.deck_state[deck]["playposition"] = p / self.deck_state[deck]["duration_s"]

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        def _noop(*args, **kwargs):
            self.calls.append((name, args))
        return _noop



async def run_flow():
    import queue_manager
    queue_manager.Announcer = lambda midi: None

    midi = HarnessMidi({TRACK_A["id"]: TRACK_A, TRACK_B["id"]: TRACK_B})
    qm = queue_manager.QueueManager(session_factory=lambda: None, midi=midi)
    qm.is_running = True
    qm.pace_mode = "party"

    async def no_pick():
        return None
    qm._pick_next = no_pick

    tick = asyncio.create_task(midi.tick_loop())
    try:
        assert await qm._load_and_verify("A", TRACK_A)
        midi.play("A")
        qm.now_playing = dict(TRACK_A)
        nxt = dict(TRACK_B)
        nxt.update(transition_style="beatmatch_crossfade",
                   transition_duration_bars=16, entry_point="intro")
        qm.up_next = [nxt]

        paced = qm._mix_out_for(qm.now_playing, 300.0)
        assert 60000 <= paced <= 150000, f"party mix-out {paced}ms not early"
        print(f"[pacing] party mix-out at {paced / 1000:.1f}s (analyzer said 270s) — OK")

        plan = qm._blend_plan(300.0)
        assert plan is not None
        bar_ms = (60000.0 / midi.deck_state["A"]["bpm"]) * 4
        expected_start = paced - plan["bars"] * bar_ms
        assert abs(plan["blend_start_ms"] - expected_start) < 1
        assert abs(plan["trigger_ms"] - (expected_start - queue_manager.LEAD_MS)) < 1

        fired = []
        real_begin = qm._begin_transition
        async def spy_begin(p=None):
            fired.append(p)
        qm._begin_transition = spy_begin
        state = {"A": dict(midi.deck_state["A"]), "B": dict(midi.deck_state["B"])}
        state["A"]["duration_s"] = 300.0
        state["A"]["playposition"] = (plan["trigger_ms"] - 500) / 1000.0 / 300.0
        await qm.on_mixxx_state(state)
        assert not fired, "transition fired before the trigger point"
        state["A"]["playposition"] = (plan["trigger_ms"] + 200) / 1000.0 / 300.0
        await qm.on_mixxx_state(state)
        assert fired and fired[0], "transition did not fire at the trigger point"
        assert fired[0]["mix_out_ms"] == paced
        qm._begin_transition = real_begin
        print(f"[trigger] fires at {plan['trigger_ms'] / 1000:.1f}s for a "
              f"{plan['bars']}-bar blend ending at mix-out {paced / 1000:.1f}s — OK")

        qm._schedule_preload()
        await asyncio.wait_for(qm._preload_task, timeout=60)
        assert qm._preloaded["ready"], "preload did not finish"
        assert ("B", TRACK_B["id"]) in midi.loads, "next track not loaded early"
        assert not midi.deck_state["B"]["playing"], "prepped deck should be parked"
        bpm_err = abs(midi.deck_state["A"]["bpm"] - midi.deck_state["B"]["bpm"])
        assert bpm_err < 0.5, f"tempo not pre-matched (err {bpm_err:.2f} BPM)"
        entry = qm._entry_ms(nxt)
        pos_ms = midi._sim_pos_s["B"] * 1000
        assert entry - 8000 <= pos_ms <= entry + 2000, \
            f"deck B parked at {pos_ms:.0f}ms, entry point is {entry:.0f}ms"
        print(f"[preload] deck B loaded early, tempo err {bpm_err:.2f} BPM, "
              f"parked at {pos_ms / 1000:.1f}s for entry {entry / 1000:.1f}s — OK")

        performed = {}
        async def fake_perform(a, b, next_track, style, bars, chaos,
                               blend_start_ms=None, blend_method=None,
                               moves=None):
            performed.update(a=a, b=b, style=style, bars=bars,
                             blend_start_ms=blend_start_ms,
                             b_playing=midi.deck_state[b]["playing"])
            return {"style": style, "landed": True, "bailed": False, "max_drift": 0.0}
        qm.performer.perform = fake_perform

        beat_s = 60.0 / midi.deck_state["A"]["bpm"]
        offset = (midi._sim_pos_s["A"] - midi._sim_pos_s["B"]) % beat_s
        midi._sim_pos_s["B"] += offset
        midi.deck_state["B"]["playposition"] = \
            midi._sim_pos_s["B"] / midi.deck_state["B"]["duration_s"]

        await asyncio.wait_for(qm._begin_transition(), timeout=60)
        assert performed, "performer never ran"
        assert performed["b"] == "B" and performed["b_playing"], \
            "prepped deck was not playing when the move opened"
        lo, hi = blend_bars_range("party", qm.vibe)
        assert lo <= performed["bars"] <= hi, \
            f"blend bars {performed['bars']} outside party bounds {lo}-{hi}"
        assert qm.active_deck == "B", "deck roles did not swap"
        assert not midi.sync_calls, "prepped path should not have hit beatsync"
        print(f"[transition] prepped fast path, {performed['bars']}-bar blend, "
              f"no beatsync, decks swapped — OK")
    finally:
        tick.cancel()
        if qm._preload_task and not qm._preload_task.done():
            qm._preload_task.cancel()



def assert_spine(midi: HarnessMidi, style: str, bars: int, bar_s: float,
                 full_overlap: bool = True):
    xf = midi.xf_trace
    assert xf, f"{style}: crossfader never moved"
    vals = [v for _, v in xf]
    for prev, cur in zip(vals, vals[1:]):
        assert cur >= prev - 1e-6, f"{style}: crossfader moved backwards"
        assert cur - prev <= 0.15 + 1e-6, \
            f"{style}: crossfader jumped {cur - prev:.2f} in one step — hard cut"
    assert vals[-1] >= 0.99, f"{style}: crossfade never completed ({vals[-1]:.2f})"

    if full_overlap:
        build_bars = _phase_bars(bars)["build"]
        t_in = next(t for t, v in xf if v > 0.1)
        t_out = next(t for t, v in xf if v >= 0.9)
        assert t_out - t_in >= build_bars * bar_s * 0.5, \
            f"{style}: overlap only {t_out - t_in:.1f}s — decks barely coexisted"
        t_end = next(t for t, v in xf if v >= 0.54)
        t_mid = xf[0][0] + (t_end - xf[0][0]) / 2
        v_mid = max(v for t, v in xf if t <= t_mid)
        assert v_mid <= 0.32, \
            f"{style}: crossfader at {v_mid:.2f} halfway through the build — not eased"

    a_vol = midi.vol_trace["A"]
    assert a_vol and a_vol[-1][1] <= 0.05, \
        f"{style}: outgoing deck's channel not silent at the end"


async def run_spine_case(style: str, bars: int = 4,
                         bail_after_s: float | None = None) -> HarnessMidi:
    midi = HarnessMidi({TRACK_A["id"]: TRACK_A,
                        TRACK_B_MATCHED["id"]: TRACK_B_MATCHED})
    grid = BeatGrid(midi)
    perf = Performer(midi, grid)

    midi.load_track("A", TRACK_A["id"])
    midi.load_track("B", TRACK_B_MATCHED["id"])
    grid.set_track("A", TRACK_A["first_beat_ms"])
    grid.set_track("B", TRACK_B_MATCHED["first_beat_ms"])
    midi.play("A")

    bpm = midi.deck_state["A"]["bpm"]
    bar_s = (60.0 / bpm) * 4

    tick = asyncio.create_task(midi.tick_loop())
    bail_task = None
    if bail_after_s is not None:
        async def trip():
            await asyncio.sleep(bail_after_s)
            perf._bailed = True
        bail_task = asyncio.create_task(trip())
    try:
        outcome = await asyncio.wait_for(
            perf.perform("A", "B", dict(TRACK_B_MATCHED), style, bars, 0.3),
            timeout=180)
    finally:
        tick.cancel()
        if bail_task:
            bail_task.cancel()

    if bail_after_s is None:
        assert not outcome["bailed"], f"{style}: unexpectedly bailed in the harness"
    assert_spine(midi, style, bars, bar_s, full_overlap=bail_after_s is None)
    return midi


async def run_spine_checks():
    await run_spine_case("beatmatch_crossfade", bars=4)
    print("[spine] clean blend: monotonic ramped crossfade, real overlap, "
          "silent outgoing exit — OK")

    for style in FLAVORS:
        if style == "beatmatch_crossfade":
            continue
        await run_spine_case(style, bars=4)
    print(f"[flavors] all {len(FLAVORS)} styles hold the fluidity invariants — OK")

    midi = await run_spine_case("beatmatch_crossfade", bars=8, bail_after_s=3.0)
    bail_t = 3.0
    steps_after = [v for t, v in midi.xf_trace if t > bail_t]
    assert len(steps_after) >= 5, "bail exit was not ramped"
    print("[bail] recovery reflex exits through a ramp, never a slam — OK")


def main():
    check_math()
    asyncio.run(run_flow())
    asyncio.run(run_spine_checks())
    print("PASS")


if __name__ == "__main__":
    main()
