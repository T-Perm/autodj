from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []

for pkg in ("librosa", "numba", "sklearn", "lazy_loader", "soundfile", "soxr", "audioread"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

hiddenimports += ["pyttsx3.drivers.sapi5", "rtmidi", "mido.backends.rtmidi"]
hiddenimports += collect_submodules("sqlmodel")

a = Analysis(
    ["backend/autodj.py"],
    pathex=["backend"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["matplotlib", "tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="autodj",
          console=True, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, name="autodj")
