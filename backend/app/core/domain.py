from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Product(Model):
    id: str = Field(default_factory=lambda: new_id("product"))
    name: str = Field(min_length=1, max_length=200)
    variant: str = Field(default="", max_length=200)
    confirmed: bool = False


class Asset(Model):
    id: str
    path: str
    name: str
    sha256: str
    duration_us: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    has_audio: bool
    video_start_us: int = 0
    audio_start_us: int = 0
    session_start_us: int = Field(default=0, ge=0)


class Span(Model):
    source_asset_id: str
    start_us: int = Field(ge=0)
    end_us: int = Field(gt=0)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.end_us <= self.start_us:
            raise ValueError("区间结束时间必须晚于开始时间")
        return self


class Transcript(Span):
    text: str = Field(min_length=1, max_length=5000)
    timing: Literal["sentence", "audio_chunk"] = "sentence"


class Candidate(Span):
    id: str = Field(default_factory=lambda: new_id("candidate"))
    product_id: str
    tags: list[str] = Field(default_factory=list, max_length=30)
    reason: str = Field(default="", max_length=2000)
    evidence: str = Field(default="", max_length=4000)
    quality: float = Field(default=0.5, ge=0, le=1)


class Clip(Model):
    source_asset_id: str
    source_start_us: int = Field(ge=0)
    source_end_us: int = Field(gt=0)
    timeline_start_us: int = Field(ge=0)
    candidate_id: str
    audio_gain_db: float = Field(default=0.0, ge=-40, le=12)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.source_end_us <= self.source_start_us:
            raise ValueError("切点区间无效")
        return self

    @property
    def duration_us(self) -> int:
        return self.source_end_us - self.source_start_us


class Subtitle(Model):
    start_us: int = Field(ge=0)
    end_us: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.end_us <= self.start_us:
            raise ValueError("字幕区间无效")
        return self


class Canvas(Model):
    width: int = Field(gt=0, le=4096)
    height: int = Field(gt=0, le=4096)
    fps: int = Field(default=30, ge=12, le=60)

    @model_validator(mode="after")
    def even_size(self):
        if self.width % 2 or self.height % 2:
            raise ValueError("输出尺寸必须为偶数")
        return self


class Plan(Model):
    schema_version: Literal["1.0"] = "1.0"
    id: str = Field(default_factory=lambda: new_id("plan"))
    product_id: str
    revision: int = Field(default=1, ge=1)
    target_duration_us: int = Field(default=60_000_000, gt=0)
    canvas: Canvas
    clips: list[Clip] = Field(min_length=1, max_length=100)
    subtitles: list[Subtitle] = Field(default_factory=list, max_length=5000)
    status: Literal["needs_review", "approved"] = "needs_review"
    instructions: str = Field(default="", max_length=4000)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def continuous_timeline(self):
        cursor = 0
        for clip in self.clips:
            if clip.timeline_start_us != cursor:
                raise ValueError("第一版时间线必须连续，不允许空隙或片段重叠")
            cursor += clip.duration_us
        for subtitle in self.subtitles:
            if subtitle.end_us > cursor:
                raise ValueError("字幕超出成片时长")
        return self

    @property
    def duration_us(self) -> int:
        return sum(clip.duration_us for clip in self.clips)


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    pause_requested = "pause_requested"
    paused = "paused"
    cancel_requested = "cancel_requested"
    cancelled = "cancelled"
    succeeded = "succeeded"
    failed = "failed"


class Batch(Model):
    id: str = Field(default_factory=lambda: new_id("batch"))
    index: int = Field(ge=0)
    spans: list[Span]
    duration_us: int = Field(gt=0)
    partial: bool
