// AutoDJ_script.js — Mixxx QJSEngine controller script
// Runs inside Mixxx (requires the AutoDJ fork with [ChannelN],load_track_by_id).
//
// PORT AutoDJ_OUT (channel 1 in):  receives commands from Python
// PORT AutoDJ_IN  (channel 2 out): sends deck state back to Python
//
// This script is a pure telemetry relay plus a 14-bit decoder for track ids.
// It never touches [Library] and never triggers play — Python is the sole
// play authority, and track loading goes straight to the native
// load_track_by_id ControlObject.
//
// 14-bit encoding: value = (msb << 7) | lsb  (range 0–16383)

var AutoDJ = {};

// ── Internal state ──────────────────────────────────────────────────────────
AutoDJ._posTimer      = null;
AutoDJ._trackHighByte = 0;  // staged high 7 bits of the Mixxx track id
AutoDJ._trackLowByte  = 0;  // staged low 7 bits of the Mixxx track id

// ── Lifecycle ───────────────────────────────────────────────────────────────
AutoDJ.init = function(id, debugging) {
    // Play/pause state
    engine.makeConnection("[Channel1]", "play_indicator", AutoDJ.onDeckAPlay);
    engine.makeConnection("[Channel2]", "play_indicator", AutoDJ.onDeckBPlay);

    // BPM
    engine.makeConnection("[Channel1]", "bpm", AutoDJ.onDeckABpm);
    engine.makeConnection("[Channel2]", "bpm", AutoDJ.onDeckBBpm);

    // Duration — also used as "new track loaded" signal
    engine.makeConnection("[Channel1]", "duration", AutoDJ.onDeckADuration);
    engine.makeConnection("[Channel2]", "duration", AutoDJ.onDeckBDuration);

    // 100ms position polling timer
    AutoDJ._posTimer = engine.beginTimer(100, AutoDJ.reportPositions, false);

    print("[AutoDJ] Bridge initialised (load-by-id mode)");
};

AutoDJ.shutdown = function(id) {
    if (AutoDJ._posTimer !== null) {
        engine.stopTimer(AutoDJ._posTimer);
        AutoDJ._posTimer = null;
    }
    print("[AutoDJ] Bridge shut down");
};

// ── Position reporting (every 100ms) ────────────────────────────────────────
// Encodes playposition (0.0–1.0) as 14-bit across two consecutive CCs.
// Deck A → CC 60 (MSB), CC 61 (LSB)
// Deck B → CC 62 (MSB), CC 63 (LSB)
AutoDJ.reportPositions = function() {
    var posA = engine.getValue("[Channel1]", "playposition");
    if (posA < 0) posA = 0;
    var rawA = Math.round(posA * 16383);
    midi.sendShortMsg(0xB1, 60, (rawA >> 7) & 0x7F);
    midi.sendShortMsg(0xB1, 61, rawA & 0x7F);

    var posB = engine.getValue("[Channel2]", "playposition");
    if (posB < 0) posB = 0;
    var rawB = Math.round(posB * 16383);
    midi.sendShortMsg(0xB1, 62, (rawB >> 7) & 0x7F);
    midi.sendShortMsg(0xB1, 63, rawB & 0x7F);
};

// ── State callbacks → MIDI out (pure status echoes, no side effects) ────────
AutoDJ.onDeckAPlay = function(value, group, control) {
    midi.sendShortMsg(0x91, 1, value > 0.5 ? 127 : 0);
};

AutoDJ.onDeckBPlay = function(value, group, control) {
    midi.sendShortMsg(0x91, 2, value > 0.5 ? 127 : 0);
};

// BPM: encode as (bpm * 10) as 14-bit
AutoDJ.onDeckABpm = function(value, group, control) {
    var scaled = Math.min(Math.round(value * 10), 16383);
    midi.sendShortMsg(0xB1, 70, (scaled >> 7) & 0x7F);
    midi.sendShortMsg(0xB1, 71, scaled & 0x7F);
};

AutoDJ.onDeckBBpm = function(value, group, control) {
    var scaled = Math.min(Math.round(value * 10), 16383);
    midi.sendShortMsg(0xB1, 72, (scaled >> 7) & 0x7F);
    midi.sendShortMsg(0xB1, 73, scaled & 0x7F);
};

// Duration: encode as (seconds * 10) as 14-bit; also fires "track loaded" Note ON
AutoDJ.onDeckADuration = function(value, group, control) {
    var scaled = Math.min(Math.round(value * 10), 16383);
    midi.sendShortMsg(0xB1, 74, (scaled >> 7) & 0x7F);
    midi.sendShortMsg(0xB1, 75, scaled & 0x7F);
    midi.sendShortMsg(0x91, 30, 127);  // "track loaded on deck A" event
};

AutoDJ.onDeckBDuration = function(value, group, control) {
    var scaled = Math.min(Math.round(value * 10), 16383);
    midi.sendShortMsg(0xB1, 76, (scaled >> 7) & 0x7F);
    midi.sendShortMsg(0xB1, 77, scaled & 0x7F);
    midi.sendShortMsg(0x91, 31, 127);  // "track loaded on deck B" event
};

// ── Track loading ─────────────────────────────────────────────────────────────
// Python sends the Mixxx library track id as two CCs (CC 45 = high 7 bits,
// CC 46 = low 7 bits), then Note ON 30/31 commits it to Deck A/B. Both id
// bytes ride CCs so a low byte of 0 can't be mistaken for a Note OFF.
// The commit goes straight to the native load_track_by_id ControlObject —
// no library navigation, no play triggering.

AutoDJ.trackIdHighByte = function(channel, control, value, status) {
    AutoDJ._trackHighByte = value;
};

AutoDJ.trackIdLowByte = function(channel, control, value, status) {
    AutoDJ._trackLowByte = value;
};

AutoDJ._commitLoad = function(group) {
    var trackId = (AutoDJ._trackHighByte << 7) | AutoDJ._trackLowByte;
    AutoDJ._trackHighByte = 0;
    AutoDJ._trackLowByte = 0;
    if (trackId <= 0) {
        print("[AutoDJ] load commit with no track id staged — ignoring");
        return;
    }
    print("[AutoDJ] load_track_by_id " + trackId + " → " + group);
    engine.setValue(group, "load_track_by_id", trackId);
};

AutoDJ.loadToDeckA = function(channel, control, value, status) {
    if (value === 0) return;  // ignore Note OFF
    AutoDJ._commitLoad("[Channel1]");
};

AutoDJ.loadToDeckB = function(channel, control, value, status) {
    if (value === 0) return;
    AutoDJ._commitLoad("[Channel2]");
};

// ── Spinback / brake (vinyl physics) ─────────────────────────────────────────
// Note ON starts the effect, Note OFF releases it (deck resumes if playing).
// These use Mixxx's built-in scratch physics helpers; still no play authority
// here — the effect only shapes a deck that Python already started.

AutoDJ.spinbackA = function(channel, control, value, status) {
    engine.spinback(1, value > 0);
};

AutoDJ.spinbackB = function(channel, control, value, status) {
    engine.spinback(2, value > 0);
};

AutoDJ.brakeA = function(channel, control, value, status) {
    engine.brake(1, value > 0);
};

AutoDJ.brakeB = function(channel, control, value, status) {
    engine.brake(2, value > 0);
};
