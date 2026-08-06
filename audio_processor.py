from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import re
from typing import Iterable

from sqlalchemy.orm import Session

from database import AudioFile, AudioSegment, IndexJob, SessionLocal, Setting

SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma", ".mp4"}


def parse_keywords(raw_keywords: str) -> list[str]:
    parts = re.split(r"[,;\n]+", raw_keywords)
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        keyword = " ".join(part.strip().lower().split())
        if keyword and keyword not in seen:
            seen.add(keyword)
            result.append(keyword)
    if not result:
        raise ValueError("Введите хотя бы одно ключевое слово")
    if len(result) > 10:
        raise ValueError("Можно указать не более 10 ключевых слов")
    return result


def format_timestamp(seconds: float | None) -> str:
    total = max(0, int(seconds or 0))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    # Границы через \w корректно работают и для кириллицы.
    return re.compile(rf"(?<!\w){re.escape(keyword)}(?!\w)", re.IGNORECASE)


def keyword_counts(text: str, keywords: Iterable[str]) -> dict[str, int]:
    return {keyword: len(_keyword_pattern(keyword).findall(text or "")) for keyword in keywords}


def search_audio(db: Session, keywords: list[str], mode: str = "any") -> list[dict]:
    mode = "all" if mode == "all" else "any"
    results: list[dict] = []
    files = db.query(AudioFile).filter(AudioFile.status == "completed").order_by(AudioFile.file_name).all()

    for audio in files:
        total_counts = keyword_counts(audio.transcription or "", keywords)
        found_words = [word for word, count in total_counts.items() if count > 0]
        matched = len(found_words) == len(keywords) if mode == "all" else bool(found_words)
        if not matched:
            continue

        segment_matches: list[dict] = []
        for segment in audio.segments:
            counts = keyword_counts(segment.text, keywords)
            segment_found = [word for word, count in counts.items() if count > 0]
            if segment_found:
                segment_matches.append({
                    "start": segment.start_time,
                    "end": segment.end_time,
                    "start_label": format_timestamp(segment.start_time),
                    "end_label": format_timestamp(segment.end_time),
                    "text": segment.text,
                    "keywords": segment_found,
                })

        results.append({
            "id": audio.id,
            "file_name": audio.file_name,
            "file_path": audio.file_path,
            "language": audio.language or "-",
            "duration": format_timestamp(audio.duration),
            "found_keywords": found_words,
            "counts": {k: v for k, v in total_counts.items() if v > 0},
            "total_matches": sum(total_counts.values()),
            "segments": segment_matches,
        })
    return results


def discover_audio_files(folder: Path) -> list[Path]:
    return sorted(
        path for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def _get_setting(db: Session, key: str, default: str) -> str:
    item = db.query(Setting).filter(Setting.key == key).first()
    return item.value if item and item.value else default


def _create_whisper_model(model_name: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Не установлен faster-whisper. Выполните: pip install faster-whisper"
        ) from exc

    device = os.getenv("WHISPER_DEVICE", "cpu")
    compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "int8" if device == "cpu" else "float16")
    return WhisperModel(model_name, device=device, compute_type=compute_type)


def run_index_job(job_id: int) -> None:
    db = SessionLocal()
    job = db.query(IndexJob).filter(IndexJob.id == job_id).first()
    if not job:
        db.close()
        return

    try:
        folder = Path(job.folder_path).expanduser().resolve()
        if not folder.exists() or not folder.is_dir():
            raise ValueError("Указанная папка не существует или недоступна")

        files = discover_audio_files(folder)
        job.status = "running"
        job.started_at = datetime.utcnow()
        job.total_files = len(files)
        db.commit()

        model_name = _get_setting(db, "whisper_model", "large-v3")
        model = _create_whisper_model(model_name)

        for path in files:
            job.current_file = path.name
            db.commit()
            try:
                stat = path.stat()
                normalized_path = str(path)
                audio = db.query(AudioFile).filter(AudioFile.file_path == normalized_path).first()

                unchanged = bool(
                    audio
                    and audio.status == "completed"
                    and audio.file_size == stat.st_size
                    and audio.modified_timestamp == stat.st_mtime
                )
                if unchanged:
                    job.processed_files += 1
                    job.successful_files += 1
                    db.commit()
                    continue

                if not audio:
                    audio = AudioFile(file_name=path.name, file_path=normalized_path)
                    db.add(audio)
                    db.flush()

                audio.file_name = path.name
                audio.file_size = stat.st_size
                audio.modified_timestamp = stat.st_mtime
                audio.status = "processing"
                audio.error_message = None
                audio.segments.clear()
                db.commit()

                segments_iter, info = model.transcribe(
                    normalized_path,
                    language="ru",
                    task="transcribe",
                    beam_size=10,
                    best_of=10,
                    vad_filter=True,
                    vad_parameters={
                        "min_silence_duration_ms": 500,
                    },
                    word_timestamps=True,
                    condition_on_previous_text=True,
                    temperature=0.0,
                )
                transcript_parts: list[str] = []
                new_segments: list[AudioSegment] = []
                last_end = 0.0
                for segment in segments_iter:
                    text = segment.text.strip()
                    if not text:
                        continue
                    transcript_parts.append(text)
                    last_end = float(segment.end)
                    new_segments.append(AudioSegment(
                        audio_file_id=audio.id,
                        start_time=float(segment.start),
                        end_time=float(segment.end),
                        text=text,
                    ))

                db.add_all(new_segments)
                audio.transcription = " ".join(transcript_parts)
                audio.language = getattr(info, "language", None)
                audio.duration = float(getattr(info, "duration", last_end) or last_end)
                audio.status = "completed"
                audio.indexed_at = datetime.utcnow()
                job.successful_files += 1
            except Exception as exc:
                db.rollback()
                audio = db.query(AudioFile).filter(AudioFile.file_path == str(path)).first()
                if not audio:
                    audio = AudioFile(file_name=path.name, file_path=str(path))
                    db.add(audio)
                audio.status = "error"
                audio.error_message = str(exc)[:2000]
                job.failed_files += 1
            finally:
                job.processed_files += 1
                db.commit()

        job.status = "completed"
        job.current_file = None
        job.finished_at = datetime.utcnow()
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.query(IndexJob).filter(IndexJob.id == job_id).first()
        if job:
            job.status = "error"
            job.error_message = str(exc)[:2000]
            job.finished_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()
