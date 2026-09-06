"""All executable options are typed/allowlisted; clients never send paths or commands."""
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Point(Strict):
    time: float = Field(ge=0, le=2400)
    db: float = Field(ge=-60, le=12)


class TrackPatch(Strict):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    gain_db: float | None = Field(default=None, ge=-60, le=12)
    pan: float | None = Field(default=None, ge=-1, le=1)
    muted: bool | None = None
    solo: bool | None = None
    offset: float | None = Field(default=None, ge=0, le=2400)
    automation: list[Point] | None = Field(default=None, max_length=400)

    @model_validator(mode="after")
    def ordered(self):
        if self.automation:
            times = [p.time for p in self.automation]
            if any(a >= b for a, b in zip(times, times[1:])):
                raise ValueError("Automation points must have strictly increasing times")
        return self


class ProjectPatch(Strict):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    bpm: float | None = Field(default=None, ge=30, le=300)
    tonic: int | None = Field(default=None, ge=0, le=11)
    scale: Literal["major", "minor", "chromatic"] | None = None
    lyrics: str | None = Field(default=None, max_length=100000)


class Revision(Strict):
    revision: int = Field(ge=1)


class Effects(Strict):
    denoise: float = Field(default=0.2, ge=0, le=1)
    highpass: float = Field(default=80, ge=0, le=500)
    low_db: float = Field(default=0, ge=-12, le=12)
    mid_db: float = Field(default=0, ge=-12, le=12)
    high_db: float = Field(default=0, ge=-12, le=12)
    compressor_db: float = Field(default=-20, ge=-60, le=0)
    ratio: float = Field(default=3, ge=1, le=12)
    deesser: float = Field(default=0.2, ge=0, le=1)
    reverb: float = Field(default=0.12, ge=0, le=0.6)
    delay: float = Field(default=0, ge=0, le=0.6)
    output_db: float = Field(default=0, ge=-24, le=12)


class PitchNote(Strict):
    start: float = Field(ge=0, le=2400)
    end: float = Field(gt=0, le=2400)
    midi: float = Field(ge=24, le=108)

    @model_validator(mode="after")
    def interval(self):
        if self.end <= self.start:
            raise ValueError("Note end must be after start")
        return self


class JobRequest(Strict):
    revision: int = Field(ge=1)
    task: Literal["separate", "transpose", "effects", "erase", "pitch", "tune", "harmony", "export", "restore", "automix"]
    track_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    model: Literal["bs-roformer", "melband", "ensemble", "denoise", "dereverb"] = "bs-roformer"
    quality: Literal["balanced", "quality"] = "balanced"
    semitones: int = Field(default=0, ge=-12, le=12)
    effects: Effects = Field(default_factory=Effects)
    start: float = Field(default=0, ge=0, le=2400)
    end: float | None = Field(default=None, gt=0, le=2400)
    pitch_engine: Literal["pyin", "torchcrepe"] = "pyin"
    strength: float = Field(default=0.75, ge=0, le=1)
    retune_ms: float = Field(default=100, ge=0, le=500)
    tonic: int = Field(default=0, ge=0, le=11)
    scale: Literal["major", "minor", "chromatic"] = "major"
    notes: list[PitchNote] = Field(default_factory=list, max_length=2000)
    voices: list[Literal[-4, -2, 2, 4, 7]] = Field(default_factory=lambda: [2, 4], min_length=1, max_length=3)
    format: Literal["wav", "mp3", "flac"] = "wav"
    target_lufs: float = Field(default=-14, ge=-24, le=-9)

    @model_validator(mode="after")
    def task_requirements(self):
        if self.task in {"effects", "erase", "pitch", "tune", "harmony", "restore"} and not self.track_id:
            raise ValueError("Select an audio track first")
        if self.task == "erase" and (self.end is None or self.end <= self.start):
            raise ValueError("Select a non-empty region")
        if self.task == "separate" and self.model not in {"bs-roformer", "melband", "ensemble"}:
            raise ValueError("This is not a separation model")
        if self.task == "restore" and self.model not in {"denoise", "dereverb"}:
            raise ValueError("Choose a restoration model")
        for a, b in zip(sorted(self.notes, key=lambda n: n.start), sorted(self.notes, key=lambda n: n.start)[1:]):
            if a.end > b.start:
                raise ValueError("Manual pitch regions cannot overlap")
        return self
