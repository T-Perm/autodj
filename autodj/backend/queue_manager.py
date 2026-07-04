import asyncio
import random
from typing import Optional, Callable
from sqlmodel import select
from models import Track
from brain import decide_next, get_compatible_candidates
from mix_timeline import validate_timeline, pick_fallback_method, TimelineError
from beatgrid import BeatGrid, snap_phrase_ms, PHRASE_BEATS
from beatmatch_engine import BeatmatchEngine
from pacing import PACE_MODES, blend_bars_range, clamp_blend_bars, mix_out_at_ms
from performer import Performer, risk_score
from personality import Personality
from director import Director
from announcer import Announcer
from set_report import SetReport

LEAD_MS = 9000


class QueueManager:
    def __init__(self, session_factory: Callable, midi=None):
        self.session_factory = session_factory
        self.midi = midi

        self.now_playing: Optional[dict] = None
        self.up_next: list[dict] = []
        self.history: list[dict] = []
        self.vibe: str = "hype"
        self.is_running: bool = False

        self.active_deck: str = "A"
        self.inactive_deck: str = "B"
        self._transition_active: bool = False
        self._midi_feedback_seen: bool = False

        self.pace_mode: str = "auto"
        self._preload_task: Optional[asyncio.Task] = None
        self._preloaded: dict = {"id": None, "ready": False}

        self.review_mode: bool = False
        self.review_pending: bool = False
        self._review_event = asyncio.Event()
        self._review_action: Optional[str] = None

        self.grid = BeatGrid(midi) if midi else None
        self.beatmatch = BeatmatchEngine(midi, self.grid) if midi else None
        self.performer = Performer(midi, self.grid, self.beatmatch) if midi else None
        self.personality = Personality(self.vibe)
        self.director = Director()
        self.announcer = Announcer(midi) if midi else None
        self.report = SetReport()

    async def _broadcast(self, msg: dict):
        t = msg.get("type")
        if t == "track_changed":
            track = msg.get("track", {})
            bpm = track.get("bpm") or 0
            print(f"\n[now playing] {track.get('artist')} - {track.get('title')}  [{bpm:.1f} BPM | {track.get('key')} | {track.get('mood')}]")
        elif t == "transition_start":
            nxt = msg.get("next_track", {})
            flag = "  [OFF-SCRIPT]" if msg.get("sabotaged") else ""
            print(f"   [mix] {msg.get('style')} ({msg.get('duration_bars')} bars, risk {msg.get('risk', 0):.2f}){flag} -> {nxt.get('title')}")
        elif t == "brain_reasoning":
            print(f"   [brain] {msg.get('text')}")
        elif t == "queue_update":
            queue = msg.get("queue", [])
            if queue:
                titles = "  ->  ".join(f"{t.get('title')}" for t in queue[:3])
                print(f"   up next: {titles}")


    def _get_all_tracks(self) -> list[dict]:
        with self.session_factory() as session:
            tracks = session.exec(select(Track)).all()
            return [t.model_dump(mode="json") for t in tracks]


    async def _pick_next(self) -> Optional[dict]:
        all_tracks = self._get_all_tracks()
        if not all_tracks:
            return None

        used_ids = {t["id"] for t in self.history[-10:]} | {t["id"] for t in self.up_next}
        if self.now_playing:
            used_ids.add(self.now_playing["id"])
        pool = [t for t in all_tracks if t["id"] not in used_ids] or all_tracks

        if self.now_playing:
            candidates = get_compatible_candidates(pool, self.now_playing.get("key", "1A"))
            if not candidates:
                candidates = pool
            try:
                result = await decide_next(
                    current_track=self.now_playing,
                    history=self.history,
                    candidates=candidates,
                    vibe=self.vibe,
                    directive=self.director.directive_text(),
                    blend_range=blend_bars_range(self.pace_mode, self.vibe),
                )
                track_id = str(result.get("next_track_id") or "").strip("[]'\" ")
                chosen = next((t for t in candidates if t["id"] == track_id), None)
                if not chosen and len(track_id) >= 8:
                    prefix_hits = [t for t in candidates if t["id"].startswith(track_id)]
                    if len(prefix_hits) == 1:
                        chosen = prefix_hits[0]
                if chosen:
                    chosen = dict(chosen)
                    chosen["transition_style"]         = result.get("transition_style", "beatmatch_crossfade")
                    chosen["transition_duration_bars"] = result.get("transition_duration_bars", 8)
                    chosen["entry_point"]              = result.get("entry_point", "intro")
                    chosen["reasoning"]                = result.get("reasoning", "")
                    try:
                        chosen["moves"] = validate_timeline(
                            result.get("blend_method"), result.get("moves"),
                            chosen["transition_duration_bars"])
                        chosen["blend_method"] = result["blend_method"]
                    except (TimelineError, KeyError):
                        chosen["blend_method"] = pick_fallback_method(self.now_playing, chosen)
                        chosen["moves"] = []
                    return chosen
                else:
                    print(f"[Brain] LLM picked unknown id {track_id!r}, falling back to rule-based pick")
            except Exception as e:
                import traceback
                print(f"[Brain] LLM error ({type(e).__name__}): {e}")
                traceback.print_exc()

        fallback_pool = candidates if self.now_playing else pool
        chosen = dict(self._pick_fallback_track(fallback_pool))
        chosen["transition_style"]         = "beatmatch_crossfade"
        chosen["transition_duration_bars"] = clamp_blend_bars(self.pace_mode, self.vibe, 8)
        chosen["entry_point"]              = "intro"
        chosen["reasoning"]                = ""
        chosen["blend_method"] = (pick_fallback_method(self.now_playing, chosen)
                                  if self.now_playing else "crossfader")
        chosen["moves"] = []
        return chosen

    def _pick_fallback_track(self, options: list[dict]) -> dict:
        if not self.now_playing:
            return random.choice(options)

        current_bpm = self.now_playing.get("bpm") or 120.0
        tolerant = [t for t in options
                    if abs((t.get("bpm") or current_bpm) - current_bpm) <= current_bpm * 0.08]
        scored_pool = tolerant or options

        energy_target = self.director.get_directive()["energy_target"] if self.director else 0.6

        def score(t: dict) -> tuple[float, float]:
            bpm_delta = abs((t.get("bpm") or current_bpm) - current_bpm)
            energy_delta = abs((t.get("energy") or energy_target) - energy_target)
            return (bpm_delta, energy_delta)

        return min(scored_pool, key=score)


    async def _load_and_verify(self, deck: str, track: dict,
                               timeout_s: float = 3.0) -> bool:
        if not self.midi:
            return False
        if track["id"] not in self.midi.mixxx_id_map:
            print(f"[MIDI] {track.get('title')!r} not in Mixxx's library - cannot load. "
                  f"Import it in Mixxx (Library -> Add folder) and restart.")
            return False

        self.midi.deck_state[deck]["duration_s"] = 0.0
        self.midi.load_track(deck, track["id"])

        elapsed = 0.0
        while elapsed < timeout_s:
            await asyncio.sleep(0.2)
            elapsed += 0.2
            if self.midi.deck_state[deck]["duration_s"] > 0:
                if self.grid:
                    self.grid.set_track(deck, track.get("first_beat_ms"))
                if self.beatmatch:
                    self.beatmatch.reset_deck(deck)
                if self.performer:
                    self.performer.clear_hotcue_cache(deck)
                return True
        print(f"[MIDI] Deck {deck} did not report a loaded track within {timeout_s:.0f}s")
        return False


    async def start(self):
        self.is_running = True

        persona = await self.personality.invent_persona()
        print(f"\nTonight on the decks: {persona.get('name')} - {persona.get('style')}")
        await self.director.plan_set(self._get_all_tracks(), self.vibe)
        phases = " -> ".join(p["name"] for p in self.director.plan["phases"])
        print(f"   set plan: {phases}")
        if self.announcer:
            await self.announcer.prepare(persona)

        first = await self._pick_next()
        if not first:
            print("[QueueManager] No tracks in library")
            return

        self.now_playing = first
        self.report.track(first)
        await self._broadcast({"type": "track_changed", "track": first})

        if self.midi:
            loaded = await self._load_and_verify(self.active_deck, first)
            if not loaded:
                print(f"[MIDI] Could not load first track onto Deck {self.active_deck}. "
                      f"Check that Mixxx (patched build) is running with the AutoDJ preset enabled.")
            self.midi.set_crossfader(0.0)
            self.midi.set_deck_volume("A", 1.0)
            self.midi.set_deck_volume("B", 1.0)
            if loaded:
                self.midi.enable_keylock(self.active_deck)
                self.midi.play(self.active_deck)
                if self.announcer:
                    await self.announcer.say("set_start", deck=self.active_deck, force=True)

        await self._fill_queue()
        self._schedule_preload()


    async def on_mixxx_state(self, deck_state: dict):
        if not self._midi_feedback_seen:
            self._midi_feedback_seen = True
            print("[MIDI] Receiving playposition from Mixxx (ok)")
        if self._transition_active or not self.is_running or not self.now_playing:
            return
        if not self.up_next:
            return

        active = self.active_deck
        pos = deck_state[active]["playposition"]
        dur = deck_state[active]["duration_s"]

        if dur <= 0:
            return

        current_ms = pos * dur * 1000
        plan = self._blend_plan(dur)
        if plan and current_ms >= plan["trigger_ms"]:
            await self._begin_transition(plan)

    def _blend_plan(self, dur_s: float) -> Optional[dict]:
        if not (self.now_playing and self.up_next):
            return None
        bars = clamp_blend_bars(self.pace_mode, self.vibe,
                                self.up_next[0].get("transition_duration_bars", 8))
        bpm = self.now_playing.get("bpm") or 120.0
        if self.midi:
            bpm = self.midi.deck_state[self.active_deck]["bpm"] or bpm
        bar_ms = (60000.0 / bpm) * 4
        mix_out = self._mix_out_for(self.now_playing, dur_s)
        blend_start = mix_out - bars * bar_ms
        return {
            "bars": bars,
            "blend_start_ms": blend_start,
            "trigger_ms": blend_start - LEAD_MS,
            "mix_out_ms": mix_out,
        }

    def _mix_out_for(self, track: dict, dur_s: float) -> float:
        cached = track.get("_pace_mix_out_ms")
        if cached is not None:
            return cached
        energy = self.director.get_directive().get("energy_target", 0.6)
        paced = mix_out_at_ms(self.pace_mode, self.vibe, energy, track)
        if paced is None:
            paced = track.get("mix_out_ms") or max((dur_s - 30) * 1000, 0)
        track["_pace_mix_out_ms"] = paced
        return paced

    def _entry_ms(self, track: dict) -> float:
        bpm = track.get("bpm") or 120.0
        phrase_ms = PHRASE_BEATS * 60000.0 / bpm
        ep = track.get("entry_point", "intro")
        drop = track.get("drop_ms")
        if ep in ("breakdown", "drop") and drop:
            lead = 2 if ep == "breakdown" else 1
            target = max(0.0, drop - lead * phrase_ms)
            return snap_phrase_ms(track.get("first_beat_ms"), bpm, target, mode="floor")
        target = float(track.get("mix_in_ms") or 0)
        return snap_phrase_ms(track.get("first_beat_ms"), bpm, target, mode="nearest")


    def _schedule_preload(self):
        if not self.midi or not self.beatmatch:
            return
        if self._preload_task and not self._preload_task.done():
            return
        self._preload_task = asyncio.create_task(self._preload_next())

    async def _preload_next(self):
        await asyncio.sleep(4.0)
        if not self.is_running or self._transition_active or not self.up_next:
            return
        nxt = self.up_next[0]
        if self._preloaded.get("id") == nxt["id"] and self._preloaded.get("ready"):
            return
        self._preloaded = {"id": nxt["id"], "ready": False}
        deck = self.inactive_deck
        if not await self._load_and_verify(deck, nxt):
            self._preloaded = {"id": None, "ready": False}
            return
        await self.beatmatch.start_match(
            deck, self.active_deck, timeout_s=20.0, tempo_only=True)
        entry = self._entry_ms(nxt)
        if entry > 0 and self.performer:
            bpm = self.midi.deck_state[self.active_deck]["bpm"] or 120.0
            await self.performer._preroll_to(deck, entry, lead_bars=0.5,
                                             bar_s=(60.0 / bpm) * 4)
        self.midi.pause(deck)
        self._preloaded = {"id": nxt["id"], "ready": True}
        print(f"   [preload] prepped on deck {deck}: {nxt.get('title')!r} "
              f"(tempo matched, cued at {self._entry_ms(nxt) / 1000:.0f}s)")


    async def _begin_transition(self, plan: Optional[dict] = None):
        if self._transition_active:
            return
        self._transition_active = True

        next_track = self.up_next[0] if self.up_next else None
        if not next_track:
            self._transition_active = False
            return

        style = next_track.get("transition_style", "beatmatch_crossfade")
        bars  = plan["bars"] if plan else clamp_blend_bars(
            self.pace_mode, self.vibe,
            next_track.get("transition_duration_bars", 8))

        sabotaged = False
        moment = self.director.due_moment()
        if moment:
            style = moment.get("move", style)
            if self.announcer:
                await self.announcer.say("moment", deck=self.active_deck)
        else:
            style, sabotaged = self.personality.maybe_sabotage(style)

        risk = risk_score(self.now_playing or {}, next_track, style)

        await self._broadcast({
            "type":        "transition_start",
            "style":       style,
            "duration_bars": bars,
            "next_track":  next_track,
            "risk":        risk,
            "sabotaged":   sabotaged,
            "reasoning":   next_track.get("reasoning", ""),
        })
        if next_track.get("reasoning"):
            await self._broadcast({"type": "brain_reasoning", "text": next_track["reasoning"]})

        if self.review_mode:
            print(f"   [review] {style} -> {next_track.get('title')!r} queued. "
                  f"Type 'go' to commit or 'skip' to veto.")
            self._review_event.clear()
            self.review_pending = True
            await self._review_event.wait()
            self.review_pending = False
            if self._review_action == "skip":
                print("   [review] vetoed - staying on current track.")
                self._transition_active = False
                return

        outcome = {"style": style, "landed": True, "bailed": False, "max_drift": 0.0}
        if self.midi:
            if self._preload_task and not self._preload_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(self._preload_task), timeout=2.5)
                except asyncio.TimeoutError:
                    self._preload_task.cancel()
                except Exception:
                    pass

            prepped = (self._preloaded.get("ready")
                       and self._preloaded.get("id") == next_track["id"])
            if not prepped:
                loaded = await self._load_and_verify(self.inactive_deck, next_track)
                if not loaded:
                    print(f"[MIDI] Transition aborted - Deck {self.inactive_deck} "
                          f"did not load {next_track.get('title')!r}. Staying on current track.")
                    self._transition_active = False
                    return
                if self.beatmatch:
                    locked = await self.beatmatch.start_match(
                        self.inactive_deck, self.active_deck, timeout_s=10.0)
                    if not locked:
                        print(f"   [beatmatch] manual match fell back to sync on deck {self.inactive_deck}")
            elif self.beatmatch:
                locked = await self.beatmatch.rephase(
                    self.inactive_deck, self.active_deck, timeout_s=5.0)
                if not locked:
                    print(f"   [beatmatch] phase re-lock fell back to sync on deck {self.inactive_deck}")

            blend_start_ms = plan["blend_start_ms"] if plan else None
            if plan:
                st = self.midi.deck_state[self.active_deck]
                cur_ms = st["playposition"] * st["duration_s"] * 1000
                bar_ms = (60000.0 / (st["bpm"] or 120.0)) * 4
                remaining = plan["mix_out_ms"] - cur_ms
                if remaining < bars * bar_ms:
                    bars = max(2, int(remaining // bar_ms))
                    blend_start_ms = None
                    print(f"   [pacing] blend shortened to {bars} bars (late trigger)")

            outcome = await self.performer.perform(
                self.active_deck, self.inactive_deck, next_track,
                style, bars, self.personality.chaos,
                blend_start_ms=blend_start_ms,
                blend_method=next_track.get("blend_method"),
                moves=next_track.get("moves") or None,
            )
        else:
            await asyncio.sleep(0.1)

        outcome["risk"] = risk
        self.personality.record_outcome(outcome)
        self.report.transition(style, risk, outcome, sabotaged)
        if outcome.get("bailed"):
            print(f"   [recovery] bailed out of {style} (drift {outcome['max_drift']:.2f} beats)")
            if self.announcer:
                await self.announcer.say("gamble_loss", deck=self.inactive_deck)
        elif risk > 0.5 and self.announcer:
            await self.announcer.say("gamble_win", deck=self.inactive_deck)

        await self._complete_transition(next_track, moment, sabotaged, style)

    async def _complete_transition(self, next_track: dict, moment=None,
                                   sabotaged: bool = False, style: str = ""):
        if self.midi:
            self.midi.pause(self.active_deck)
            self.midi.reset_deck(self.active_deck)
            self.midi.set_deck_volume(self.active_deck, 1.0)
            new_cf = 0.0 if self.inactive_deck == "A" else 1.0
            self.midi.set_crossfader(new_cf)

        self.active_deck, self.inactive_deck = self.inactive_deck, self.active_deck

        if self.now_playing:
            self.history.append(self.now_playing)
        if self.up_next:
            self.up_next.pop(0)
        self.now_playing = next_track
        self._transition_active = False

        before = self.director.current_phase().get("name")
        self.director.advance(
            moment_used=moment.get("move") if moment else None,
            sabotaged=style if sabotaged else None,
        )
        after = self.director.current_phase().get("name")
        if self.announcer and after != before:
            print(f"   [phase] set phase -> {after}")
            if after == "peak":
                await self.announcer.say("phase_peak", deck=self.active_deck)
            elif after in ("cooldown", "outro"):
                await self.announcer.say("phase_cool", deck=self.active_deck)

        self.report.track(next_track)
        await self._broadcast({"type": "track_changed", "track": self.now_playing})
        await self._fill_queue()

        self._preloaded = {"id": None, "ready": False}
        self._schedule_preload()


    async def _fill_queue(self):
        while len(self.up_next) < 3:
            nxt = await self._pick_next()
            if nxt:
                self.up_next.append(nxt)
            else:
                break
        await self._broadcast({"type": "queue_update", "queue": self.up_next})


    async def skip(self):
        if self._transition_active:
            return
        if self.up_next:
            await self._begin_transition()

    async def manual_override(self, track_id: str):
        with self.session_factory() as session:
            track = session.get(Track, track_id)
            if track:
                track_dict = track.model_dump(mode="json")
                track_dict["transition_style"]         = "beatmatch_crossfade"
                track_dict["transition_duration_bars"] = 4
                track_dict["reasoning"]                = "Manual override"
                self.up_next.insert(0, track_dict)
                await self._broadcast({"type": "queue_update", "queue": self.up_next})

    def cue(self, deck: Optional[str]):
        if not self.midi:
            return
        for d in ("A", "B"):
            (self.midi.pfl_enable if d == deck else self.midi.pfl_disable)(d)

    def set_review_mode(self, on: bool):
        self.review_mode = on
        if not on:
            self._review_action = "go"
            self._review_event.set()

    def review_respond(self, action: str):
        if action not in ("go", "skip"):
            return
        self._review_action = action
        self._review_event.set()

    def set_vibe(self, vibe: str):
        if vibe in ("chill", "hype", "chaotic"):
            self.vibe = vibe
            self.personality.set_vibe(vibe)
            if self.pace_mode == "auto" and self.now_playing:
                self.now_playing.pop("_pace_mix_out_ms", None)

    def set_pace(self, mode: str) -> bool:
        if mode not in PACE_MODES:
            return False
        self.pace_mode = mode
        if self.now_playing:
            self.now_playing.pop("_pace_mix_out_ms", None)
        return True

    async def write_report(self):
        return await self.report.write(
            self.personality.persona, self.director.plan, self.personality.summary())
