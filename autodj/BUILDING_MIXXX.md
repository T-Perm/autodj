# Building the patched Mixxx fork

AutoDJ requires a patched Mixxx build that adds one native control,
`[ChannelN],load_track_by_id`, letting the controller script load an exact
track into a deck by Mixxx's internal library id (stock Mixxx can only load
"whatever row is selected in the library view", which is what caused the old
wrong-track bugs).

The fork lives at `github.com/T-Perm/mixxx`, branch `autodj-patch`, based on
release tag `2.5.6`. The patch is intentionally tiny (~40 lines in
`src/mixer/playermanager.h/.cpp`) to survive upstream syncs.

## One-time setup

Prerequisites (all free):

- **Visual Studio Build Tools 2022** with the "Desktop development with C++"
  workload (includes CMake + Ninja).
- **Git**.
- ~10 GB free disk (source + prebuilt deps + build tree).

```bat
git clone --filter=blob:none https://github.com/T-Perm/mixxx.git C:\dev\mixxx
cd C:\dev\mixxx
git remote add upstream https://github.com/mixxxdj/mixxx.git
git checkout autodj-patch
```

Download + unpack Mixxx's prebuilt dependency environment (~1.4 GB download,
5.7 GB unpacked — this replaces hours of vcpkg compilation):

```bat
tools\windows_buildenv.bat setup
```

Configure (from a "x64 Native Tools" VS command prompt, or after running
`vcvars64.bat`):

```bat
cd C:\dev\mixxx\build
cmake -G Ninja ^
  -DCMAKE_TOOLCHAIN_FILE=C:\dev\mixxx\buildenv\mixxx-deps-2.5-x64-windows-c15790e\scripts\buildsystems\vcpkg.cmake ^
  -DVCPKG_TARGET_TRIPLET=x64-windows ^
  -DCMAKE_BUILD_TYPE=RelWithDebInfo ^
  -DOPTIMIZE=portable -DQT6=ON -DFFMPEG=OFF -DKEYFINDER=OFF -DSTATIC_DEPS=OFF ^
  C:\dev\mixxx
```

Build:

```bat
cmake --build C:\dev\mixxx\build --target mixxx
```

First full build takes a few minutes (the heavy dependencies are prebuilt).
Incremental rebuilds after touching the patch are ~1–3 minutes. Result:
`C:\dev\mixxx\build\mixxx.exe` — this is what `start.bat` launches. After the
first build, `build_mixxx.bat` in this folder wraps the rebuild step.

## Syncing with upstream Mixxx

```bat
cd C:\dev\mixxx
git fetch upstream --tags
git rebase <new-release-tag> autodj-patch
```

Expect occasional conflicts in `src/mixer/playermanager.cpp/.h` — that's
where the patch lives. After any sync, re-verify:

1. The patch still compiles and `load_track_by_id` still loads tracks.
2. The `library`/`track_locations` schema query in `backend/mixxx_db.py`
   still matches Mixxx's DB (it's undocumented internal schema).
3. The prebuilt buildenv name in `tools/windows_buildenv.bat` may have
   changed — rerun `tools\windows_buildenv.bat setup` and reconfigure if so.

## What the patch does

`src/mixer/playermanager.cpp`, in `addDeckInner()`: registers a per-deck
`ControlObject` `[ChannelN],load_track_by_id`. When set to a positive integer
(from a controller script or XML mapping), `slotLoadTrackByIdToPlayer` looks
the id up via `TrackCollectionManager::getTrackById` and routes it through
the standard `slotLoadTrackToPlayer` path with `play=false` — loading never
auto-starts playback; the Python backend owns all play/pause decisions.
