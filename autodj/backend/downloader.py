import asyncio
import os
import re
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

MUSIC_DIR = os.getenv("MUSIC_DIR", "./music")


def _sanitize(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


async def download_track(artist: str, title: str) -> str | None:
    query = f"ytsearch1:{artist} {title} official audio"
    safe_name = _sanitize(f"{artist} - {title}")
    out_template = str(Path(MUSIC_DIR) / f"{safe_name}.%(ext)s")

    cmd = [
        "yt-dlp",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--output", out_template,
        "--no-playlist",
        "--quiet",
        query,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    expected_path = str(Path(MUSIC_DIR) / f"{safe_name}.mp3")
    if proc.returncode == 0 and Path(expected_path).exists():
        return expected_path

    print(f"[yt-dlp] Failed: {stderr.decode()[:200]}")
    return None
