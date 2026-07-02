from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


class Track(SQLModel, table=True):
    id: str = Field(primary_key=True)
    filepath: str
    title: str
    artist: str
    bpm: float = 120.0
    key: str = "1A"
    energy: float = 0.5
    mood: str = "neutral"
    genre_hint: str = "unknown"
    mix_in_ms: int = 0
    mix_out_ms: int = 0
    duration_ms: int = 0
    first_beat_ms: Optional[int] = None
    drop_ms: Optional[int] = None
    analyzed_at: datetime = Field(default_factory=datetime.utcnow)


class QueueItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    track_id: str
    position: int
    transition_style: str = "beatmatch_crossfade"
    transition_duration_bars: int = 8
    added_at: datetime = Field(default_factory=datetime.utcnow)
