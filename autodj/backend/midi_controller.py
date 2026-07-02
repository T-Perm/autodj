import asyncio
import time
from typing import Callable, Optional
import mido

MIDI_OUT_PORT = "AutoDJ_OUT"
MIDI_IN_PORT  = "AutoDJ_OUT"


class MidiController:
    def __init__(self):
        self._out: Optional[mido.ports.BaseOutput] = None
        self._in:  Optional[mido.ports.BaseInput]  = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._state_callback: Optional[Callable] = None
        self._manual_override: set[str] = set()
        self.mixxx_id_map: dict[str, int] = {}

        self._pos_buf = {"A": 0, "B": 0}
        self._bpm_buf = {"A": 0, "B": 0}
        self._dur_buf = {"A": 0, "B": 0}

        self.deck_state: dict = {
            "A": {"playposition": 0.0, "bpm": 120.0, "duration_s": 0.0, "playing": False, "pos_wall": 0.0},
            "B": {"playposition": 0.0, "bpm": 120.0, "duration_s": 0.0, "playing": False, "pos_wall": 0.0},
        }


    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def set_state_callback(self, fn: Callable):
        self._state_callback = fn

    def set_mixxx_id_map(self, id_map: dict[str, int]):
        self.mixxx_id_map = id_map

    def open(self):
        available_out = mido.get_output_names()
        available_in  = mido.get_input_names()

        out_name = next((n for n in available_out if MIDI_OUT_PORT in n), None)
        in_name  = next((n for n in available_in  if MIDI_IN_PORT  in n), None)

        if not out_name:
            raise RuntimeError(
                f"loopMIDI port '{MIDI_OUT_PORT}' not found. "
                "Open loopMIDI and create a port named 'AutoDJ_OUT'."
            )
        if not in_name:
            raise RuntimeError(
                f"loopMIDI port '{MIDI_IN_PORT}' not found. "
                "Make sure loopMIDI is running with 'AutoDJ_OUT' created."
            )

        self._out = mido.open_output(out_name)
        self._in  = mido.open_input(in_name, callback=self._on_midi_in)
        print(f"[MIDI] Connected — OUT: {out_name!r}, listening on: {in_name!r}")

    def close(self):
        if self._out:
            self._out.close()
        if self._in:
            self._in.close()


    def take_manual(self, key: str):
        self._manual_override.add(key)

    def release_manual(self, key: str):
        self._manual_override.discard(key)

    def is_manual(self, key: str) -> bool:
        return key in self._manual_override

    def _can_write(self, key: str) -> bool:
        return key not in self._manual_override


    def _cc(self, cc: int, value: float, key: str = ""):
        if key and not self._can_write(key):
            return
        if self._out is None:
            return
        raw = max(0, min(127, round(value * 127)))
        self._out.send(mido.Message("control_change", channel=0, control=cc, value=raw))

    def _note_on(self, note: int):
        if self._out is None:
            return
        self._out.send(mido.Message("note_on", channel=0, note=note, velocity=127))

    def _note_off(self, note: int):
        if self._out is None:
            return
        self._out.send(mido.Message("note_off", channel=0, note=note, velocity=0))


    def set_crossfader(self, value: float):
        self._cc(1, value, key="crossfader")

    def set_deck_volume(self, deck: str, value: float):
        cc = 2 if deck == "A" else 3
        self._cc(cc, value, key=f"vol_{deck}")

    def set_eq(self, deck: str, band: str, value: float):
        offset = {"hi": 0, "mid": 1, "lo": 2}[band]
        base   = 10 if deck == "A" else 20
        self._cc(base + offset, value, key=f"eq_{deck}_{band}")

    def set_filter(self, deck: str, value: float):
        cc = 30 if deck == "A" else 31
        self._cc(cc, value, key=f"filter_{deck}")

    def play(self, deck: str):
        note = 1 if deck == "A" else 2
        self._note_on(note)

    def pause(self, deck: str):
        note = 1 if deck == "A" else 2
        self._note_off(note)

    def enable_sync(self, deck: str):
        note = 10 if deck == "A" else 11
        self._note_on(note)

    def set_rate(self, deck: str, value: float):
        key = f"rate_{deck}"
        if not self._can_write(key):
            return
        if self._out is None:
            return
        msb_cc, lsb_cc = (6, 16) if deck == "A" else (7, 17)
        raw = max(0, min(16383, round(value * 16383)))
        self._out.send(mido.Message("control_change", channel=0, control=msb_cc, value=(raw >> 7) & 0x7F))
        self._out.send(mido.Message("control_change", channel=0, control=lsb_cc, value=raw & 0x7F))

    def rate_nudge(self, deck: str, direction: str, active: bool):
        base = {("A", "up"): 60, ("B", "up"): 61, ("A", "down"): 62, ("B", "down"): 63}
        note = base.get((deck, direction))
        if note is None:
            return
        self._note_on(note) if active else self._note_off(note)

    def enable_keylock(self, deck: str):
        note = 20 if deck == "A" else 21
        self._note_on(note)

    def seek(self, deck: str, position: float):
        cc = 50 if deck == "A" else 51
        self._cc(cc, position)

    def load_track(self, deck: str, track_id: str):
        if self._out is None:
            return
        mixxx_id = self.mixxx_id_map.get(track_id)
        if mixxx_id is None:
            print(f"[MIDI] Track {track_id!r} has no Mixxx id — import it into Mixxx's library first")
            return
        if not 0 < mixxx_id <= 0x3FFF:
            print(f"[MIDI] Mixxx id {mixxx_id} outside 14-bit range — cannot send")
            return

        note = 30 if deck == "A" else 31
        self._out.send(mido.Message("control_change", channel=0, control=45, value=(mixxx_id >> 7) & 0x7F))
        self._out.send(mido.Message("control_change", channel=0, control=46, value=mixxx_id & 0x7F))
        self._out.send(mido.Message("note_on", channel=0, note=note, velocity=127))
        print(f"[MIDI] Load deck {deck} ← Mixxx track id {mixxx_id} (track {track_id[:8]}…)")


    _LOOP_NOTES = {0.25: 0, 0.5: 1, 1: 2, 2: 3, 4: 4}

    def beatloop(self, deck: str, beats: float):
        offset = self._LOOP_NOTES.get(beats)
        if offset is None:
            return
        base = 40 if deck == "A" else 45
        self._note_on(base + offset)
        self._note_off(base + offset)

    def loop_exit(self, deck: str):
        note = 50 if deck == "A" else 51
        self._note_on(note)
        self._note_off(note)

    def hotcue_set(self, deck: str, n: int):
        if not 1 <= n <= 4:
            return
        base = 70 if deck == "A" else 80
        self._note_on(base + n - 1)
        self._note_off(base + n - 1)

    def hotcue_jump(self, deck: str, n: int):
        if not 1 <= n <= 4:
            return
        base = 74 if deck == "A" else 84
        self._note_on(base + n - 1)
        self._note_off(base + n - 1)

    def spinback(self, deck: str, release: bool = False):
        note = 90 if deck == "A" else 92
        self._note_off(note) if release else self._note_on(note)

    def brake(self, deck: str, release: bool = False):
        note = 91 if deck == "A" else 93
        self._note_off(note) if release else self._note_on(note)

    def fx_enable(self, unit: int, deck: str, on: bool = True):
        note = {(1, "A"): 55, (1, "B"): 56, (2, "A"): 57, (2, "B"): 58}.get((unit, deck))
        if note is None:
            return
        self._note_on(note) if on else self._note_off(note)

    def fx_super(self, unit: int, value: float):
        self._cc(32 if unit == 1 else 33, value)

    def fx_mix(self, unit: int, value: float):
        self._cc(34 if unit == 1 else 35, value)


    def pfl_enable(self, deck: str):
        note = 64 if deck == "A" else 65
        self._note_on(note)

    def pfl_disable(self, deck: str):
        note = 64 if deck == "A" else 65
        self._note_off(note)

    def set_head_mix(self, value: float):
        self._cc(8, value)

    def set_head_gain(self, value: float):
        self._cc(9, value)

    def set_head_split(self, on: bool):
        note = 66
        self._note_on(note) if on else self._note_off(note)

    def reset_deck(self, deck: str):
        self.set_eq(deck, "hi",  0.75)
        self.set_eq(deck, "mid", 0.75)
        self.set_eq(deck, "lo",  0.75)
        self.set_filter(deck, 0.5)
        self.fx_enable(1, deck, on=False)
        self.fx_enable(2, deck, on=False)


    def _on_midi_in(self, msg: mido.Message):
        if msg.channel == 1:
            self._decode_state(msg)
        elif msg.channel == 0:
            pass

    def _decode_state(self, msg: mido.Message):
        if msg.type == "control_change":
            cc, v = msg.control, msg.value
            if   cc == 60: self._pos_buf["A"] = v
            elif cc == 61:
                self.deck_state["A"]["playposition"] = ((self._pos_buf["A"] << 7) | v) / 16383.0
                self.deck_state["A"]["pos_wall"] = time.monotonic()
                self._fire_callback()
            elif cc == 62: self._pos_buf["B"] = v
            elif cc == 63:
                self.deck_state["B"]["playposition"] = ((self._pos_buf["B"] << 7) | v) / 16383.0
                self.deck_state["B"]["pos_wall"] = time.monotonic()
                self._fire_callback()
            elif cc == 70: self._bpm_buf["A"] = v
            elif cc == 71:
                self.deck_state["A"]["bpm"] = ((self._bpm_buf["A"] << 7) | v) / 10.0
            elif cc == 72: self._bpm_buf["B"] = v
            elif cc == 73:
                self.deck_state["B"]["bpm"] = ((self._bpm_buf["B"] << 7) | v) / 10.0
            elif cc == 74: self._dur_buf["A"] = v
            elif cc == 75:
                self.deck_state["A"]["duration_s"] = ((self._dur_buf["A"] << 7) | v) / 10.0
            elif cc == 76: self._dur_buf["B"] = v
            elif cc == 77:
                self.deck_state["B"]["duration_s"] = ((self._dur_buf["B"] << 7) | v) / 10.0

        elif msg.type == "note_on":
            if   msg.note == 1:  self.deck_state["A"]["playing"] = msg.velocity > 0
            elif msg.note == 2:  self.deck_state["B"]["playing"] = msg.velocity > 0
            elif msg.note == 30: self._fire_callback()
            elif msg.note == 31: self._fire_callback()

        elif msg.type == "note_off":
            if   msg.note == 1: self.deck_state["A"]["playing"] = False
            elif msg.note == 2: self.deck_state["B"]["playing"] = False

    def _fire_callback(self):
        if self._state_callback is None or self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._state_callback(self.deck_state),
                self._loop,
            )
        except Exception as e:
            print(f"[MIDI] Callback error: {e}")
