import os
import threading
import uuid

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


ACTIVE_STATUSES = {
    "queued",
    "running",
    "paused",
    "cancel_requested",
}

FINAL_STATUSES = {
    "completed",
    "cancelled",
    "error",
}


def utc_now():
    return datetime.now(timezone.utc)


@dataclass
class VoiceSearchJob:
    job_id: str
    owner_user_id: int

    status: str = "queued"

    processed: int = 0
    total: int = 0

    current_file: str = ""
    results_dir: Optional[str] = None
    error: Optional[str] = None

    created_at: datetime = field(
        default_factory=utc_now
    )
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    _pause_event: Optional[Any] = field(
        default=None,
        repr=False,
        compare=False,
    )

    _cancel_event: Optional[Any] = field(
        default=None,
        repr=False,
        compare=False,
    )

    _lock: threading.RLock = field(
        default_factory=threading.RLock,
        repr=False,
        compare=False,
    )

    def snapshot(self):
        with self._lock:
            results_url = ""

            if self.results_dir:
                folder_name = os.path.basename(
                    self.results_dir
                )

                results_url = (
                    f"/results/{folder_name}/"
                )

            return {
                "job_id": self.job_id,
                "owner_user_id": self.owner_user_id,
                "status": self.status,
                "processed": self.processed,
                "total": self.total,
                "current_file": self.current_file,
                "results_dir": self.results_dir,
                "results_url": results_url,
                "error": self.error,
                "created_at": self.created_at.isoformat(),
                "started_at": (
                    self.started_at.isoformat()
                    if self.started_at
                    else None
                ),
                "finished_at": (
                    self.finished_at.isoformat()
                    if self.finished_at
                    else None
                ),
            }

    def attach_controls(
        self,
        pause_event,
        cancel_event,
    ):
        with self._lock:
            self._pause_event = pause_event
            self._cancel_event = cancel_event

            if self.status == "paused":
                self._pause_event.set()
            else:
                self._pause_event.clear()

            if self.status == "cancel_requested":
                self._pause_event.clear()
                self._cancel_event.set()
            else:
                self._cancel_event.clear()

        return True

    def detach_controls(self):
        with self._lock:
            self._pause_event = None
            self._cancel_event = None

    def controls_attached(self):
        with self._lock:
            return (
                self._pause_event is not None
                and self._cancel_event is not None
            )

    def start(self, total):
        with self._lock:
            if self.status != "queued":
                return False

            self.status = "running"
            self.processed = 0
            self.total = max(0, int(total))
            self.current_file = (
                "Initializing search..."
            )
            self.error = None
            self.started_at = utc_now()
            self.finished_at = None

            return True

    def update(
        self,
        processed=None,
        current_file=None,
        results_dir=None,
    ):
        with self._lock:
            if processed is not None:
                processed = max(
                    0,
                    int(processed),
                )

                if self.total > 0:
                    processed = min(
                        processed,
                        self.total,
                    )

                self.processed = processed

            if current_file is not None:
                self.current_file = (
                    current_file
                )

            if results_dir is not None:
                self.results_dir = (
                    results_dir
                )

    def pause(self):
        with self._lock:
            if self.status != "running":
                return False

            self.status = "paused"
            self.current_file = (
                "Search paused by user"
            )

            if self._pause_event is not None:
                self._pause_event.set()

            return True

    def resume(self):
        with self._lock:
            if self.status != "paused":
                return False

            self.status = "running"
            self.current_file = (
                "Resuming search..."
            )

            if self._pause_event is not None:
                self._pause_event.clear()

            return True

    def request_cancel(self):
        with self._lock:
            if self.status not in {
                "running",
                "paused",
                "queued",
            }:
                return False

            self.status = "cancel_requested"
            self.current_file = (
                "Stopping search..."
            )

            if self._pause_event is not None:
                self._pause_event.clear()

            if self._cancel_event is not None:
                self._cancel_event.set()

            return True

    def mark_cancelled(self):
        with self._lock:
            if self.status in FINAL_STATUSES:
                return False

            self.status = "cancelled"
            self.current_file = (
                "Search cancelled by user"
            )
            self.finished_at = utc_now()

            if self._pause_event is not None:
                self._pause_event.clear()

            if self._cancel_event is not None:
                self._cancel_event.set()

            return True

    def finish(self):
        with self._lock:
            if self.status in {
                "cancel_requested",
                "cancelled",
                "error",
            }:
                return False

            self.status = "completed"

            if self.total > 0:
                self.processed = self.total

            self.current_file = "Done!"
            self.finished_at = utc_now()

            if self._pause_event is not None:
                self._pause_event.clear()

            if self._cancel_event is not None:
                self._cancel_event.clear()

            return True

    def fail(self, error):
        with self._lock:
            self.status = "error"
            self.error = str(error)
            self.current_file = (
                "Search failed"
            )
            self.finished_at = utc_now()

            if self._pause_event is not None:
                self._pause_event.clear()

            if self._cancel_event is not None:
                self._cancel_event.clear()


class VoiceSearchJobManager:

    def __init__(self):
        self._lock = threading.RLock()
        self._jobs: Dict[
            str,
            VoiceSearchJob,
        ] = {}

    def create(self, owner_user_id):
        owner_user_id = int(
            owner_user_id
        )

        job = VoiceSearchJob(
            job_id=uuid.uuid4().hex,
            owner_user_id=owner_user_id,
        )

        with self._lock:
            self._jobs[job.job_id] = job

        return job

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def get_for_user(
        self,
        job_id,
        owner_user_id,
    ):
        job = self.get(job_id)

        if job is None:
            return None

        if (
            job.owner_user_id
            != int(owner_user_id)
        ):
            return None

        return job

    def owns(
        self,
        job_id,
        owner_user_id,
    ):
        return (
            self.get_for_user(
                job_id,
                owner_user_id,
            )
            is not None
        )

    def remove(
        self,
        job_id,
        owner_user_id=None,
    ):
        with self._lock:
            job = self._jobs.get(job_id)

            if job is None:
                return False

            if (
                owner_user_id is not None
                and job.owner_user_id
                != int(owner_user_id)
            ):
                return False

            del self._jobs[job_id]

            return True

    def list_for_user(
        self,
        owner_user_id,
    ):
        owner_user_id = int(
            owner_user_id
        )

        with self._lock:
            jobs = [
                job
                for job in self._jobs.values()
                if job.owner_user_id
                == owner_user_id
            ]

        return sorted(
            jobs,
            key=lambda job: job.created_at,
            reverse=True,
        )


voice_job_manager = VoiceSearchJobManager()
