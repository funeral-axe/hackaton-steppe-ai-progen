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

# --- СТРОГИЙ ОФФЛАЙН РЕЖИМ ---
# Запрещаем библиотекам любые сетевые запросы (работаем только с локальным кэшем)

# --- АВТООПРЕДЕЛЕНИЕ УСТРОЙСТВА ---
# SpeechBrain поддерживает cuda и cpu. На Mac используется CPU, на боевом ПК с NVIDIA — CUDA.
print(f"🚀 Нейросеть SpeechBrain настроена на устройство: {DEVICE.upper()}")

# --- ПАТЧ СОВМЕСТИМОСТИ ДЛЯ TORCH И SPEECHBRAIN ---

from services.speaker_model import get_speaker_model

# Глобальные переменные моделей для воркеров многопроцессорности
_worker_speaker_model = None
_worker_shared_counter = None
_worker_lock = None
_worker_pause_event = None
_current_session_dir = None


def get_current_session_dir():
  global _current_session_dir
  return _current_session_dir



def init_worker(shared_counter=None, lock=None, pause_event=None):
  """Инициализирует локальную модель SpeechBrain внутри каждого отдельного процесса-воркера."""
  global _worker_speaker_model, _worker_shared_counter, _worker_lock, _worker_pause_event
  _worker_shared_counter = shared_counter
  _worker_lock = lock
  _worker_pause_event = pause_event

  torch.set_num_threads(1)

  try:
    print(
        f"[{os.getpid()}] Загрузка локальной модели SpeechBrain на {DEVICE.upper()}..."
    )
    model = get_speaker_model()
    _worker_speaker_model = model
    print(f"[{os.getpid()}] Модель SpeechBrain успешно загружена.")
  except Exception as e:
    print(
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
  """Обработка файла-кандидата с локальным скользящим окном для поиска голоса среди нескольких спикеров."""
  candidate_path, target_embedding_np, threshold = args
  global _worker_speaker_model, _worker_shared_counter, _worker_lock, _worker_pause_event

  if _worker_pause_event is not None:
    while _worker_pause_event.is_set():
      time.sleep(0.5)

  try:
    if _worker_speaker_model is None:
      raise RuntimeError("Модель SpeechBrain не инициализирована в воркере!")

    _, candidate_wav_path = process_audio_file(candidate_path)
    max_file_similarity = 0.0
    target_embedding = torch.as_tensor(target_embedding_np, dtype=torch.float32, device=DEVICE)

    with wave.open(candidate_wav_path, "rb") as wf:
      sr = wf.getframerate()
      frames = wf.readframes(wf.getnframes())
      audio_np = (
          np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
      )

    total_samples = len(audio_np)
    chunk_duration = 3.0
    step_duration = 1.5
    chunk_samples = int(chunk_duration * sr)
    step_samples = int(step_duration * sr)

    if total_samples <= chunk_samples * 1.5:
      candidate_waveform, _ = process_audio_file(candidate_path)
      with torch.no_grad():
        candidate_embedding = _worker_speaker_model.encode_batch(candidate_waveform)
      cos_sim = F.cosine_similarity(
          target_embedding, candidate_embedding, dim=-1
      ).item()
      max_file_similarity = round(max(0.0, cos_sim) * 100, 2)
    else:
      for start in range(0, total_samples - int(1.0 * sr), step_samples):
        end = min(start + chunk_samples, total_samples)
        if (end - start) < int(1.0 * sr):
          break

        segment_np = audio_np[start:end]
        segment_tensor = torch.tensor(segment_np).unsqueeze(0)

        with torch.no_grad():
          segment_embedding = _worker_speaker_model.encode_batch(segment_tensor)

        cos_sim = F.cosine_similarity(
            target_embedding, segment_embedding, dim=-1
        ).item()
        similarity = round(max(0.0, cos_sim) * 100, 2)

        if similarity > max_file_similarity:
          max_file_similarity = similarity

        if max_file_similarity >= 95.0:
          break

    if candidate_wav_path and os.path.exists(candidate_wav_path):
      os.remove(candidate_wav_path)

    matched = max_file_similarity >= threshold
    return (candidate_path, max_file_similarity, matched, None)

  except Exception as e:
    return (candidate_path, 0.0, False, str(e))
  finally:
    if _worker_shared_counter is not None and _worker_lock is not None:
      with _worker_lock:
        _worker_shared_counter.value += 1


def run_background_voice_search(
    file_path_or_data,
    folder_path,
    threshold=75.0,
    num_processes=2,
    shared_counter=None,
    lock=None,
    pause_event=None,
):
  """Многопроцессорный поиск с локальным скользящим окном."""
  global _current_session_dir
  try:
    threshold = float(threshold)
    num_processes = int(num_processes)

    if shared_counter is not None:
      if lock is not None:
        with lock:
          shared_counter.value = 0
      else:
        shared_counter.value = 0

    if not os.path.exists(folder_path):
      raise FileNotFoundError(f"Указанная папка не найдена: {folder_path}")

    results_root = "results"
    os.makedirs(results_root, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _current_session_dir = os.path.abspath(
        os.path.join(results_root, f"session_{timestamp}")
    )
    os.makedirs(_current_session_dir, exist_ok=True)

    info_path = os.path.join(_current_session_dir, "search_info.txt")
    with open(info_path, "w", encoding="utf-8") as f:
      f.write(
          f"Дата запуска: {datetime.now()}\nПапка сканирования:"
          f" {folder_path}\nПорог: {threshold}%\n"
      )

    print(f"Инициализация целевой модели SpeechBrain на {DEVICE.upper()}...")
    temp_model = get_speaker_model()

    target_waveform, target_wav_path = process_audio_file(file_path_or_data)
    with torch.no_grad():
      target_embedding = temp_model.encode_batch(target_waveform)
    target_embedding_np = target_embedding.cpu().numpy()

    if target_wav_path and os.path.exists(target_wav_path):
      os.remove(target_wav_path)

    supported_extensions = (".wav", ".mp3", ".opus", ".ogg", ".flac", ".m4a")
    candidate_tasks = []

    for root, _, files in os.walk(folder_path):
      for file in files:
        if file.lower().endswith(supported_extensions):
          candidate_path = os.path.join(root, file)
          if os.path.abspath(candidate_path) == os.path.abspath(
              str(file_path_or_data)
          ):
            continue
          candidate_tasks.append(
              (candidate_path, target_embedding_np, threshold)
          )

    if not candidate_tasks:
      empty_report = os.path.join(_current_session_dir, "report.txt")
      with open(empty_report, "w", encoding="utf-8") as f:
        f.write("В указанной папке не найдено подходящих аудиофайлов.")
      return

    matched_files = []

    with Pool(
        processes=num_processes,
        initializer=init_worker,
        initargs=(shared_counter, lock, pause_event),
    ) as pool:
      for candidate_path, score, matched, err in pool.imap_unordered(
          process_candidate_worker, candidate_tasks
      ):
        file_name = os.path.basename(candidate_path)
        if err:
          print(f"[VOICE SEARCH ERROR] {candidate_path}: {err}")
          continue
        if matched:
          dest_path = os.path.join(_current_session_dir, file_name)
          shutil.copy2(candidate_path, dest_path)
          matched_files.append((file_name, score))
          print(
              f"✅ Найдено совпадение: {file_name} ({score}%) -> скопировано в"
              f" {_current_session_dir}"
          )

    report_path = os.path.join(_current_session_dir, "report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
      f.write("=== Отчет о многопроцессорном голосовом поиске ===\n")
      f.write(f"Дата и время: {datetime.now()}\n")
      f.write(f"Сканируемая папка: {folder_path}\n")
      f.write(f"Порог сходства: {threshold}%\n")
      f.write(f"Всего найдено совпадений: {len(matched_files)}\n\n")
      f.write("Список найденных файлов:\n")
      for fname, score in matched_files:
        f.write(f"- {fname} (Сходство: {score}%)\n")

    print(
        "Многопроцессорный поиск успешно завершен. Найдено совпадений:"
        f" {len(matched_files)}"
    )

  except Exception as e:
    error_msg = (
        f"Критическая ошибка в run_background_voice_search:"
        f" {e}\n{traceback.format_exc()}"
    )
    print(error_msg)
    if _current_session_dir:
      error_path = os.path.join(_current_session_dir, "error_report.txt")
      with open(error_path, "w", encoding="utf-8") as f:
        f.write(error_msg)
    raise e