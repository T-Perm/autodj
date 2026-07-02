# AutoDJ

An AI that *performs*. Drop your music library in, open Mixxx, run one command — it invents a DJ persona for the night, plans a set arc, takes risks in the mix (loop rolls, spinbacks, drop teases, double drops), talks over the decks, and writes an in-character review of its own set afterward. You can override anything from the terminal.

No cloud APIs. No browser UI. Just Python talking to Mixxx over MIDI — with one small patch to Mixxx itself that makes track loading exact instead of fragile.

---

## What it actually does

AutoDJ analyzes every song in your library (BPM, key, energy, mood) and builds a live queue using a local LLM. It picks the next track based on harmonic compatibility (Camelot wheel), current vibe, and what's been playing. And it mixes like a working DJ, not a jukebox: tracks play for 2–4 minutes (you control the rotation speed with `pace`), the next record is loaded, tempo-matched, and cued at its entry point minutes in advance, and blends open, bass-swap, and close on 32-beat phrase boundaries with both tracks audibly overlapping.

Mixxx does the audio. AutoDJ drives it blind via MIDI.

Every transition runs the same *blend spine* — the incoming track enters as a whisper (bass-cut, mids and highs closed) while the outgoing is still in its body, and *builds* continuously on an eased curve until the fast bass trade on the 1, with the crossfade completing right at the outgoing track's mix-out point. The handoff is one rising gesture instead of a stop-and-start. Twelve styles color that spine as flavors: beatmatch crossfade, filter sweep, echo out, drum roll, riser, stutter cut, vinyl scratch, silence drop, reverse crash, acapella swap, drop tease, double drop. The AI picks the one that fits the genre and vibe — and sometimes overrides its own plan. None of them can break the overlap: the crossfader belongs to the spine and only ever moves in ramps.

### The performance stack

- **Pacing engine** — decides how long each track actually plays (a real DJ never runs whole files) and how many bars the blends overlap, snapped to the phrase grid. Runtime-selectable: `pace auto` adapts to vibe and set phase, `club` is ~3 min with long blends, `party` is ~2 min with punchy cuts, `full` restores play-to-the-end behavior.
- **Director** — at session start the LLM plans a set arc (warmup → build → peak → cooldown → encore) with 2–3 scheduled showpiece "moments", and tracks progress against it live.
- **Personality** — a chaos meter (seeded by vibe, moved by how gambles land) and a boredom accumulator. Too many safe transitions and it *sabotages the plan* with a riskier move — flagged `⚠ OFF-SCRIPT` in the terminal.
- **Performer** — the blend spine plus flavor layers, timed by a Python-side beat grid (librosa stores each track's first-beat offset; positions are dead-reckoned between MIDI polls). A **recovery reflex** watches beat drift during every transition and bails early if it's going off the rails — but even a bail exits through a fast ramp, never a slam. Its tolerance widens with chaos and it switches off entirely in `vibe chaos`.
- **Beatmatch engine** — the next record is prepped *minutes* before the transition, the way a real DJ works the headphones: as soon as it's picked it loads onto the standby deck, a control loop rides the pitch fader to the live tempo (no auto-sync button), and the deck parks paused at its entry point. At transition time only a quick phase re-nudge remains, so the handoff starts instantly. `beatsync` only fires as an emergency fallback.
- **Headphone cue** — Mixxx's native PFL/headphone-mix controls are mapped so you can put on headphones, audition either deck in isolation (`cue A` / `cue B`), and optionally gate every transition on your own approval (`review on`) instead of letting it run fully autonomous.
- **Announcer** — the LLM writes drop lines in the persona's voice, offline TTS renders them once to `backend/tags/`, and they play over a ducked mix at set start, phase changes, and after landed (or botched) gambles.
- **Set report** — on quit, the DJ writes an in-character recap of the night — tracklist, risks, botches, self-assigned grade — to `sets/`.

### Why a patched Mixxx?

Stock Mixxx's controller API can only load "whatever row is selected in the library view" — so the old AutoDJ had to scroll the library by row index and hope Mixxx's sort order matched. When it didn't, the wrong song loaded. The fork adds one native control, `[ChannelN],load_track_by_id`, that loads an exact track by Mixxx's internal database id. Wrong-track loads are now structurally impossible, and loading never auto-starts playback — Python is the sole play authority. See `BUILDING_MIXXX.md`.

---

## Setup

### Prerequisites

- **Patched Mixxx fork** — built from `github.com/T-Perm/mixxx` branch `autodj-patch`; see `BUILDING_MIXXX.md` (one-time, mostly automated)
- [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) — virtual MIDI cable for Windows
- [Ollama](https://ollama.com) — runs the LLM locally
- Python 3.11+

### One-time setup

**1. Build the patched Mixxx** (see `BUILDING_MIXXX.md`).

**2. Create two virtual MIDI ports in loopMIDI:**

- `AutoDJ_OUT`
- `AutoDJ_IN`

**3. Install Ollama and pull a model:**

```
winget install Ollama.Ollama
ollama pull llama3.1
```

**4. Install Python dependencies:**

```
cd backend
pip install -r requirements.txt
```

**5. Load the Mixxx preset:**

Copy `mixxx/AutoDJ.midi.xml` and `mixxx/AutoDJ_script.js` to:
```
%LOCALAPPDATA%\Mixxx\controllers\
```

Open Mixxx → Preferences → Controllers → enable **AutoDJ Virtual Bridge**
- Input: `AutoDJ_OUT`
- Output: `AutoDJ_IN`

**5b. One-time FX rack setup (for echo-out / riser / reverse-crash moves):**

In Mixxx's FX section, load **Echo** into Effect Unit 1's first slot and
**Reverb** into Effect Unit 2's first slot. AutoDJ drives the units' enable
switches and metaknobs over MIDI but can't choose which effect is loaded —
Mixxx remembers this between sessions, so it's a one-time step. If you skip it,
those moves still work; they just apply whatever effect is loaded (or none).

**6. Import your music into Mixxx's library:**

In Mixxx: Library → Add folder → select your music folder. This gives every
track a Mixxx database id, which is how AutoDJ addresses tracks. No crate,
no sorting requirements — sort the library however you like.

**7. Drop music in `backend/music/`** (or point `MUSIC_DIR` in `.env` somewhere else)

---

## Running it

```
start.bat
```

This launches the patched Mixxx (if not already running), then the backend. Or manually:

```
cd backend
python autodj.py
```

AutoDJ scans your library, matches it against Mixxx's library, connects over MIDI, loads the first track to Deck A, and starts playing. Everything from there is automatic.

---

## Controls

Type in the terminal while it's running:

| Command | What it does |
|---|---|
| `vibe chill` | Smooth blends, long transitions, harmonic keys — safety net always on |
| `vibe hype` | Progressive energy builds, drum rolls and risers at peaks |
| `vibe chaos` | Anything goes — and the recovery reflex is **disabled**: trainwrecks are part of the show |
| `pace auto` / `club` / `party` / `full` | Rotation speed: `auto` adapts to vibe + set phase (default), `club` ≈ 3 min/track with 16–32-bar blends, `party` ≈ 2 min with punchy 4–8-bar blends, `full` plays tracks to their analyzed end |
| `skip` | Force a transition to the next queued track right now (or veto a pending reviewed transition — see `review`) |
| `status` (or just Enter) | Now playing, vibe, pace, chaos/boredom meters, review mode, live beatmatch lock status, upcoming queue |
| `plan` | Tonight's set arc, scheduled moments, and where the DJ went off-script |
| `persona` | Who is DJing tonight, plus the gamble scoreboard |
| `cue A` / `cue B` / `cue off` | Route a deck to the headphone/PFL output for isolated monitoring (needs headphones on the audio interface's cue output) — doesn't affect the master mix |
| `review on` / `review off` | When on, pauses before each transition and waits for `go`/`skip` instead of running fully autonomous |
| `go` | Approve a transition that's paused waiting for review |
| `report` | Write the in-character set recap now |
| `quit` | Stop cleanly (writes the set recap) |

---

## How it works

### Audio analysis

When AutoDJ scans your library, it runs every track through librosa:

- **BPM** — beat tracking
- **Key** — chroma analysis mapped to Camelot notation (1A–12B)
- **Energy** — RMS energy normalized to track peak
- **Mood** — derived from energy + BPM + spectral centroid (hype / euphoric / neutral / melancholic / dark)
- **Mix points** — finds where the intro ends and the outro starts from the energy envelope, so transitions fire at the right moment instead of some arbitrary percentage

Results are cached in SQLite so rescanning is instant for tracks you've already analyzed.

### Track selection

Every time a track loads, AutoDJ sends the current track, last 5 plays, and a list of harmonically compatible candidates (Camelot wheel neighbors) to a local Llama 3.1 instance. The LLM picks the next track and transition style. If Ollama is offline or the response is malformed, it falls back to a random pick from the compatible pool — no crash.

### MIDI bridge

At startup, AutoDJ reads Mixxx's own library database (read-only) and joins it against its own track DB by filepath, building a map from each track to Mixxx's integer track id. Loading a track sends that id over MIDI (two CCs for the 14-bit id, one Note to commit it to a deck); the Mixxx-side script hands it straight to the fork's native `load_track_by_id` control. The right track loads every time, regardless of what the library view shows.

The Mixxx controller script is a pure telemetry relay: it reports playposition, BPM, and track duration every 100ms as 14-bit CC pairs and never triggers playback itself. Python watches the stream and starts the blend early enough that the crossfade *completes* at the mix-out point — the incoming deck is layered in while the outgoing track is still in its body — driving crossfader/EQ/filter automation for the whole transition.

### Phrasing and pacing

Dance music is built in 32-beat (8-bar) phrases, and that's the grammar every real DJ mixes in. The Python-side beat grid tracks each deck's phrase position, and everything structural lands on the grid: blends open on a bar boundary, the bassline swap happens *on the 1* (fast, over ~1 bar — a slow mid-phrase bass fade is the amateur tell), and drop-timed flavors pre-roll the incoming track so its drop lands exactly on the swap.

Just as important is what a DJ *doesn't* play: whole files. The pacing engine (`pacing.py`) decides when to leave each track — measured from its mix-in point, snapped to its phrase grid, and bounded by the analyzer's detected outro. The LLM never controls this; it picks *which* track and *which* move, while pacing bounds *when* and *how long*. The brain also chooses each track's entry point — its intro, or two phrases before its drop so the build rides in under the outgoing track and the drop lands right at the handoff.

The whole transition is prepped ahead of time: the moment the next track is picked, it's loaded on the standby deck, tempo-matched, and parked at its entry point. The trigger fires blend-length bars (plus a few seconds of matching headroom) *before* the mix-out point, so there's no loading, no 10-second matching pause — the deck starts, gets a quick phase re-nudge, the blend opens while the outgoing track still has life in it, and the crossfade lands right as the track reaches its natural exit.

### Manual beatmatching

As soon as the incoming track is loaded onto the standby deck, `beatmatch_engine.py` cues it in and closes two control loops against live telemetry — no audio is analyzed, just the same beat-grid + MIDI feedback the rest of the stack already tracks:

1. **Tempo** — a homing search on the pitch fader (sent as a 14-bit CC pair, same technique real DJ controllers use) steps toward zero BPM error using Mixxx's live, pitch-adjusted `bpm` telemetry. It halves its step on every overshoot, so it converges without needing to know the controller's rate-range calibration.
2. **Phase** — short `rate_temp_up`/`rate_temp_down` nudge bursts (the digital equivalent of pushing/pulling a platter) walk the beat-grid drift between the two decks to zero.

The crossfader sits hard on the active deck's side the whole time, so the deck being matched stays inaudible in the master output while it's cued — the same way a DJ pre-listens before bringing a track in. If matching can't converge within its time budget, it falls back to a single `beatsync` press so a live set never stalls, logged loudly since that's a manual-match failure, not a stylistic choice.

---

## Project structure

```
autodj/
  backend/
    autodj.py           — entry point, terminal loop, Mixxx id-map join
    queue_manager.py    — orchestrator: wires director/personality/performer together
    performer.py        — blend spine + flavor layers + recovery reflex
    director.py         — LLM set-arc planner (phases + scheduled moments)
    personality.py      — chaos meter, boredom, sabotage rolls, session persona
    announcer.py        — TTS drop tags played over a ducked mix
    set_report.py       — session journal + in-character set recap
    beatgrid.py         — Python-side beat + 32-beat phrase clock (dead-reckoned between MIDI polls)
    pacing.py           — rotation speed: when to leave each track, how long blends overlap
    beatmatch_engine.py — manual tempo/phase matching (rate fader + nudge control loops)
    verify_beatmatch.py — offline convergence check for the beatmatch engine (no Mixxx needed)
    verify_phrasing.py  — offline check of phrase math, pacing, and the preload flow
    midi_controller.py  — MIDI I/O bridge to Mixxx
    mixxx_db.py         — read-only reader for Mixxx's library DB
    brain.py            — LLM track selection
    analyzer.py         — librosa audio analysis (BPM, key, energy, beat grid, drop)
    models.py           — Track schema (SQLModel)
    database.py         — SQLite setup
    music/              — drop audio files here
    tags/               — rendered TTS drop lines (auto-created, cached)
  sets/                — post-set recaps, one per session (auto-created)
  mixxx/
    AutoDJ.midi.xml    — Mixxx controller mapping
    AutoDJ_script.js   — Mixxx controller script (telemetry relay + id decoder)
  .env                 — OLLAMA_MODEL, MUSIC_DIR, DB_PATH
  start.bat            — one-click start (Mixxx + backend)
  build_mixxx.bat      — rebuild the patched Mixxx fork
  BUILDING_MIXXX.md    — fork build + maintenance guide
```

---

## Troubleshooting

**`AutoDJ_OUT not found`** — loopMIDI isn't running, or the port names don't match. Open loopMIDI and confirm `AutoDJ_OUT` and `AutoDJ_IN` exist.

**Mixxx not connecting** — start Mixxx first, load the AutoDJ preset, enable it in Preferences → Controllers, then run autodj.py. `start.bat` handles the ordering for you.

**"not in Mixxx's library — cannot load"** — that track hasn't been imported into Mixxx. In Mixxx: Library → Add folder (pointing at your music folder), let it scan, then restart autodj.py. AutoDJ matches by filepath, falling back to filename.

**LLM taking forever** — llama3.1 is 4.9GB and slow on CPU. Try `ollama pull llama3.2` (3B, much faster) and set `OLLAMA_MODEL=llama3.2` in `.env`. A slow LLM doesn't block playback — it just falls back to a random compatible pick while waiting.

**"manual match fell back to sync"** — the beatmatch engine couldn't converge tempo/phase within its time budget (very large BPM gaps, or a mistracked beat grid on one of the two tracks) and pressed `beatsync` once instead of stalling the transition. Occasional fallbacks are harmless; if it happens on every transition, rescan the library (`Library → Add folder` in Mixxx, then restart autodj.py) to make sure beat-grid analysis isn't stale.
