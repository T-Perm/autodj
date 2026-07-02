import hashlib
import os
from pathlib import Path
import librosa
import numpy as np
from mutagen import File as MutagenFile
from models import Track

CAMELOT = {
    (0, True): "8B",  (0, False): "5A",
    (1, True): "3B",  (1, False): "10A",
    (2, True): "10B", (2, False): "7A",
    (3, True): "5B",  (3, False): "2A",
    (4, True): "12B", (4, False): "9A",
    (5, True): "7B",  (5, False): "4A",
    (6, True): "2B",  (6, False): "11A",
    (7, True): "9B",  (7, False): "6A",
    (8, True): "4B",  (8, False): "1A",
    (9, True): "11B", (9, False): "8A",
    (10, True): "6B", (10, False): "3A",
    (11, True): "1B", (11, False): "10A",
}

CAMELOT_COMPATIBLE = {
    "1A": ["1A","2A","12A","1B"],
    "2A": ["2A","3A","1A","2B"],
    "3A": ["3A","4A","2A","3B"],
    "4A": ["4A","5A","3A","4B"],
    "5A": ["5A","6A","4A","5B"],
    "6A": ["6A","7A","5A","6B"],
    "7A": ["7A","8A","6A","7B"],
    "8A": ["8A","9A","7A","8B"],
    "9A": ["9A","10A","8A","9B"],
    "10A": ["10A","11A","9A","10B"],
    "11A": ["11A","12A","10A","11B"],
    "12A": ["12A","1A","11A","12B"],
    "1B": ["1B","2B","12B","1A"],
    "2B": ["2B","3B","1B","2A"],
    "3B": ["3B","4B","2B","3A"],
    "4B": ["4B","5B","3B","4A"],
    "5B": ["5B","6B","4B","5A"],
    "6B": ["6B","7B","5B","6A"],
    "7B": ["7B","8B","6B","7A"],
    "8B": ["8B","9B","7B","8A"],
    "9B": ["9B","10B","8B","9A"],
    "10B": ["10B","11B","9B","10A"],
    "11B": ["11B","12B","10B","11A"],
    "12B": ["12B","1B","11B","12A"],
}


def file_hash(filepath: str) -> str:
    return hashlib.sha256(filepath.encode()).hexdigest()[:16]


def extract_metadata(filepath: str) -> dict:
    meta = {"title": Path(filepath).stem, "artist": "Unknown"}
    try:
        tags = MutagenFile(filepath, easy=True)
        if tags:
            meta["title"] = str(tags.get("title", [Path(filepath).stem])[0])
            meta["artist"] = str(tags.get("artist", ["Unknown"])[0])
    except Exception:
        pass
    return meta


def detect_mood(energy: float, tempo: float, spectral_centroid_mean: float) -> str:
    if energy > 0.7 and tempo > 130:
        return "hype"
    elif energy > 0.6 and tempo > 110:
        return "euphoric"
    elif energy < 0.3 and spectral_centroid_mean < 2000:
        return "dark"
    elif energy < 0.4:
        return "melancholic"
    else:
        return "neutral"


def guess_genre(tempo: float, spectral_centroid: float, energy: float) -> str:
    if tempo > 135 and energy > 0.6:
        return "electronic"
    elif tempo > 85 and tempo < 115 and spectral_centroid < 2500:
        return "hip-hop"
    elif tempo > 100 and spectral_centroid > 3000:
        return "pop"
    elif energy < 0.35:
        return "ambient"
    else:
        return "mixed"


def find_mix_points(y: np.ndarray, sr: int, duration_ms: int) -> tuple[int, int]:
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    frames = len(rms)

    threshold = np.max(rms) * 0.2
    mix_in_frame = next((i for i, v in enumerate(rms) if v > threshold), 0)

    mix_out_frame = frames - 1
    for i in range(frames - 1, 0, -1):
        if rms[i] > threshold:
            mix_out_frame = i
            break

    hop_length = 512
    mix_in_ms = int((mix_in_frame * hop_length / sr) * 1000)
    mix_out_ms = int((mix_out_frame * hop_length / sr) * 1000)

    mix_in_ms = min(mix_in_ms, 30000)
    mix_out_ms = max(mix_out_ms, duration_ms - 30000)

    return mix_in_ms, mix_out_ms


def find_drop(y: np.ndarray, sr: int, beat_times: np.ndarray,
              mix_in_ms: int, duration_ms: int) -> int:
    hop = 512
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    frame_t = hop / sr
    win = max(1, int(4.0 / frame_t))

    lo = int((mix_in_ms / 1000) / frame_t)
    hi = int(len(rms) * 0.7)
    if hi - lo < 2 * win:
        return mix_in_ms

    best_i, best_jump = lo, 0.0
    for i in range(lo + win, hi - win, max(1, win // 8)):
        jump = float(np.mean(rms[i:i + win]) - np.mean(rms[i - win:i]))
        if jump > best_jump:
            best_jump, best_i = jump, i

    drop_s = best_i * frame_t
    if len(beat_times):
        drop_s = float(beat_times[np.argmin(np.abs(beat_times - drop_s))])
    return int(drop_s * 1000)


def analyze_track(filepath: str) -> Track:
    y, sr = librosa.load(filepath, sr=None, mono=True)
    duration_ms = int(len(y) / sr * 1000)

    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    bpm = float(tempo)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    first_beat_ms = int(beat_times[0] * 1000) if len(beat_times) else 0

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    chroma_mean = np.mean(chroma, axis=1)
    pitch_class = int(np.argmax(chroma_mean))
    major_third = chroma_mean[(pitch_class + 4) % 12]
    minor_third = chroma_mean[(pitch_class + 3) % 12]
    is_major = major_third > minor_third
    camelot_key = CAMELOT.get((pitch_class, is_major), "1A")

    rms = librosa.feature.rms(y=y)[0]
    energy = float(np.mean(rms) / (np.max(rms) + 1e-8))

    spec_centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    spec_mean = float(np.mean(spec_centroid))

    mood = detect_mood(energy, bpm, spec_mean)
    genre_hint = guess_genre(bpm, spec_mean, energy)
    mix_in_ms, mix_out_ms = find_mix_points(y, sr, duration_ms)
    drop_ms = find_drop(y, sr, beat_times, mix_in_ms, duration_ms)

    meta = extract_metadata(filepath)

    return Track(
        id=file_hash(filepath),
        filepath=filepath,
        title=meta["title"],
        artist=meta["artist"],
        bpm=round(bpm, 1),
        key=camelot_key,
        energy=round(energy, 3),
        mood=mood,
        genre_hint=genre_hint,
        mix_in_ms=mix_in_ms,
        mix_out_ms=mix_out_ms,
        duration_ms=duration_ms,
        first_beat_ms=first_beat_ms,
        drop_ms=drop_ms,
    )
