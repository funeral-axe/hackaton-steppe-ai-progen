import os
import threading
from services.voice_engine import get_current_session_dir


class SearchProgressManager:

  def __init__(self):
    self._lock = threading.Lock()
    self.state = {
        "status": "idle",
        "processed": 0,
        "total": 0,
        "current_file": "",
        "results_dir": None,
    }

  def start(self, total_files):
    with self._lock:
      self.state["status"] = "running"
      self.state["processed"] = 0
      self.state["total"] = total_files
      self.state["current_file"] = "Initializing search..."
      self.state["results_dir"] = None

  def pause(self):
    with self._lock:
      if self.state["status"] == "running":
        self.state["status"] = "paused"
        self.state["current_file"] = "Search paused by user"
        self.state["results_dir"] = get_current_session_dir()

  def resume(self):
    with self._lock:
      if self.state["status"] == "paused":
        self.state["status"] = "running"
        self.state["current_file"] = "Resuming search..."

  def request_cancel(self):
    with self._lock:
      if self.state["status"] not in {"running", "paused"}:
        return False

      self.state["status"] = "cancel_requested"
      self.state["current_file"] = "Stopping search..."
      self.state["results_dir"] = get_current_session_dir()
      return True

  def mark_cancelled(self, processed=None):
    with self._lock:
      if processed is not None:
        if self.state["total"] > 0:
          self.state["processed"] = min(
              processed,
              self.state["total"],
          )
        else:
          self.state["processed"] = processed

      self.state["status"] = "cancelled"
      self.state["current_file"] = "Search cancelled by user"
      self.state["results_dir"] = get_current_session_dir()

  def update(self, processed, current_file=""):
    with self._lock:
      if self.state["total"] > 0:
        self.state["processed"] = min(
            processed,
            self.state["total"],
        )
      else:
        self.state["processed"] = processed

      if current_file:
        self.state["current_file"] = current_file

      self.state["results_dir"] = get_current_session_dir()

  def finish(self):
    with self._lock:
      if self.state["status"] in {
          "cancel_requested",
          "cancelled",
      }:
        return

      self.state["status"] = "completed"
      self.state["processed"] = self.state["total"]
      self.state["current_file"] = "Done!"
      self.state["results_dir"] = get_current_session_dir()

  def get(self):
    with self._lock:
      state_copy = dict(self.state)

      if state_copy["results_dir"]:
        folder_name = os.path.basename(
            state_copy["results_dir"]
        )
        state_copy["results_url"] = (
            f"/results/{folder_name}/"
        )
      else:
        state_copy["results_url"] = ""

      return state_copy


# Глобальный экземпляр менеджера
progress_manager = SearchProgressManager()