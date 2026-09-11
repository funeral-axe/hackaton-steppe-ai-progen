from __future__ import annotations

import gc
import os
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import ctranslate2
from faster_whisper import WhisperModel

from meeting.schemas import TranscriptSegment
from services.whisper_models import resolve_whisper_model


def _bool_env(
    name: str,
    default: bool = False,
) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _runtime() -> tuple[str, str]:
    device = os.getenv(
        "MEETING_WHISPER_DEVICE",
        "",
    ).strip()

    compute_type = os.getenv(
        "MEETING_WHISPER_COMPUTE_TYPE",
        "",
    ).strip()

    if not device:
        device = (
            "cuda"
            if ctranslate2.get_cuda_device_count() > 0
            else "cpu"
        )

    if not compute_type:
        compute_type = (
            "int8"
            if device == "cuda"
            else "int8"
        )

    return device, compute_type


@lru_cache(maxsize=1)
def _load_model(
    source: str,
    device: str,
    compute_type: str,
    offline: bool,
) -> WhisperModel:
    print(
        "[meeting] Loading Faster-Whisper: "
        f"model={source}, "
        f"device={device}, "
        f"compute_type={compute_type}, "
        f"offline={offline}"
    )

    return WhisperModel(
        source,
        device=device,
        compute_type=compute_type,
        local_files_only=offline,
    )


def _probe_duration(
    file_path: str,
) -> float:
    process = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            file_path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    return float(
        process.stdout.strip()
    )


def _extract_chunk(
    source: str,
    destination: str,
    start: float,
    duration: float,
) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(start),
            "-i",
            source,
            "-t",
            str(duration),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            destination,
        ],
        check=True,
    )


def transcribe_meeting(
    file_path: str,
    model_selection: str = "large-v3-trained",
) -> dict:
    option = resolve_whisper_model(
        model_selection
    )

    device, compute_type = _runtime()

    offline = _bool_env(
        "MEETING_OFFLINE",
        False,
    )

    model = _load_model(
        option.source,
        device,
        compute_type,
        offline,
    )

    total_duration = _probe_duration(
        file_path
    )

    chunk_seconds = float(
        os.getenv(
            "MEETING_CHUNK_SECONDS",
            "30",
        )
    )

    beam_size = int(
        os.getenv(
            "MEETING_WHISPER_BEAM_SIZE",
            "1",
        )
    )

    all_segments: list[
        TranscriptSegment
    ] = []

    transcript_parts: list[str] = []

    detected_languages: list[str] = []

    segment_id = 1
    chunk_start = 0.0

    with tempfile.TemporaryDirectory() as tmp:
        while chunk_start < total_duration:
            current_duration = min(
                chunk_seconds,
                total_duration - chunk_start,
            )

            chunk_path = str(
                Path(tmp)
                / f"chunk-{segment_id}.wav"
            )

            _extract_chunk(
                source=file_path,
                destination=chunk_path,
                start=chunk_start,
                duration=current_duration,
            )

            pieces, info = model.transcribe(
                chunk_path,
                task="transcribe",
                language=None,
                beam_size=beam_size,
                temperature=0.0,
                vad_filter=True,
                vad_parameters={
                    "min_silence_duration_ms": 350,
                    "speech_pad_ms": 200,
                },
                condition_on_previous_text=False,
                word_timestamps=False,
            )

            chunk_text_parts: list[str] = []

            for piece in pieces:
                text = (
                    piece.text
                    or ""
                ).strip()

                if text:
                    chunk_text_parts.append(
                        text
                    )

            chunk_text = " ".join(
                chunk_text_parts
            ).strip()

            language = getattr(
                info,
                "language",
                None,
            )

            if language:
                detected_languages.append(
                    language
                )

            if chunk_text:
                # Evidence timestamp строим по настоящему
                # временному окну, а не по нестабильным
                # timestamp tokens fine-tuned Whisper.
                all_segments.append(
                    TranscriptSegment(
                        id=segment_id,
                        start=round(
                            chunk_start,
                            2,
                        ),
                        end=round(
                            min(
                                chunk_start
                                + current_duration,
                                total_duration,
                            ),
                            2,
                        ),
                        text=chunk_text,
                    )
                )

                transcript_parts.append(
                    chunk_text
                )

                segment_id += 1

            chunk_start += current_duration

    language = (
        max(
            set(detected_languages),
            key=detected_languages.count,
        )
        if detected_languages
        else None
    )

    if _bool_env(
        "MEETING_RELEASE_WHISPER",
        True,
    ):
        _load_model.cache_clear()

        del model

        gc.collect()

    return {
        "transcript": " ".join(
            transcript_parts
        ),
        "segments": all_segments,
        "language": language,
        "duration_seconds": total_duration,
        "whisper_model": option.key,
    }


def runtime_info() -> dict:
    device, compute_type = _runtime()

    return {
        "device": device,
        "compute_type": compute_type,
        "cuda_devices": (
            ctranslate2
            .get_cuda_device_count()
        ),
        "offline_enforced": _bool_env(
            "MEETING_OFFLINE",
            False,
        ),
        "chunk_seconds": float(
            os.getenv(
                "MEETING_CHUNK_SECONDS",
                "30",
            )
        ),
    }
