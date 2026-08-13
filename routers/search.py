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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydub import AudioSegment
from auth_dependencies import get_current_user
from auth_permissions import has_role, role_home
from database import UserRole
from services.progress_manager import progress_manager
from services.voice_engine import run_background_voice_search
from services.voice_jobs import voice_job_manager

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def background_search_runner(
    target_wav_path,
    folder_path,
    threshold,
    workers_count,
    job_id=None,
):
  job = (
      voice_job_manager.get(job_id)
      if job_id
      else None
  )

  if job_id and job is None:
    print(
        "[VOICE JOB WARNING] "
        f"Job not found: {job_id}. "
        "Continuing through legacy progress manager."
    )

  job_results_dir = (
      os.path.abspath(
          os.path.join(
              "results",
              f"job_{job.job_id}",
          )
      )
      if job is not None
      else None
  )

  audio_extensions = (
      ".mp3",
      ".wav",
      ".opus",
      ".m4a",
      ".flac",
      ".aac",
  )

  all_files = []

  if (
      os.path.exists(folder_path)
      and os.path.isdir(folder_path)
  ):
    for root, _, files in os.walk(folder_path):
      for file in files:
        if file.lower().endswith(
            audio_extensions
        ):
          all_files.append(
              os.path.join(root, file)
          )

  total_files = len(all_files)

  # Legacy state remains active while the frontend
  # is being migrated to job-scoped endpoints.
  progress_manager.start(total_files)

  # Remember the legacy state only to detect real transitions
  # coming from the old frontend endpoints.
  legacy_status_seen = progress_manager.get()["status"]

  # New independent state.
  if job is not None:
    started = job.start(total_files)

    if not started:
      print(
          "[VOICE JOB WARNING] "
          f"Could not start job {job.job_id}; "
          f"current status={job.snapshot()['status']}"
      )

  shared_counter = multiprocessing.Value(
      "i",
      0,
  )

  lock = multiprocessing.Lock()
  pause_event = multiprocessing.Event()
  cancel_event = multiprocessing.Event()
  search_done = threading.Event()

  if job is not None:
    job.attach_controls(
        pause_event,
        cancel_event,
    )

  search_result = {
      "cancelled": False,
      "error": None,
  }

  def sync_job_from_legacy(
      processed=None,
  ):
    if job is None:
      return

    legacy_state = progress_manager.get()

    job.update(
        processed=(
            processed
            if processed is not None
            else legacy_state["processed"]
        ),
        current_file=legacy_state[
            "current_file"
        ],
        results_dir=job_results_dir,
    )

  def sync_job_control_state(
      legacy_status,
  ):
    if job is None:
      return

    job_status = job.snapshot()["status"]

    if legacy_status == "cancel_requested":
      if job_status in {
          "queued",
          "running",
          "paused",
      }:
        job.request_cancel()

    elif legacy_status == "paused":
      if job_status == "running":
        job.pause()

    elif legacy_status == "running":
      if job_status == "paused":
        job.resume()

  def run_search():
    try:
      search_result["cancelled"] = (
          run_background_voice_search(
              file_path_or_data=target_wav_path,
              folder_path=folder_path,
              threshold=threshold,
              num_processes=workers_count,
              shared_counter=shared_counter,
              lock=lock,
              pause_event=pause_event,
              cancel_event=cancel_event,
              results_dir=job_results_dir,
          )
      )

    except Exception as e:
      search_result["error"] = str(e)

      print(
          f"Voice background search error: {e}"
      )

    finally:
      search_done.set()

  search_thread = threading.Thread(
      target=run_search
  )

  search_thread.start()

  while not search_done.is_set():

    current_state = progress_manager.get()
    status = current_state["status"]

    if job is not None:
      # For job-aware searches the job itself owns the runtime
      # events. The legacy manager is observed only when its
      # status actually changes because the old frontend still
      # uses /api/search_pause|resume|cancel.
      if status != legacy_status_seen:
        sync_job_control_state(status)
        legacy_status_seen = status

    else:
      # Pure legacy fallback for searches without job_id.
      if status == "cancel_requested":
        pause_event.clear()
        cancel_event.set()

      elif status == "paused":
        pause_event.set()

      elif status == "running":
        pause_event.clear()

    with lock:
      current_processed = (
          shared_counter.value
      )

    progress_manager.update(
        current_processed
    )

    sync_job_from_legacy(
        current_processed
    )

    time.sleep(0.3)

  with lock:
    current_processed = shared_counter.value

  progress_manager.update(
      current_processed
  )

  sync_job_from_legacy(
      current_processed
  )

  final_state = progress_manager.get()

  if (
      search_result["cancelled"]
      or final_state["status"]
      == "cancel_requested"
  ):
    progress_manager.mark_cancelled(
        current_processed
    )

    sync_job_from_legacy(
        current_processed
    )

    if job is not None:
      job.mark_cancelled()

  elif search_result["error"] is not None:
    # Keep legacy behavior untouched during migration.
    progress_manager.finish()

    sync_job_from_legacy(
        current_processed
    )

    if job is not None:
      job.fail(
          search_result["error"]
      )

  else:
    progress_manager.finish()

    sync_job_from_legacy()

    if job is not None:
      job.finish()

  if job is not None:
    job.detach_controls()

  if (
      target_wav_path
      and os.path.exists(target_wav_path)
  ):
    try:
      os.remove(target_wav_path)
    except OSError:
      pass


@router.get("/results", response_class=HTMLResponse)
@router.get("/results/{full_path:path}", response_class=HTMLResponse)
async def browse_results(full_path: str = "", user=Depends(get_current_user)):
  if not user:
    return RedirectResponse(url="/login", status_code=303)

  if not has_role(user, UserRole.USER.value):
    return RedirectResponse(
        url=role_home(user),
        status_code=303,
    )

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

  if not has_role(user, UserRole.USER.value):
    return RedirectResponse(
        url=role_home(user),
        status_code=303,
    )
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

  if not has_role(user, UserRole.USER.value):
    return RedirectResponse(
        url=role_home(user),
        status_code=303,
    )
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
    return JSONResponse(
        status_code=401,
        content={"detail": "Unauthorized"},
    )

  if not has_role(user, UserRole.USER.value):
    return JSONResponse(
        status_code=403,
        content={"detail": "Forbidden"},
    )
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
    return JSONResponse(
        status_code=401,
        content={"detail": "Unauthorized"},
    )

  if not has_role(user, UserRole.USER.value):
    return JSONResponse(
        status_code=403,
        content={"detail": "Forbidden"},
    )
  return progress_manager.get()


@router.get("/api/search_progress/{job_id}")
async def api_search_progress_by_job(
    job_id: str,
    user=Depends(get_current_user),
):
  if not user:
    return JSONResponse(
        status_code=401,
        content={"detail": "Unauthorized"},
    )

  if not has_role(
      user,
      UserRole.USER.value,
  ):
    return JSONResponse(
        status_code=403,
        content={"detail": "Forbidden"},
    )

  job = voice_job_manager.get(job_id)

  if job is None:
    return JSONResponse(
        status_code=404,
        content={"detail": "Voice search job not found"},
    )

  if job.owner_user_id != user.id:
    return JSONResponse(
        status_code=403,
        content={"detail": "Forbidden"},
    )

  return job.snapshot()


@router.post("/api/search_pause/{job_id}")
async def api_search_pause_by_job(
    job_id: str,
    user=Depends(get_current_user),
):
  if not user:
    return JSONResponse(
        status_code=401,
        content={"detail": "Unauthorized"},
    )

  if not has_role(
      user,
      UserRole.USER.value,
  ):
    return JSONResponse(
        status_code=403,
        content={"detail": "Forbidden"},
    )

  job = voice_job_manager.get(job_id)

  if job is None:
    return JSONResponse(
        status_code=404,
        content={"detail": "Voice search job not found"},
    )

  if job.owner_user_id != user.id:
    return JSONResponse(
        status_code=403,
        content={"detail": "Forbidden"},
    )

  if not job.pause():
    return JSONResponse(
        status_code=409,
        content={
            "detail": (
                "Voice search job cannot be paused "
                f"from status {job.snapshot()['status']}"
            )
        },
    )

  return {
      "message": "Voice search paused",
      "job_id": job.job_id,
      "status": job.snapshot()["status"],
  }


@router.post("/api/search_resume/{job_id}")
async def api_search_resume_by_job(
    job_id: str,
    user=Depends(get_current_user),
):
  if not user:
    return JSONResponse(
        status_code=401,
        content={"detail": "Unauthorized"},
    )

  if not has_role(
      user,
      UserRole.USER.value,
  ):
    return JSONResponse(
        status_code=403,
        content={"detail": "Forbidden"},
    )

  job = voice_job_manager.get(job_id)

  if job is None:
    return JSONResponse(
        status_code=404,
        content={"detail": "Voice search job not found"},
    )

  if job.owner_user_id != user.id:
    return JSONResponse(
        status_code=403,
        content={"detail": "Forbidden"},
    )

  if not job.resume():
    return JSONResponse(
        status_code=409,
        content={
            "detail": (
                "Voice search job cannot be resumed "
                f"from status {job.snapshot()['status']}"
            )
        },
    )

  return {
      "message": "Voice search resumed",
      "job_id": job.job_id,
      "status": job.snapshot()["status"],
  }


@router.post("/api/search_cancel/{job_id}")
async def api_search_cancel_by_job(
    job_id: str,
    user=Depends(get_current_user),
):
  if not user:
    return JSONResponse(
        status_code=401,
        content={"detail": "Unauthorized"},
    )

  if not has_role(
      user,
      UserRole.USER.value,
  ):
    return JSONResponse(
        status_code=403,
        content={"detail": "Forbidden"},
    )

  job = voice_job_manager.get(job_id)

  if job is None:
    return JSONResponse(
        status_code=404,
        content={"detail": "Voice search job not found"},
    )

  if job.owner_user_id != user.id:
    return JSONResponse(
        status_code=403,
        content={"detail": "Forbidden"},
    )

  if not job.request_cancel():
    return JSONResponse(
        status_code=409,
        content={
            "detail": (
                "Voice search job cannot be cancelled "
                f"from status {job.snapshot()['status']}"
            )
        },
    )

  return {
      "message": "Voice search cancellation requested",
      "job_id": job.job_id,
      "status": job.snapshot()["status"],
  }


@router.post("/api/search_pause")
async def api_search_pause(user=Depends(get_current_user)):
  if not user:
    return JSONResponse(
        status_code=401,
        content={"detail": "Unauthorized"},
    )

  if not has_role(user, UserRole.USER.value):
    return JSONResponse(
        status_code=403,
        content={"detail": "Forbidden"},
    )
  progress_manager.pause()
  return {"message": "Поиск приостановлен"}


@router.post("/api/search_resume")
async def api_search_resume(user=Depends(get_current_user)):
  if not user:
    return JSONResponse(
        status_code=401,
        content={"detail": "Unauthorized"},
    )

  if not has_role(user, UserRole.USER.value):
    return JSONResponse(
        status_code=403,
        content={"detail": "Forbidden"},
    )
  progress_manager.resume()
  return {"message": "Поиск возобновлен"}


@router.post("/api/search_cancel")
async def api_search_cancel(
    user=Depends(get_current_user),
):
  if not user:
    return JSONResponse(
        status_code=401,
        content={"detail": "Unauthorized"},
    )

  if not has_role(
      user,
      UserRole.USER.value,
  ):
    return JSONResponse(
        status_code=403,
        content={"detail": "Forbidden"},
    )

  accepted = progress_manager.request_cancel()

  if not accepted:
    return JSONResponse(
        status_code=409,
        content={
            "detail": "No active search to cancel"
        },
    )

  return {
      "message": "Cancellation requested"
  }


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
    return JSONResponse(
        status_code=401,
        content={"detail": "Unauthorized"},
    )

  if not has_role(user, UserRole.USER.value):
    return JSONResponse(
        status_code=403,
        content={"detail": "Forbidden"},
    )
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

    job = voice_job_manager.create(
        owner_user_id=user.id
    )

    background_tasks.add_task(
        background_search_runner,
        temp_target_path,
        folder_path,
        threshold,
        1,
        job.job_id,
    )
    return {
        "message": "\u041f\u043e\u0438\u0441\u043a \u0443\u0441\u043f\u0435\u0448\u043d\u043e \u0437\u0430\u043f\u0443\u0449\u0435\u043d.",
        "job_id": job.job_id,
    }
  except Exception as e:
    if temp_target_path and os.path.exists(temp_target_path):
      os.remove(temp_target_path)
    return {"error": f"Ошибка обработки аудио: {str(e)}"}
  finally:
    if temp_input_path and os.path.exists(temp_input_path):
      os.remove(temp_input_path)