from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Priority = Literal["low", "medium", "high", "not_specified"]
Confidence = Literal["high", "medium", "needs_review"]
RiskSeverity = Literal["low", "medium", "high", "not_specified"]


class TranscriptSegment(BaseModel):
    id: int
    start: float
    end: float
    text: str
    speaker: str | None = None


class Topic(BaseModel):
    title: str
    theses: list[str] = Field(default_factory=list)
    evidence_segment_ids: list[int] = Field(default_factory=list)


class Decision(BaseModel):
    text: str
    evidence_segment_ids: list[int] = Field(default_factory=list)
    confidence: Confidence = "needs_review"


class OpenQuestion(BaseModel):
    text: str
    evidence_segment_ids: list[int] = Field(default_factory=list)
    confidence: Confidence = "needs_review"


class ActionItem(BaseModel):
    assignee: str | None = None
    task: str
    deadline: str | None = None
    priority: Priority = "not_specified"
    evidence_segment_ids: list[int] = Field(default_factory=list)
    confidence: Confidence = "needs_review"


class RiskItem(BaseModel):
    text: str
    severity: RiskSeverity = "not_specified"
    evidence_segment_ids: list[int] = Field(default_factory=list)
    confidence: Confidence = "needs_review"


class MeetingAnalysis(BaseModel):
    summary: list[str] = Field(default_factory=list)
    topics: list[Topic] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    risks: list[RiskItem] = Field(default_factory=list)


class ModelInfo(BaseModel):
    whisper: str
    llm: str
    offline_enforced: bool


class MeetingAnalyzeResponse(BaseModel):
    meeting_id: str
    filename: str
    language: str | None = None
    duration_seconds: float
    processing_ms: int

    transcript: str
    segments: list[TranscriptSegment]

    analysis: MeetingAnalysis
    models: ModelInfo
