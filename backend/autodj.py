
import asyncio
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
from sqlmodel import Session, select

load_dotenv()

MUSIC_DIR = os.getenv("MUSIC_DIR", "./music")



def scan_library(music_dir: str) -> int:
    from database import create_db, engine
    from models import Track
    from analyzer import analyze_track, file_hash

    create_db()
    music_path = Path(music_dir)
    music_path.mkdir(parents=True, exist_ok=True)

    extensions = {".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a"}
    files = [f for f in music_path.rglob("*") if f.suffix.lower() in extensions]
    print(f"[scan] Found {len(files)} audio files in {music_path.resolve()}")

    with Session(engine) as session:
        existing_ids = {t.id for t in session.exec(select(Track)).all()}

    new_count = 0
    for f in files:
        if file_hash(str(f)) in existing_ids:
            continue
        try:
            track = analyze_track(str(f))
            label = f"{track.artist} - {track.title}  [{track.bpm:.1f} BPM | {track.key}]"
            with Session(engine) as session:
                session.add(track)
                session.commit()
            print(f"  + {label}")
            new_count += 1
        except Exception as e:
            print(f"  ! Failed {f.name}: {e}")

    with Session(engine) as session:
        stale = session.exec(select(Track).where(Track.first_beat_ms == None)).all()
    for old in stale:
        try:
            fresh = analyze_track(old.filepath)
            with Session(engine) as session:
                t = session.get(Track, old.id)
                t.first_beat_ms = fresh.first_beat_ms
                t.drop_ms = fresh.drop_ms
                session.add(t)
                session.commit()
            print(f"  ~ beat grid: {old.artist} - {old.title}")
        except Exception as e:
            print(f"  ! Beat-grid backfill failed {old.title}: {e}")

    total = len(existing_ids) + new_count
    print(f"[scan] Done - {new_count} new, {total} total tracks in library\n")
    return total



def build_library_index(midi) -> None:
    from database import engine
    from models import Track
    from mixxx_db import read_mixxx_id_map, _normalize

    mixxx_map = read_mixxx_id_map()

    by_name: dict[str, int | None] = {}
    for path, mixxx_id in mixxx_map.items():
        name = path.rsplit("/", 1)[-1]
        by_name[name] = None if name in by_name else mixxx_id

    with Session(engine) as session:
        tracks = session.exec(select(Track)).all()
        id_map = {}
        missing = []
        for t in tracks:
            norm = _normalize(t.filepath)
            mixxx_id = mixxx_map.get(norm)
            if mixxx_id is None:
                mixxx_id = by_name.get(norm.rsplit("/", 1)[-1])
            if mixxx_id is not None:
                id_map[t.id] = mixxx_id
            else:
                missing.append(f"{t.artist} - {t.title}")

    midi.set_mixxx_id_map(id_map)
    print(f"[midi] Mixxx id map: {len(id_map)}/{len(tracks)} tracks resolved")
    if missing:
        print(f"[midi] Not in Mixxx's library (import via Library -> Add folder, then rescan):")
        for name in missing[:10]:
            print(f"       - {name}")
        if len(missing) > 10:
            print(f"       ... and {len(missing) - 10} more")



async def terminal_loop(queue_mgr, loop: asyncio.AbstractEventLoop) -> None:
    if not sys.stdin.isatty():
        print("   (headless mode - send SIGINT to stop)\n")
        try:
            await asyncio.Event().wait()
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        return

    print("\nCommands: vibe [chill|hype|chaos]  |  pace [auto|club|party|full]  |  skip  |  "
          "status  |  plan  |  persona  |  cue [A|B|off]  |  review [on|off]  |  go  |  report  |  quit\n")

    def read_input():
        line = sys.stdin.readline()
        if line == "":
            return None
        return line.strip().lower()

    while True:
        try:
            line = await loop.run_in_executor(None, read_input)
        except (EOFError, KeyboardInterrupt):
            break

        if line is None:
            break

        if not line:
            if queue_mgr.now_playing:
                t = queue_mgr.now_playing
                bpm = t.get("bpm") or 0
                print(f"\n[now playing] {t.get('artist')} - {t.get('title')}  [{bpm:.1f} BPM | {t.get('key')}]")
                print(f"   vibe: {queue_mgr.vibe}  |  deck: {queue_mgr.active_deck}")
                for i, nxt in enumerate(queue_mgr.up_next[:3], 1):
                    print(f"   {i}. {nxt.get('title')}  [{nxt.get('transition_style')}]")
            else:
                print("   (not playing yet)")

        elif line.startswith("vibe"):
            parts = line.split()
            vibe = parts[1] if len(parts) > 1 else ""
            if vibe == "chaos":
                vibe = "chaotic"
            if vibe in ("chill", "hype", "chaotic"):
                queue_mgr.set_vibe(vibe)
                print(f"   vibe -> {vibe}")
            else:
                print("   usage: vibe chill | vibe hype | vibe chaos")

        elif line.startswith("pace"):
            parts = line.split()
            mode = parts[1] if len(parts) > 1 else ""
            if queue_mgr.set_pace(mode):
                print(f"   pace -> {mode}")
            else:
                print("   usage: pace auto | club | party | full")

        elif line in ("skip", "go"):
            if queue_mgr.review_pending:
                action = "skip" if line == "skip" else "go"
                queue_mgr.review_respond(action)
                print(f"   review -> {action}")
            elif line == "skip":
                print("   skipping...")
                asyncio.run_coroutine_threadsafe(queue_mgr.skip(), loop)
            else:
                print("   usage: 'go' only resolves a pending review - nothing pending")

        elif line.startswith("cue"):
            parts = line.split()
            target = parts[1] if len(parts) > 1 else ""
            deck = {"a": "A", "b": "B"}.get(target)
            queue_mgr.cue(deck)
            print(f"   headphone cue -> {deck or 'off'}")

        elif line.startswith("review"):
            parts = line.split()
            mode = parts[1] if len(parts) > 1 else ""
            if mode in ("on", "off"):
                queue_mgr.set_review_mode(mode == "on")
                print(f"   review mode -> {mode}")
            else:
                print("   usage: review on | review off")

        elif line == "status":
            if queue_mgr.now_playing:
                t = queue_mgr.now_playing
                bpm = t.get("bpm") or 0
                p = queue_mgr.personality.summary()
                print(f"\n[now playing] {t.get('artist')} - {t.get('title')}  [{bpm:.1f} BPM | {t.get('key')}]")
                print(f"   vibe: {queue_mgr.vibe}  |  pace: {queue_mgr.pace_mode}"
                      f"  |  active deck: {queue_mgr.active_deck}"
                      f"  |  chaos: {p['chaos']}  |  boredom: {p['boredom']}"
                      f"  |  review: {'on' if queue_mgr.review_mode else 'off'}")
                if queue_mgr.beatmatch:
                    locked = queue_mgr.beatmatch.is_locked(queue_mgr.inactive_deck)
                    quality = queue_mgr.beatmatch.lock_quality(queue_mgr.inactive_deck)
                    if quality is not None:
                        print(f"   beatmatch deck {queue_mgr.inactive_deck}: "
                              f"{'locked' if locked else 'matching...'}  (drift {quality:.3f} beats)")
                if queue_mgr.up_next:
                    print("   up next:")
                    for i, nxt in enumerate(queue_mgr.up_next[:3], 1):
                        print(f"     {i}. {nxt.get('artist')} - {nxt.get('title')}  [{nxt.get('transition_style')}]")
            else:
                print("   (not playing yet)")

        elif line == "plan":
            d = queue_mgr.director
            print(f"\n   set plan (track {d.tracks_played + 1}, phase: {d.current_phase().get('name')}):")
            for ph in d.plan.get("phases", []):
                print(f"     {ph['name']:<10} {ph.get('tracks', '?')} tracks  energy {ph.get('energy', '?')}  - {ph.get('note', '')}")
            for m in d.plan.get("moments", []):
                mark = "[done]" if m.get("done") else "[pending]"
                print(f"     {mark} moment after track {m.get('after_track')}: {m.get('move')} ({m.get('why', '')})")
            if d.went_off_script:
                print("   off-script:")
                for s in d.went_off_script:
                    print(f"     ! {s}")

        elif line == "persona":
            p = queue_mgr.personality.summary()
            per = p["persona"]
            print(f"\n   {per.get('name')} - {per.get('style')}")
            print(f'   "{per.get("catchphrase", "")}"')
            print(f"   chaos: {p['chaos']}  |  gambles landed: {p['gambles_landed']}  |  botched: {p['gambles_botched']}")

        elif line == "report":
            print("   writing set recap...")
            await queue_mgr.write_report()

        elif line in ("quit", "exit", "q"):
            print("   stopping...")
            break



async def main():
    print("=" * 60)
    print("  AutoDJ x Mixxx")
    print("=" * 60)

    total = scan_library(MUSIC_DIR)
    if total == 0:
        print(f"No tracks found in {MUSIC_DIR} - add music files and restart.")
        return

    from midi_controller import MidiController
    from queue_manager import QueueManager
    from database import engine

    midi = MidiController()
    midi.set_event_loop(asyncio.get_running_loop())
    try:
        midi.open()
    except RuntimeError as e:
        print(f"\n[midi] {e}")
        print("[midi] Make sure loopMIDI is running and Mixxx has the AutoDJ preset loaded.")
        return

    build_library_index(midi)

    queue_mgr = QueueManager(
        session_factory=lambda: Session(engine),
        midi=midi,
    )
    midi.set_state_callback(queue_mgr.on_mixxx_state)

    print("\n[autodj] Starting - Mixxx is the interface\n")
    loop = asyncio.get_running_loop()
    asyncio.create_task(queue_mgr.start())

    try:
        await terminal_loop(queue_mgr, loop)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[autodj] Shutting down...")
        try:
            await queue_mgr.write_report()
        except Exception as e:
            print(f"[report] Could not write set recap: {e}")
        midi.close()


if __name__ == "__main__":
    asyncio.run(main())
