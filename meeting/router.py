from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
from threading import Lock

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from meeting.analyzer import analyze_meeting
from meeting.exporter import export_csv, export_json
from meeting.llm import get_llm_model, get_ollama_url
from meeting.schemas import MeetingAnalyzeResponse
from meeting.transcriber import runtime_info


router = APIRouter(
    prefix="/api/meeting",
    tags=["Meeting Intelligence"],
)


SUPPORTED_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".flac",
    ".ogg",
    ".aac",
    ".opus",
}


_meetings: dict[
    str,
    MeetingAnalyzeResponse,
] = {}

_meetings_lock = Lock()


def _save_meeting(
    meeting: MeetingAnalyzeResponse,
) -> None:
    with _meetings_lock:
        _meetings[
            meeting.meeting_id
        ] = meeting


def _get_meeting(
    meeting_id: str,
) -> MeetingAnalyzeResponse:
    with _meetings_lock:
        meeting = _meetings.get(
            meeting_id
        )

    if meeting is None:
        raise HTTPException(
            status_code=404,
            detail="Meeting not found",
        )

    return meeting


@router.get("/health")
def meeting_health():
    whisper = runtime_info()

    return {
        "status": "ok",
        "service": "ProGen Meeting Intelligence",
        "transcription": "faster-whisper",
        "llm": "ollama",
        "llm_model": get_llm_model(),
        "ollama_url": get_ollama_url(),
        "whisper_runtime": whisper,
        "external_commercial_api": False,
    }


@router.post(
    "/analyze",
    response_model=MeetingAnalyzeResponse,
)
def analyze_audio(
    file: UploadFile = File(...),
    whisper_model: str = Form("small"),
):
    filename = (
        file.filename
        or "meeting.wav"
    )

    suffix = Path(
        filename
    ).suffix.lower()

    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported audio format. "
                "Use MP3, WAV or M4A."
            ),
        )

    temp_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix,
        ) as temporary:
            shutil.copyfileobj(
                file.file,
                temporary,
            )

            temp_path = temporary.name

        meeting = analyze_meeting(
            file_path=temp_path,
            original_filename=filename,
            whisper_model=whisper_model,
        )

        _save_meeting(
            meeting
        )

        return meeting

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    finally:
        file.file.close()

        if temp_path:
            try:
                Path(
                    temp_path
                ).unlink(
                    missing_ok=True
                )
            except OSError:
                pass


@router.get(
    "/{meeting_id}",
    response_model=MeetingAnalyzeResponse,
)
def get_meeting(
    meeting_id: str,
):
    return _get_meeting(
        meeting_id
    )


@router.get(
    "/{meeting_id}/export/json"
)
def download_json(
    meeting_id: str,
):
    meeting = _get_meeting(
        meeting_id
    )

    return Response(
        content=export_json(
            meeting
        ),
        media_type=(
            "application/json; "
            "charset=utf-8"
        ),
        headers={
            "Content-Disposition": (
                "attachment; "
                f'filename="meeting-{meeting_id}.json"'
            )
        },
    )


@router.get(
    "/{meeting_id}/export/csv"
)
def download_csv(
    meeting_id: str,
):
    meeting = _get_meeting(
        meeting_id
    )

    return Response(
        content=export_csv(
            meeting
        ),
        media_type=(
            "text/csv; "
            "charset=utf-8"
        ),
        headers={
            "Content-Disposition": (
                "attachment; "
                f'filename="meeting-{meeting_id}.csv"'
            )
        },
    )
