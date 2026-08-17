from datetime import datetime
import os
import shutil
import subprocess
import tempfile
import time
import traceback
import wave

from multiprocessing import Pool
import numpy as np
import torch
import torch.nn.functional as F
from services.speaker_model import DEVICE

import json
import hashlib

# --- СТРОГИЙ ОФФЛАЙН РЕЖИМ ---
# Запрещаем библиотекам любые сетевые запросы (работаем только с локальным кэшем)

# --- АВТООПРЕДЕЛЕНИЕ УСТРОЙСТВА ---
# SpeechBrain поддерживает cuda и cpu. На Mac используется CPU, на боевом ПК с NVIDIA — CUDA.
print(f"🚀 Нейросеть SpeechBrain настроена на устройство: {DEVICE.upper()}")

# --- ПАТЧ СОВМЕСТИМОСТИ ДЛЯ TORCH И SPEECHBRAIN ---

from services.speaker_model import get_speaker_model
from services.speaker_scoring import (
    clamp_score,
    is_match,
    legacy_percent_to_score,
    score_to_legacy_percent,
)

# Глобальные переменные моделей для воркеров многопроцессорности
_worker_speaker_model = None
_worker_shared_counter = None
_worker_lock = None
_worker_pause_event = None
_current_session_dir = None


def get_current_session_dir():
  global _current_session_dir
  return _current_session_dir



def init_worker(
    shared_counter=None,
    lock=None,
    pause_event=None,
    cancel_event=None,
):
  """Initialize SpeechBrain model and shared worker controls."""
  global _worker_speaker_model
  global _worker_shared_counter
  global _worker_lock
  global _worker_pause_event
  global _worker_cancel_event

  _worker_shared_counter = shared_counter
  _worker_lock = lock
  _worker_pause_event = pause_event
  _worker_cancel_event = cancel_event

  torch.set_num_threads(1)

  try:
    print(
        f"[{os.getpid()}] Loading SpeechBrain model on "
        f"{DEVICE.upper()}..."
    )
    _worker_speaker_model = get_speaker_model()
    print(
        f"[{os.getpid()}] SpeechBrain model loaded successfully."
    )
  except Exception as e:
    _worker_speaker_model = None
    print(
        f"[{os.getpid()}] SpeechBrain model initialization failed: "
        f"{e}"
    )


def process_audio_file(file_input):
  """Универсальная безопасная обработка аудио через локальный FFmpeg."""
  input_file_path = None
  output_wav_path = None
  try:
    if isinstance(file_input, str):
      if not os.path.exists(file_input):
        raise FileNotFoundError(f"Файл не найден: {file_input}")
      input_file_path = file_input
    elif isinstance(file_input, (bytes, bytearray)):
      with tempfile.NamedTemporaryFile(delete=False, suffix=".opus") as tmp:
        tmp.write(file_input)
        input_file_path = tmp.name
    elif hasattr(file_input, "read"):
      audio_bytes = file_input.read()
      suffix = ".opus"
      if hasattr(file_input, "filename") and file_input.filename:
        _, ext = os.path.splitext(file_input.filename)
        if ext:
          suffix = ext
      with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(audio_bytes)
        input_file_path = tmp.name
    else:
      raise ValueError("Неподдерживаемый тип входных данных для аудио")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_out:
      output_wav_path = tmp_out.name

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_file_path,
        "-ar",
        "16000",
        "-ac",
        "1",
        "-sample_fmt",
        "s16",
        output_wav_path,
    ]
    result = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if result.returncode != 0:
      raise RuntimeError(f"Ошибка FFmpeg (код {result.returncode}): {result.stderr}")

    with wave.open(output_wav_path, "rb") as wav_file:
      frames = wav_file.readframes(wav_file.getnframes())
      audio_data = (
          np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
      )

    tensor_waveform = torch.tensor(audio_data).unsqueeze(0)
    return tensor_waveform, output_wav_path
  except Exception as e:
    raise RuntimeError(f"Ошибка обработки аудио: {e}")


def process_candidate_worker(args):
  """Process one candidate with pause and cancellation support."""
  candidate_path, target_embedding_np, threshold = args

  global _worker_speaker_model
  global _worker_shared_counter
  global _worker_lock
  global _worker_pause_event
  global _worker_cancel_event

  candidate_wav_path = None
  was_cancelled = False

  try:
    if (
        _worker_cancel_event is not None
        and _worker_cancel_event.is_set()
    ):
      was_cancelled = True
      return (
          candidate_path,
          0.0,
          False,
          0.0,
          0.0,
          "__CANCELLED__",
      )

    if _worker_pause_event is not None:
      while _worker_pause_event.is_set():
        if (
            _worker_cancel_event is not None
            and _worker_cancel_event.is_set()
        ):
          was_cancelled = True
          return (
              candidate_path,
              0.0,
              False,
              0.0,
              0.0,
              "__CANCELLED__",
          )

        time.sleep(0.2)

    if _worker_speaker_model is None:
      raise RuntimeError(
          "SpeechBrain model is not initialized in worker"
      )

    _, candidate_wav_path = process_audio_file(
        candidate_path
    )

    if (
        _worker_cancel_event is not None
        and _worker_cancel_event.is_set()
    ):
      was_cancelled = True
      return (
          candidate_path,
          0.0,
          False,
          0.0,
          0.0,
          "__CANCELLED__",
      )

    max_file_score = -1.0
    best_start_seconds = 0.0
    best_end_seconds = 0.0

    target_embedding = torch.as_tensor(
        target_embedding_np,
        dtype=torch.float32,
        device=DEVICE,
    )

    with wave.open(candidate_wav_path, "rb") as wf:
      sr = wf.getframerate()
      frames = wf.readframes(wf.getnframes())

      audio_np = (
          np.frombuffer(
              frames,
              dtype=np.int16,
          ).astype(np.float32)
          / 32768.0
      )

    total_samples = len(audio_np)

    chunk_duration = 3.0
    step_duration = 1.5

    chunk_samples = int(chunk_duration * sr)
    step_samples = int(step_duration * sr)

    if total_samples <= chunk_samples * 1.5:

      if (
          _worker_cancel_event is not None
          and _worker_cancel_event.is_set()
      ):
        was_cancelled = True
        return (
            candidate_path,
            0.0,
            False,
            0.0,
            0.0,
            "__CANCELLED__",
        )

      candidate_waveform, extra_wav_path = (
          process_audio_file(candidate_path)
      )

      try:
        with torch.no_grad():
          candidate_embedding = (
              _worker_speaker_model.encode_batch(
                  candidate_waveform
              )
          )
      finally:
        if (
            extra_wav_path
            and os.path.exists(extra_wav_path)
        ):
          os.remove(extra_wav_path)

      cos_sim = F.cosine_similarity(
          target_embedding,
          candidate_embedding,
          dim=-1,
      ).item()

      max_file_score = clamp_score(
          cos_sim
      )

      best_start_seconds = 0.0
      best_end_seconds = round(
          total_samples / sr,
          3,
      )

    else:
      for start in range(
          0,
          total_samples - int(1.0 * sr),
          step_samples,
      ):

        if (
            _worker_cancel_event is not None
            and _worker_cancel_event.is_set()
        ):
          was_cancelled = True
          return (
              candidate_path,
              0.0,
              False,
              0.0,
              0.0,
              "__CANCELLED__",
          )

        end = min(
            start + chunk_samples,
            total_samples,
        )

        if (end - start) < int(1.0 * sr):
          break

        segment_np = audio_np[start:end]

        segment_tensor = (
            torch.tensor(segment_np)
            .unsqueeze(0)
        )

        with torch.no_grad():
          segment_embedding = (
              _worker_speaker_model.encode_batch(
                  segment_tensor
              )
          )

        cos_sim = F.cosine_similarity(
            target_embedding,
            segment_embedding,
            dim=-1,
        ).item()

        segment_score = clamp_score(
            cos_sim
        )

        if segment_score > max_file_score:
          max_file_score = segment_score

          best_start_seconds = round(
              start / sr,
              3,
          )

          best_end_seconds = round(
              end / sr,
              3,
          )

        if max_file_score >= 0.95:
          break

    threshold_score = (
        legacy_percent_to_score(
            threshold
        )
    )

    matched = is_match(
        max_file_score,
        threshold_score,
    )

    display_similarity = (
        score_to_legacy_percent(
            max_file_score
        )
    )

    return (
        candidate_path,
        display_similarity,
        matched,
        best_start_seconds,
        best_end_seconds,
        None,
    )

  except Exception as e:
    return (
        candidate_path,
        0.0,
        False,
        0.0,
        0.0,
        str(e),
    )

  finally:
    if (
        candidate_wav_path
        and os.path.exists(candidate_wav_path)
    ):
      try:
        os.remove(candidate_wav_path)
      except OSError:
        pass

    if (
        not was_cancelled
        and _worker_shared_counter is not None
        and _worker_lock is not None
    ):
      with _worker_lock:
        _worker_shared_counter.value += 1


def run_background_voice_search(
    file_path_or_data,
    folder_path,
    threshold=34.0,
    num_processes=2,
    shared_counter=None,
    lock=None,
    pause_event=None,
    cancel_event=None,
    results_dir=None,
):
  """Multiprocess voice search with cooperative cancellation."""
  global _current_session_dir

  session_dir = None

  try:
    results_root = "results"
    os.makedirs(results_root, exist_ok=True)

    if results_dir is not None:
      session_dir = os.path.abspath(
          results_dir
      )
    else:
      timestamp = datetime.now().strftime(
          "%Y%m%d_%H%M%S"
      )

      session_dir = os.path.abspath(
          os.path.join(
              results_root,
              f"session_{timestamp}",
          )
      )

    # Legacy compatibility only.
    # Real file operations below use local session_dir.
    _current_session_dir = session_dir

    os.makedirs(
        session_dir,
        exist_ok=True,
    )

    threshold = float(threshold)
    num_processes = int(num_processes)

    if shared_counter is not None:
      if lock is not None:
        with lock:
          shared_counter.value = 0
      else:
        shared_counter.value = 0

    if not os.path.exists(folder_path):
      raise FileNotFoundError(
          f"Search folder not found: {folder_path}"
      )

    info_path = os.path.join(
        session_dir,
        "search_info.txt",
    )

    with open(
        info_path,
        "w",
        encoding="utf-8",
    ) as f:
      f.write(
          f"Started: {datetime.now()}\n"
          f"Search folder: {folder_path}\n"
          f"Threshold: {threshold}%\n"
      )

    matched_files = []
    cancelled = False

    if (
        cancel_event is not None
        and cancel_event.is_set()
    ):
      cancelled = True
    else:
      print(
          f"Initializing target SpeechBrain model on "
          f"{DEVICE.upper()}..."
      )

      temp_model = get_speaker_model()

      target_waveform, target_wav_path = (
          process_audio_file(file_path_or_data)
      )

      try:
        with torch.no_grad():
          target_embedding = (
              temp_model.encode_batch(
                  target_waveform
              )
          )

        target_embedding_np = (
            target_embedding
            .detach()
            .cpu()
            .numpy()
        )
      finally:
        if (
            target_wav_path
            and os.path.exists(target_wav_path)
        ):
          os.remove(target_wav_path)

      supported_extensions = (
          ".wav",
          ".mp3",
          ".opus",
          ".ogg",
          ".flac",
          ".m4a",
          ".aac",
      )

      candidate_tasks = []

      for root, _, files in os.walk(folder_path):

        if (
            cancel_event is not None
            and cancel_event.is_set()
        ):
          cancelled = True
          break

        for file in files:
          if not file.lower().endswith(
              supported_extensions
          ):
            continue

          candidate_path = os.path.join(
              root,
              file,
          )

          if os.path.abspath(
              candidate_path
          ) == os.path.abspath(
              str(file_path_or_data)
          ):
            continue

          candidate_tasks.append(
              (
                  candidate_path,
                  target_embedding_np,
                  threshold,
              )
          )

      if not cancelled and candidate_tasks:

        with Pool(
            processes=num_processes,
            initializer=init_worker,
            initargs=(
                shared_counter,
                lock,
                pause_event,
                cancel_event,
            ),
        ) as pool:

          for (
              candidate_path,
              score,
              matched,
              match_start,
              match_end,
              err,
          ) in pool.imap_unordered(
              process_candidate_worker,
              candidate_tasks,
          ):

            if err == "__CANCELLED__":
              cancelled = True
              break

            file_name = os.path.basename(
                candidate_path
            )

            if err:
              print(
                  f"[VOICE SEARCH ERROR] "
                  f"{candidate_path}: {err}"
              )

            elif matched:
              audio_dir = os.path.join(
                  session_dir,
                  "audio",
              )

              os.makedirs(
                  audio_dir,
                  exist_ok=True,
              )

              source_path = os.path.abspath(
                  candidate_path
              )

              source_hash = hashlib.sha1(
                  source_path.encode(
                      "utf-8"
                  )
              ).hexdigest()[:12]

              stored_name = (
                  f"{source_hash}_{file_name}"
              )

              dest_path = os.path.join(
                  audio_dir,
                  stored_name,
              )

              shutil.copy2(
                  candidate_path,
                  dest_path,
              )

              match_start = round(
                  float(match_start),
                  3,
              )

              match_end = round(
                  float(match_end),
                  3,
              )

              match_duration = round(
                  max(
                      0.0,
                      match_end - match_start,
                  ),
                  3,
              )

              matched_files.append(
                  {
                      "file_name": file_name,
                      "source_path": source_path,
                      "stored_name": stored_name,
                      "similarity": float(score),
                      "start": match_start,
                      "end": match_end,
                      "duration": match_duration,
                  }
              )

              print(
                  f"Match found: {file_name} "
                  f"({score}%) "
                  f"at {match_start:.3f}-"
                  f"{match_end:.3f}s"
              )

            if (
                cancel_event is not None
                and cancel_event.is_set()
            ):
              cancelled = True
              break

      elif not cancelled and not candidate_tasks:
        print(
            "No supported audio files found "
            "in search folder."
        )

    report_path = os.path.join(
        session_dir,
        "report.txt",
    )

    with open(
        report_path,
        "w",
        encoding="utf-8",
    ) as f:

      f.write(
          "=== Voice search report ===\n"
      )

      f.write(
          f"Date: {datetime.now()}\n"
      )

      f.write(
          f"Search folder: {folder_path}\n"
      )

      f.write(
          f"Threshold: {threshold}%\n"
      )

      f.write(
          "Status: "
          + (
              "cancelled by user"
              if cancelled
              else "completed"
          )
          + "\n"
      )

      f.write(
          f"Matches: {len(matched_files)}\n\n"
      )

      for match in sorted(
          matched_files,
          key=lambda item: item["similarity"],
          reverse=True,
      ):
        f.write(
            f"- {match['file_name']} "
            f"(Similarity: "
            f"{match['similarity']}%, "
            f"Time: "
            f"{match['start']:.3f}-"
            f"{match['end']:.3f}s)\n"
        )

    finished_at = datetime.now()

    ordered_matches = sorted(
        matched_files,
        key=lambda item: item["similarity"],
        reverse=True,
    )

    result_metadata = {
        "version": 1,
        "status": (
            "cancelled"
            if cancelled
            else "completed"
        ),
        "search_folder": os.path.abspath(
            folder_path
        ),
        "threshold": float(threshold),
        "matches_count": len(
            ordered_matches
        ),
        "matches": ordered_matches,
        "finished_at": (
            finished_at.isoformat()
        ),
    }

    results_json_path = os.path.join(
        session_dir,
        "results.json",
    )

    with open(
        results_json_path,
        "w",
        encoding="utf-8",
    ) as f:
      json.dump(
          result_metadata,
          f,
          ensure_ascii=False,
          indent=2,
      )

    print(
        "Structured result metadata saved: "
        f"{results_json_path}"
    )

    if cancelled:
      print(
          "Voice search cancelled. "
          f"Saved matches: {len(matched_files)}"
      )
    else:
      print(
          "Voice search completed. "
          f"Matches: {len(matched_files)}"
      )

    return cancelled

  except Exception as e:
    error_msg = (
        "Critical error in "
        "run_background_voice_search: "
        f"{e}\n{traceback.format_exc()}"
    )

    print(error_msg)

    if session_dir:
      error_path = os.path.join(
          session_dir,
          "error_report.txt",
      )

      with open(
          error_path,
          "w",
          encoding="utf-8",
      ) as f:
        f.write(error_msg)

    raise