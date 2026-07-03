
import asyncio
import time
from analyzer import CAMELOT_COMPATIBLE

MOVE_RISK = {
    "beatmatch_crossfade": 0.05,
    "filter_sweep":        0.15,
    "echo_out":            0.25,
    "acapella_swap":       0.35,
    "riser":               0.35,
    "drum_roll":           0.45,
    "silence_drop":        0.55,
    "stutter_cut":         0.55,
    "vinyl_scratch":       0.60,
    "reverse_crash":       0.60,
    "drop_tease":          0.70,
    "double_drop":         0.85,
}


def risk_score(current: dict, nxt: dict, style: str) -> float:
    risk = MOVE_RISK.get(style, 0.3)
    bpm_a = current.get("bpm") or 120.0
    bpm_b = nxt.get("bpm") or 120.0
    risk += min(abs(bpm_a - bpm_b) / bpm_a, 0.3)
    if nxt.get("key") not in CAMELOT_COMPATIBLE.get(current.get("key", "1A"), []):
        risk += 0.2
    return min(risk, 1.0)


_METHOD_CURVE = {
    "crossfader":   {"peak": 0.55, "power": 1.6, "parked": False},
    "eq_swap":      {"peak": 0.50, "power": 1.0, "parked": True},
    "filter_blend": {"peak": 0.50, "power": 1.0, "parked": True},
}
DEFAULT_BLEND_METHOD = "crossfader"


def _phase_bars(bars: int) -> dict:
    close = max(1, bars // 4)
    build = max(2, bars - 1 - close)
    tail = min(2, max(1, bars // 4))
    return {"build": build, "swap": 1, "close": close, "tail": tail}


class FlavorCtx:

    def __init__(self, perf, a: str, b: str, nxt: dict,
                 bar_s: float, bars: int, chaos: float, phases: dict):
        self._perf = perf
        m = perf.midi
        self.a, self.b, self.nxt = a, b, nxt
        self.bar_s, self.bars, self.chaos = bar_s, bars, chaos
        self.phases = phases
        self.grid = perf.grid
        self.set_eq = m.set_eq
        self.set_filter = m.set_filter
        self.set_volume = m.set_deck_volume
        self.fx_super = m.fx_super
        self.fx_mix = m.fx_mix
        self.fx_enable = m.fx_enable
        self.beatloop = m.beatloop
        self.loop_exit = m.loop_exit
        self.spinback = m.spinback
        self.brake = m.brake
        self.pause = m.pause

    @property
    def bailed(self) -> bool:
        return self._perf._bailed

    @property
    def lead_to_swap(self) -> int:
        return self.phases["build"]

    async def sleep(self, seconds: float) -> bool:
        return await self._perf._sleep(seconds)

    async def ramp(self, duration_s: float, fn) -> bool:
        return await self._perf._ramp(duration_s, fn)

    async def preroll(self, target_ms: int, lead_bars: float) -> bool:
        ok = await self._perf._preroll_to(self.b, target_ms, lead_bars, self.bar_s)
        if ok:
            self._perf.midi.set_deck_volume(self.b, 1.0)
        return ok


class Flavor:

    handles_swap = False
    builds_body = True
    blend_method = "crossfader"

    async def on_prepare(self, ctx: FlavorCtx):
        pass

    async def on_enter(self, ctx: FlavorCtx):
        pass

    def during_ride(self, ctx: FlavorCtx, frac: float):
        pass

    async def on_swap(self, ctx: FlavorCtx):
        pass

    async def on_tail(self, ctx: FlavorCtx) -> bool:
        return False

    def cancel_pending(self):
        pass



class _FilterSweep(Flavor):
    async def on_enter(self, ctx):
        ctx.set_filter(ctx.b, 0.70)

    def during_ride(self, ctx, frac):
        ctx.set_filter(ctx.a, 0.5 - 0.35 * frac)
        ctx.set_filter(ctx.b, 0.70 - 0.20 * frac)

    async def on_tail(self, ctx):
        ctx.set_filter(ctx.a, 0.5)
        return False


class _EchoOut(Flavor):
    async def on_enter(self, ctx):
        ctx.fx_super(1, 0.6)
        ctx.fx_mix(1, 0.0)
        ctx.fx_enable(1, ctx.a, on=True)

    def during_ride(self, ctx, frac):
        if frac > 0.5:
            ctx.fx_mix(1, (frac - 0.5) * 2 * 0.9)

    async def on_tail(self, ctx):
        tail_s = ctx.phases["tail"] * ctx.bar_s
        await ctx.ramp(tail_s, lambda f: ctx.set_volume(ctx.a, 1.0 - f))
        ctx.fx_mix(1, 0.0)
        return True


class _Riser(Flavor):
    async def on_enter(self, ctx):
        ctx.fx_super(2, 0.7)
        ctx.fx_mix(2, 0.0)
        ctx.fx_enable(2, ctx.a, on=True)

    def during_ride(self, ctx, frac):
        ctx.set_filter(ctx.a, 0.5 + 0.42 * frac)
        ctx.fx_mix(2, 0.8 * frac)

    async def on_swap(self, ctx):
        ctx.fx_mix(2, 0.0)
        ctx.fx_enable(2, ctx.a, on=False)
        ctx.set_filter(ctx.a, 0.5)


class _DrumRoll(Flavor):
    async def on_swap(self, ctx):
        a, bar_s = ctx.a, ctx.bar_s
        for beats, filt, hold in ((1, 0.55, bar_s / 2),
                                  (0.5, 0.62, bar_s / 4),
                                  (0.25, 0.72, bar_s / 4)):
            if ctx.bailed:
                break
            ctx.beatloop(a, beats)
            ctx.set_filter(a, filt)
            await ctx.sleep(hold)
        ctx.loop_exit(a)
        ctx.set_filter(a, 0.5)


class _StutterCut(Flavor):
    async def on_swap(self, ctx):
        sixteenth = ctx.bar_s / 16
        for i in range(16):
            if ctx.bailed:
                break
            ctx.set_volume(ctx.a, 0.0 if i % 2 else 1.0)
            await asyncio.sleep(sixteenth)
        ctx.set_volume(ctx.a, 1.0)


class _VinylScratch(Flavor):
    async def on_tail(self, ctx):
        ctx.spinback(ctx.a)
        await ctx.sleep(1.2)
        ctx.set_volume(ctx.a, 0.0)
        ctx.spinback(ctx.a, release=True)
        return True


class _ReverseCrash(Flavor):
    async def on_enter(self, ctx):
        ctx.fx_super(2, 0.8)
        ctx.fx_mix(2, 0.0)
        ctx.fx_enable(2, ctx.a, on=True)

    def during_ride(self, ctx, frac):
        if frac > 0.75:
            ctx.fx_mix(2, (frac - 0.75) * 4 * 0.7)

    async def on_tail(self, ctx):
        ctx.brake(ctx.a)
        await ctx.sleep(max(ctx.bar_s, 1.5))
        ctx.set_volume(ctx.a, 0.0)
        ctx.brake(ctx.a, release=True)
        return True


class _AcapellaSwap(Flavor):
    builds_body = False
    async def on_enter(self, ctx):
        ctx.set_eq(ctx.b, "hi", 0.0)
        ctx.set_eq(ctx.b, "mid", 0.3)

    def during_ride(self, ctx, frac):
        ctx.set_eq(ctx.a, "mid", 0.75 * (1 - 0.6 * frac))
        ctx.set_eq(ctx.b, "hi", 0.75 * frac)
        ctx.set_eq(ctx.b, "mid", 0.3 + 0.45 * frac)

    async def on_swap(self, ctx):
        ctx.set_eq(ctx.a, "mid", 0.0)


class _DropTimed(Flavor):
    def __init__(self):
        self._target = 0

    async def on_prepare(self, ctx):
        self._target = ctx.nxt.get("drop_ms") or 0
        if self._target:
            if await ctx.preroll(self._target, ctx.lead_to_swap):
                ctx.pause(ctx.b)
            else:
                self._target = 0

    async def on_enter(self, ctx):
        if self._target:
            await ctx.preroll(self._target, ctx.lead_to_swap)


class _SilenceDrop(_DropTimed):
    handles_swap = True

    async def on_swap(self, ctx):
        await ctx.ramp(ctx.bar_s / 2, lambda f: ctx.set_volume(ctx.a, 1.0 - f))
        await ctx.sleep(ctx.bar_s / 2)
        ctx.set_eq(ctx.b, "lo", 0.75)
        ctx.set_eq(ctx.a, "lo", 0.0)
        ctx.set_volume(ctx.a, 1.0)


class _DropTease(_DropTimed):
    builds_body = False
    async def on_enter(self, ctx):
        await super().on_enter(ctx)
        if self._target:
            ctx.set_filter(ctx.b, 0.85)

    def during_ride(self, ctx, frac):
        if self._target:
            ctx.set_filter(ctx.b, 0.85 - 0.35 * frac)

    async def on_swap(self, ctx):
        if self._target:
            ctx.set_filter(ctx.b, 0.5)


class _DoubleDrop(_DropTimed):
    builds_body = False
    async def on_enter(self, ctx):
        await super().on_enter(ctx)
        if self._target:
            ctx.set_volume(ctx.b, 0.6)

    def during_ride(self, ctx, frac):
        if self._target:
            ctx.set_volume(ctx.b, 0.6 + 0.4 * frac)

    async def on_swap(self, ctx):
        ctx.set_volume(ctx.b, 1.0)


_MOVE_ACTIONS = {
    "kill_bass":    lambda ctx, deck: ctx.set_eq(deck, "lo", 0.0),
    "bring_bass":   lambda ctx, deck: ctx.set_eq(deck, "lo", 0.75),
    "swap_mids":    lambda ctx, deck: ctx.set_eq(deck, "mid", 0.75),
    "swap_highs":   lambda ctx, deck: ctx.set_eq(deck, "hi", 0.75),
    "open_filter":  lambda ctx, deck: ctx.set_filter(deck, 0.5),
    "close_filter": lambda ctx, deck: ctx.set_filter(deck, 0.15),
    "fx_send":      lambda ctx, deck: (ctx.fx_enable(1, deck, on=True),
                                       ctx.fx_mix(1, 0.6)),
}


class _LLMDirectedFlavor(Flavor):

    def __init__(self, moves: list[dict]):
        self._moves = moves
        self._fired: set[int] = set()
        self._loop_tasks: list[asyncio.Task] = []

    def _fire_due(self, ctx: FlavorCtx, current_bar: float):
        for i, mv in enumerate(self._moves):
            if i in self._fired or mv["at_bar"] > current_bar:
                continue
            self._fired.add(i)
            deck = ctx.a if mv["deck"] == "a" else ctx.b
            if mv["move"] == "loop_extend":
                ctx.beatloop(deck, 1)
                self._loop_tasks.append(
                    asyncio.create_task(self._exit_loop_later(ctx, deck)))
            else:
                _MOVE_ACTIONS[mv["move"]](ctx, deck)

    async def _exit_loop_later(self, ctx: FlavorCtx, deck: str):
        await ctx.sleep(ctx.bar_s)
        ctx.loop_exit(deck)

    async def on_enter(self, ctx: FlavorCtx):
        self._fire_due(ctx, 0.0)

    def during_ride(self, ctx: FlavorCtx, frac: float):
        self._fire_due(ctx, frac * ctx.phases["build"])

    async def on_swap(self, ctx: FlavorCtx):
        self._fire_due(ctx, ctx.phases["build"] + 0.5)

    async def on_tail(self, ctx: FlavorCtx) -> bool:
        self._fire_due(ctx, ctx.bars)
        return False

    def cancel_pending(self):
        for t in self._loop_tasks:
            if not t.done():
                t.cancel()
        self._loop_tasks.clear()


FLAVORS: dict[str, type] = {
    "beatmatch_crossfade": Flavor,
    "filter_sweep":        _FilterSweep,
    "echo_out":            _EchoOut,
    "riser":               _Riser,
    "drum_roll":           _DrumRoll,
    "stutter_cut":         _StutterCut,
    "vinyl_scratch":       _VinylScratch,
    "reverse_crash":       _ReverseCrash,
    "acapella_swap":       _AcapellaSwap,
    "silence_drop":        _SilenceDrop,
    "drop_tease":          _DropTease,
    "double_drop":         _DoubleDrop,
}


class Performer:
    def __init__(self, midi, grid, beatmatch=None):
        self.midi = midi
        self.grid = grid
        self.beatmatch = beatmatch
        self._hotcue_cache: dict[tuple[str, int], int] = {}
        self._bailed = False
        self._max_drift = 0.0
        self._xf_frac = 0.0
        self._tail_done = False
        self._reflex = None
        self._monitor = None

    def clear_hotcue_cache(self, deck: str):
        self._hotcue_cache = {k: v for k, v in self._hotcue_cache.items() if k[0] != deck}


    async def perform(self, a: str, b: str, next_track: dict, style: str,
                      bars: int, chaos: float,
                      blend_start_ms: float | None = None,
                      blend_method: str | None = None,
                      moves: list[dict] | None = None) -> dict:
        m = self.midi
        bpm = m.deck_state[a]["bpm"] or 120.0
        bar_s = (60.0 / bpm) * 4

        self._bailed = False
        self._max_drift = 0.0
        self._xf_frac = 0.0
        self._tail_done = False
        reflex_on = chaos < 0.9
        threshold = 0.12 + 0.30 * chaos

        m.enable_keylock(b)
        m.set_deck_volume(b, 1.0)

        if moves:
            flavor = _LLMDirectedFlavor(moves)
        else:
            flavor = (FLAVORS.get(style) or Flavor)()
        method = blend_method if blend_method in _METHOD_CURVE else flavor.blend_method
        self._reflex = (a, b, threshold) if reflex_on else None
        self._monitor = None
        try:
            await self._run_spine(a, b, next_track, bars, bar_s, chaos,
                                  flavor, blend_start_ms, method)
        finally:
            if self._monitor:
                self._monitor.cancel()
            flavor.cancel_pending()
            await self._finish_ramp(a, b, bar_s)

        return {
            "style": style,
            "blend_method": method,
            "landed": not self._bailed,
            "bailed": self._bailed,
            "max_drift": round(self._max_drift, 3),
        }


    async def _run_spine(self, a: str, b: str, nxt: dict, bars: int,
                         bar_s: float, chaos: float, flavor: Flavor,
                         blend_start_ms: float | None,
                         blend_method: str = DEFAULT_BLEND_METHOD):
        m = self.midi
        phases = _phase_bars(bars)
        ctx = FlavorCtx(self, a, b, nxt, bar_s, bars, chaos, phases)
        curve = _METHOD_CURVE.get(blend_method, _METHOD_CURVE[DEFAULT_BLEND_METHOD])

        await flavor.on_prepare(ctx)
        await self._wait_until_ms(a, blend_start_ms)

        m.set_eq(b, "lo", 0.0)
        if flavor.builds_body:
            m.set_eq(b, "mid", 0.30)
            m.set_eq(b, "hi", 0.45)
        self._play_if_stopped(b)
        if curve["parked"]:
            self._xf(b, curve["peak"])
        if self._reflex:
            self._monitor = asyncio.create_task(self._watch_drift(*self._reflex))
        await flavor.on_enter(ctx)

        def build_step(frac):
            eased = frac ** curve["power"]
            if not curve["parked"]:
                self._xf(b, curve["peak"] * eased)
            if flavor.builds_body:
                m.set_eq(b, "mid", 0.30 + 0.45 * eased)
                m.set_eq(b, "hi", 0.45 + 0.30 * eased)
                if frac > 0.75:
                    m.set_eq(a, "hi", 0.75 - 0.20 * (frac - 0.75) * 4)
            flavor.during_ride(ctx, frac)
        if not await self._ramp(phases["build"] * bar_s, build_step):
            return

        await self.grid.wait_for_beat(a, subdivision=4)
        if flavor.handles_swap:
            await flavor.on_swap(ctx)
        else:
            async def trade():
                def fn(frac):
                    m.set_eq(b, "lo", 0.75 * frac)
                    m.set_eq(a, "lo", 0.75 * (1.0 - frac))
                if not await self._ramp(phases["swap"] * bar_s, fn):
                    m.set_eq(b, "lo", 0.75)
            await asyncio.gather(trade(), flavor.on_swap(ctx))
        if self._bailed:
            return

        if not await self._xf_ramp(b, curve["peak"], 1.0, phases["close"] * bar_s):
            return

        if not await flavor.on_tail(ctx):
            await self._ramp(phases["tail"] * bar_s,
                             lambda f: m.set_deck_volume(a, 1.0 - f),
                             ignore_bail=True)
        m.set_deck_volume(a, 0.0)
        self._tail_done = True

    async def _finish_ramp(self, a: str, b: str, bar_s: float):
        m = self.midi
        exit_s = max(bar_s, 1.0)
        if self._xf_frac < 0.99:
            start = self._xf_frac
            await self._ramp(exit_s,
                             lambda f: self._xf(b, start + (1.0 - start) * f),
                             ignore_bail=True)
        self._xf(b, 1.0)
        m.set_deck_volume(b, 1.0)
        m.set_filter(b, 0.5)
        if not self._tail_done:
            await self._ramp(exit_s, lambda f: m.set_deck_volume(a, 1.0 - f),
                             ignore_bail=True)
            self._tail_done = True
        m.fx_enable(1, a, on=False)
        m.fx_enable(2, a, on=False)


    async def _watch_drift(self, a: str, b: str, threshold: float):
        try:
            while True:
                await asyncio.sleep(0.25)
                st = self.midi.deck_state
                if not (st[a]["playing"] and st[b]["playing"]):
                    continue
                drift = self.grid.drift_beats(a, b)
                self._max_drift = max(self._max_drift, drift)
                if drift > threshold:
                    self._bailed = True
                    return
        except asyncio.CancelledError:
            pass

    async def _sleep(self, seconds: float) -> bool:
        step = 0.05
        waited = 0.0
        while waited < seconds:
            if self._bailed:
                return False
            await asyncio.sleep(min(step, seconds - waited))
            waited += step
        return not self._bailed

    async def _ramp(self, duration_s: float, fn, ignore_bail: bool = False) -> bool:
        steps = max(10, int(duration_s * 10))
        for i in range(steps + 1):
            if self._bailed and not ignore_bail:
                return False
            fn(i / steps)
            await asyncio.sleep(duration_s / steps)
        return True

    def _xf(self, b: str, frac: float):
        self._xf_frac = frac
        self.midi.set_crossfader(frac if b == "B" else 1.0 - frac)

    async def _xf_ramp(self, b: str, start: float, end: float,
                       duration_s: float) -> bool:
        return await self._ramp(duration_s,
                                lambda f: self._xf(b, start + (end - start) * f))

    def _play_if_stopped(self, b: str):
        if not self.midi.deck_state[b]["playing"]:
            self.midi.play(b)

    async def _wait_until_ms(self, a: str, target_ms: float | None):
        if target_ms is not None:
            while not self._bailed:
                remaining = target_ms / 1000.0 - self.grid.position_s(a)
                if remaining <= 0.05:
                    break
                await asyncio.sleep(min(remaining, 0.1))
        await self.grid.wait_for_beat(a, subdivision=4)


    async def _preroll_to(self, b: str, target_ms: int, lead_bars: float,
                          bar_s: float, timeout_s: float = 20.0) -> bool:
        m = self.midi
        dur_s = m.deck_state[b]["duration_s"]
        if dur_s <= 0 or target_ms <= 0:
            return False
        lead_ms = lead_bars * bar_s * 1000
        key = (b, round(target_ms / 100))

        cached_slot = self._hotcue_cache.get(key)
        if cached_slot:
            m.set_deck_volume(b, 0.0)
            m.hotcue_jump(b, cached_slot)
            m.play(b)
            return True

        start_ms = max(0.0, target_ms - lead_ms - 2500)
        m.set_deck_volume(b, 0.0)
        m.seek(b, min(start_ms / (dur_s * 1000), 0.99))
        m.play(b)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            pos_ms = self.grid.position_s(b) * 1000
            if pos_ms >= target_ms - lead_ms:
                slot = (len(self._hotcue_cache) % 4) + 1
                m.hotcue_set(b, slot)
                self._hotcue_cache[key] = slot
                return True
            await asyncio.sleep(0.05)
        return False
