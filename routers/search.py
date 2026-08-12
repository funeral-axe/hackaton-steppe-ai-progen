import multiprocessing
import os
import tempfile
import threading
import time
from urllib.parse import urlencode

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydub import AudioSegment
from auth_dependencies import get_current_user
from services.progress_manager import progress_manager
from services.voice_engine import run_background_voice_search

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def background_search_runner(
    target_wav_path, folder_path, threshold, workers_count
):
  audio_extensions = (".mp3", ".wav", ".opus", ".m4a", ".flac", ".aac")
  all_files = []
  if os.path.exists(folder_path) and os.path.isdir(folder_path):
    for root, dirs, files in os.walk(folder_path):
      for file in files:
        if file.lower().endswith(audio_extensions):
          all_files.append(os.path.join(root, file))

  total_files = len(all_files)
  progress_manager.start(total_files)

  shared_counter = multiprocessing.Value("i", 0)
  lock = multiprocessing.Lock()
  pause_event = multiprocessing.Event()
  search_done = threading.Event()

  def run_search():
    try:
      run_background_voice_search(
          file_path_or_data=target_wav_path,
          folder_path=folder_path,
          threshold=threshold,
          num_processes=workers_count,
          shared_counter=shared_counter,
          lock=lock,
          pause_event=pause_event,
      )
    except Exception as e:
      print(f"Ошибка в фоновой задаче поиска: {e}")
    finally:
      search_done.set()

  search_thread = threading.Thread(target=run_search)
  search_thread.start()

  while not search_done.is_set():
    current_state = progress_manager.get()
    if current_state["status"] == "paused":
      pause_event.set()
    elif current_state["status"] == "running":
      pause_event.clear()

    with lock:
      current_processed = shared_counter.value
    progress_manager.update(current_processed)
    time.sleep(0.3)

  with lock:
    current_processed = shared_counter.value
  progress_manager.update(current_processed)
  progress_manager.finish()

  if target_wav_path and os.path.exists(target_wav_path):
    try:
      os.remove(target_wav_path)
    except Exception:
      pass


@router.get("/results", response_class=HTMLResponse)
@router.get("/results/{full_path:path}", response_class=HTMLResponse)
async def browse_results(full_path: str = "", user=Depends(get_current_user)):
  if not user:
    return RedirectResponse(url="/login", status_code=303)

  base_dir = os.path.abspath("results")
  target_path = os.path.normpath(os.path.join(base_dir, full_path))

  if not target_path.startswith(base_dir):
    return HTMLResponse("<h3>Доступ запрещен</h3>", status_code=403)
  if not os.path.exists(target_path):
    return HTMLResponse("<h3>Путь или файл не найден</h3>", status_code=404)
  if os.path.isfile(target_path):
    return FileResponse(target_path)

  items = os.listdir(target_path)
  html_content = f"""
    <!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"><title>Результаты | SonAr</title>
    <style>body{{font-family:sans-serif;background:#f4f6f9;padding:20px;}}.container{{max-width:800px;margin:auto;background:white;padding:30px;border-radius:12px;box-shadow:0 4px 15px rgba(0,0,0,0.1);}}a{{color:#2a5298;text-decoration:none;}}</style>
    </head><body><div class="container">
    <a href="/voice_search">&larr; Назад к поиску</a>
    <h2>📂 Папка: /results/{full_path}</h2><ul>
    """
  if full_path:
    parent_dir = "/".join(full_path.split("/")[:-1])
    parent_url = f"/results/{parent_dir}" if parent_dir else "/results"
    html_content += f'<li><a href="{parent_url}">📁 <b>.. (Наверх)</b></a></li>'

  for item in sorted(items):
    item_path_rel = (
        f"{full_path}/{item}" if full_path else item
    ).replace("\\", "/")
    is_dir = os.path.isdir(os.path.join(target_path, item))
    html_content += (
        f'<li><a href="/results/{item_path_rel}">{"📁" if is_dir else "📄"} {item}</a></li>'
    )

  html_content += "</ul></div></body></html>"
  return HTMLResponse(content=html_content)


@router.post("/init_search")
async def init_search(
    search_date: str = Form(...),
    search_number: str = Form(...),
    search_type: str = Form(...),
    user=Depends(get_current_user),
):
  if not user:
    return RedirectResponse(url="/login", status_code=303)
  query_params = urlencode({"date": search_date, "number": search_number})
  return RedirectResponse(
      url=(
          f"/voice_search?{query_params}"
          if search_type == "voice"
          else "/"
      ),
      status_code=303,
  )


@router.get("/voice_search")
async def voice_search_page(
    request: Request,
    date: str = Query(default=None),
    number: str = Query(default=None),
    user=Depends(get_current_user),
):
  if not user:
    return RedirectResponse(url="/login", status_code=303)
  return templates.TemplateResponse(
      request=request,
      name="voice_search.html",
      context={"user": user, "date": date, "number": number},
  )


@router.post("/api/check_folder")
async def api_check_folder(
    folder_path: str = Form(...), user=Depends(get_current_user)
):
  if not user:
    return {"error": "Не авторизован"}
  if not os.path.exists(folder_path) or not os.path.isdir(folder_path):
    return {"error": "Папка не существует"}
  audio_extensions = (".mp3", ".wav", ".opus", ".m4a", ".flac", ".aac")
  count = sum(
      1
      for r, d, files in os.walk(folder_path)
      for f in files
      if f.lower().endswith(audio_extensions)
  )
  return {"total_files": count}


@router.get("/api/search_progress")
async def api_search_progress(user=Depends(get_current_user)):
  if not user:
    return {"error": "Не авторизован"}
  return progress_manager.get()


@router.post("/api/search_pause")
async def api_search_pause(user=Depends(get_current_user)):
  if not user:
    return {"error": "Не авторизован"}
  progress_manager.pause()
  return {"message": "Поиск приостановлен"}


@router.post("/api/search_resume")
async def api_search_resume(user=Depends(get_current_user)):
  if not user:
    return {"error": "Не авторизован"}
  progress_manager.resume()
  return {"message": "Поиск возобновлен"}


@router.post("/api/search_voice")
async def api_search_voice(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    start: float = Form(...),
    end: float = Form(...),
    folder_path: str = Form(...),
    threshold: float = Form(75.0),
    user=Depends(get_current_user),
):
  if not user:
    return {"error": "Не авторизован"}
  if not (2.9 <= (end - start) <= 5.1):
    return {"error": "Длина фрагмента должна быть от 3 до 5 секунд"}

  temp_input_path = None
  temp_target_path = None
  try:
    file_bytes = await audio.read()
    suffix = (
        os.path.splitext(audio.filename)[1]
        if audio.filename
        else ".opus"
    )
    fd, temp_input_path = tempfile.mkstemp(suffix=suffix or ".opus")
    os.close(fd)
    with open(temp_input_path, "wb") as f:
      f.write(file_bytes)

    original_audio = AudioSegment.from_file(temp_input_path)
    trimmed_audio = original_audio[int(start * 1000) : int(end * 1000)]

    fd, temp_target_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    trimmed_audio.set_frame_rate(16000).set_channels(1).export(
        temp_target_path, format="wav"
    )

    background_tasks.add_task(
        background_search_runner, temp_target_path, folder_path, threshold, 1
    )
    return {"message": "Поиск успешно запущен."}
  except Exception as e:
    if temp_target_path and os.path.exists(temp_target_path):
      os.remove(temp_target_path)
    return {"error": f"Ошибка обработки аудио: {str(e)}"}
  finally:
    if temp_input_path and os.path.exists(temp_input_path):
      os.remove(temp_input_path)