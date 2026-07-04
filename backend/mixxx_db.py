
import os
import sqlite3
from pathlib import Path


def default_mixxxdb_path() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Mixxx" / "mixxxdb.sqlite"


def read_mixxx_id_map(mixxxdb_path: Path | None = None) -> dict[str, int]:
    path = mixxxdb_path or default_mixxxdb_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Mixxx library DB not found at {path}. "
            "Open Mixxx once and import your music folder "
            "(Library -> Add folder) so tracks get Mixxx ids."
        )

    uri = f"file:{path.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        rows = con.execute(
            '\n            SELECT library.id, track_locations.location\n            FROM library\n            JOIN track_locations ON library.location = track_locations.id\n            WHERE library.mixxx_deleted = 0\n            '
        ).fetchall()
    finally:
        con.close()

    return {_normalize(location): mixxx_id for mixxx_id, location in rows}


def _normalize(filepath: str) -> str:
    return str(Path(filepath).resolve()).replace("\\", "/").lower()
