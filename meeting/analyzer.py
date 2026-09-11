from __future__ import annotations

import os
import time
from uuid import uuid4

from meeting.llm import analyze_transcript, get_llm_model
from meeting.schemas import MeetingAnalyzeResponse, ModelInfo
from meeting.transcriber import transcribe_meeting


def _offline_enforced() -> bool:
    return (
        os.getenv(
            "MEETING_OFFLINE",
            "0",
        )
        .strip()
        .lower()
        in {
            "1",
            "true",
            "yes",
            "on",
        }
    )


def analyze_meeting(
    file_path: str,
    original_filename: str,
    whisper_model: str = "small",
) -> MeetingAnalyzeResponse:
    started = time.perf_counter()

    transcription = transcribe_meeting(
        file_path=file_path,
        model_selection=whisper_model,
    )

    analysis = analyze_transcript(
        transcription["segments"]
    )

    processing_ms = round(
        (time.perf_counter() - started)
        * 1000
    )

    return MeetingAnalyzeResponse(
        meeting_id=str(uuid4()),
        filename=original_filename,
        language=transcription["language"],
        duration_seconds=transcription[
            "duration_seconds"
        ],
        processing_ms=processing_ms,
        transcript=transcription["transcript"],
        segments=transcription["segments"],
        analysis=analysis,
        models=ModelInfo(
            whisper=transcription[
                "whisper_model"
            ],
            llm=get_llm_model(),
            offline_enforced=_offline_enforced(),
        ),
    )
