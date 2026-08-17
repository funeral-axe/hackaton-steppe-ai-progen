from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import re
from threading import Lock
from typing import Any, Iterable
from uuid import uuid4

SUPPORTED_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma", ".mp4", ".opus"
}

_ONLINE_JOBS: dict[str, dict[str, Any]] = {}
_ONLINE_JOBS_LOCK = Lock()


def parse_keywords(raw_keywords: str) -> list[str]:
    parts = re.split(r"[,;\\n]+", raw_keywords or "")
    result: list[str] = []
    seen: set[str] = set()

    for part in parts:
        keyword = " ".join(part.strip().lower().split())
        if keyword and keyword not in seen:
            seen.add(keyword)
            result.append(keyword)

    if not result:
        raise ValueError("Введите хотя бы одно ключевое слово или фразу")
    if len(result) > 10:
        raise ValueError("Можно указать не более 10 ключевых слов или фраз")
    return result


def format_timestamp(seconds: float | None) -> str:
    total = max(0, int(seconds or 0))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\\w){re.escape(keyword)}(?!\\w)", re.IGNORECASE)


def keyword_counts(text: str, keywords: Iterable[str]) -> dict[str, int]:
    source = text or ""
    return {keyword: len(_keyword_pattern(keyword).findall(source)) for keyword in keywords}


def discover_audio_files(folder: Path) -> list[Path]:
    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


@lru_cache(maxsize=6)
def _get_whisper_model(model_name: str, device: str, compute_type: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Не установлен faster-whisper. Выполните: pip install faster-whisper"
        ) from exc

    print(
        f"[word-search] Загрузка Whisper: model={model_name}, "
        f"device={device}, compute_type={compute_type}"
    )
    return WhisperModel(model_name, device=device, compute_type=compute_type)


def get_best_whisper_model(model_name: str):
    """Автовыбор NVIDIA GPU с автоматическим fallback на CPU."""
    try:
        import ctranslate2
        gpu_count = ctranslate2.get_cuda_device_count()
    except Exception as exc:
        print(f"[word-search] Не удалось проверить CUDA: {exc}")
        gpu_count = 0

    if gpu_count > 0:
        print(f"[word-search] Найдено NVIDIA GPU: {gpu_count}")
        try:
            model = _get_whisper_model(model_name, "cuda", "float16")
            print("[word-search] Whisper работает на NVIDIA GPU")
            return model
        except Exception as gpu_error:
            print(
                "[word-search] GPU недоступен, автоматическое переключение на CPU: "
                f"{type(gpu_error).__name__}: {gpu_error}"
            )

    print("[word-search] Whisper работает на CPU")
    return _get_whisper_model(model_name, "cpu", "int8")


def create_online_job(
    folder_path: str,
    keywords: list[str],
    search_mode: str,
    model_name: str,
    language: str | None,
) -> str:
    job_id = str(uuid4())
    with _ONLINE_JOBS_LOCK:
        _ONLINE_JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "folder_path": folder_path,
            "keywords": keywords,
            "search_mode": search_mode,
            "model_name": model_name,
            "language": language or "auto",
            "total_files": 0,
            "processed_files": 0,
            "successful_files": 0,
            "failed_files": 0,
            "current_file": None,
            "percent": 0,
            "error": None,
            "file_errors": [],
            "results": [],
        }
    return job_id


def get_online_job(job_id: str) -> dict[str, Any] | None:
    with _ONLINE_JOBS_LOCK:
        job = _ONLINE_JOBS.get(job_id)
        return deepcopy(job) if job else None


def _update_job(job_id: str, **values: Any) -> None:
    with _ONLINE_JOBS_LOCK:
        job = _ONLINE_JOBS.get(job_id)
        if job:
            job.update(values)


def _append_file_error(job_id: str, file_name: str, error: Exception) -> None:
    with _ONLINE_JOBS_LOCK:
        job = _ONLINE_JOBS.get(job_id)
        if not job:
            return
        job["file_errors"].append(
            {
                "file_name": file_name,
                "error": f"{type(error).__name__}: {error}"[:1000],
            }
        )


def run_online_search(
    job_id: str,
    folder_path: str,
    raw_keywords: str,
    search_mode: str = "any",
    model_name: str = "medium",
    language: str | None = "ru",
) -> None:
    """Расшифровывает аудио и ищет слова без сохранения результатов в БД."""
    try:
        folder = Path(folder_path).expanduser().resolve()
        if not folder.exists():
            raise ValueError(f"Папка не существует: {folder}")
        if not folder.is_dir():
            raise ValueError(f"Указанный путь не является папкой: {folder}")

        keywords = parse_keywords(raw_keywords)
        mode = "all" if search_mode == "all" else "any"
        files = discover_audio_files(folder)

        _update_job(
            job_id,
            status="running",
            total_files=len(files),
            processed_files=0,
            successful_files=0,
            failed_files=0,
            percent=0,
            current_file=None,
            results=[],
            error=None,
        )

        if not files:
            _update_job(job_id, status="completed", percent=100)
            return

        model = get_best_whisper_model(model_name)

        results: list[dict[str, Any]] = []
        successful = 0
        failed = 0

        for number, audio_path in enumerate(files, start=1):
            _update_job(job_id, current_file=audio_path.name)

            try:
                transcribe_kwargs: dict[str, Any] = {
                    "task": "transcribe",
                    "beam_size": 8,
                    "best_of": 8,
                    "temperature": 0.0,
                    "vad_filter": True,
                    "vad_parameters": {
                        "min_silence_duration_ms": 500,
                        "speech_pad_ms": 300,
                    },
                    "condition_on_previous_text": True,
                    "word_timestamps": False,
                    "initial_prompt": (
                        "Аудиозапись на русском или казахском языке. "
                        "Возможные ключевые слова и фразы: " + ", ".join(keywords)
                    ),
                }

                if language:
                    transcribe_kwargs["language"] = language

                segments_iterator, info = model.transcribe(
                    str(audio_path),
                    **transcribe_kwargs,
                )

                transcript_parts: list[str] = []
                segment_matches: list[dict[str, Any]] = []
                last_end = 0.0

                for segment in segments_iterator:
                    text = (segment.text or "").strip()
                    if not text:
                        continue

                    transcript_parts.append(text)
                    start = float(segment.start)
                    end = float(segment.end)
                    last_end = end

                    segment_counts = keyword_counts(text, keywords)
                    found_in_segment = [
                        word for word, count in segment_counts.items() if count > 0
                    ]

                    if found_in_segment:
                        segment_matches.append(
                            {
                                "start": start,
                                "end": end,
                                "start_label": format_timestamp(start),
                                "end_label": format_timestamp(end),
                                "text": text,
                                "keywords": found_in_segment,
                                "counts": {
                                    word: count
                                    for word, count in segment_counts.items()
                                    if count > 0
                                },
                            }
                        )

                transcription = " ".join(transcript_parts)
                counts = keyword_counts(transcription, keywords)
                found_keywords = [
                    word for word, count in counts.items() if count > 0
                ]

                matched = (
                    len(found_keywords) == len(keywords)
                    if mode == "all"
                    else bool(found_keywords)
                )

                if matched:
                    duration = float(getattr(info, "duration", last_end) or last_end)
                    results.append(
                        {
                            "id": number,
                            "file_name": audio_path.name,
                            "file_path": str(audio_path),
                            "language": getattr(info, "language", None) or "-",
                            "duration": format_timestamp(duration),
                            "found_keywords": found_keywords,
                            "counts": {k: v for k, v in counts.items() if v > 0},
                            "total_matches": sum(counts.values()),
                            "segments": segment_matches,
                        }
                    )

                successful += 1

            except Exception as file_error:
                failed += 1
                _append_file_error(job_id, audio_path.name, file_error)
                print(
                    f"[word-search] Ошибка файла {audio_path}: "
                    f"{type(file_error).__name__}: {file_error}"
                )

            percent = round(number / len(files) * 100)
            _update_job(
                job_id,
                processed_files=number,
                successful_files=successful,
                failed_files=failed,
                percent=percent,
                results=results,
            )

        _update_job(
            job_id,
            status="completed",
            current_file=None,
            percent=100,
            results=results,
        )

        print("[word-search] Онлайн-поиск завершен")
        print(f"[word-search] Обработано: {successful}")
        print(f"[word-search] Ошибок: {failed}")
        print(f"[word-search] Совпадений: {len(results)}")

    except Exception as exc:
        print(f"[word-search] {type(exc).__name__}: {exc}")
        _update_job(
            job_id,
            status="error",
            current_file=None,
            error=f"{type(exc).__name__}: {exc}"[:2000],
        )
