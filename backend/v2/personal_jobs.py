"""Bounded, temporary personal searches outside the HTTP request deadline.

Job identifiers are bearer capabilities. This module does not persist, log or
publish inputs. Cancellation stops result delivery; an already running search
must enforce its own network deadline and retains its worker slot until done.
"""

from copy import deepcopy
from dataclasses import dataclass
import math
import secrets
import threading
import time


_FAILURE_MESSAGE = "검색을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요."


class SearchBusy(RuntimeError):
    """The local search worker or temporary result capacity is exhausted."""


@dataclass
class _Job:
    created_at: float
    status: str = "pending"
    result: object = None


class SearchJobs:
    def __init__(self, search_fn, *, max_workers=2, max_jobs=32,
                 ttl_seconds=900, clock=time.monotonic):
        if not callable(search_fn) or not callable(clock):
            raise ValueError("search_fn and clock must be callable")
        for value, maximum, name in ((max_workers, 16, "max_workers"),
                                     (max_jobs, 1024, "max_jobs")):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be an integer from 1 to {maximum}")
        if type(ttl_seconds) not in (int, float) or not math.isfinite(ttl_seconds) or not 0 < ttl_seconds <= 86400:
            raise ValueError("ttl_seconds must be positive and at most 86400")
        self._search = search_fn
        self._clock = clock
        self._max_workers = max_workers
        self._max_jobs = max_jobs
        self._ttl = ttl_seconds
        self._jobs = {}
        self._active = 0
        self._closed = False
        self._condition = threading.Condition()
        self._reaper = None

    def _expire(self):
        now = self._clock()
        for job_id, job in tuple(self._jobs.items()):
            if now - job.created_at >= self._ttl:
                # The running worker may still hold this object, so discard
                # results in both the registry and that worker's reference.
                job.status = "expired"
                job.result = None
                del self._jobs[job_id]

    def _reap(self):
        with self._condition:
            while not self._closed:
                self._expire()
                if self._jobs:
                    deadline = min(job.created_at + self._ttl for job in self._jobs.values())
                    wait = max(0.01, deadline - self._clock())
                    self._condition.wait(wait)
                else:
                    # Stop the extra daemon when no records remain. A later
                    # start creates another, avoiding idle instance retention.
                    self._reaper = None
                    return

    def start(self, subject):
        if not isinstance(subject, dict):
            raise ValueError("subject must be an object")
        with self._condition:
            self._expire()
            if self._closed or self._active >= self._max_workers:
                raise SearchBusy("검색이 많습니다. 잠시 후 다시 시도해 주세요.")
            # Copy while the capacity lock is held, before reserving a slot.
            # A caller cannot mutate the input used by the search worker.
            copied_subject = deepcopy(subject)
            if len(self._jobs) >= self._max_jobs:
                terminal = [(key, job) for key, job in self._jobs.items()
                            if job.status in ("complete", "failed", "cancelled")]
                if not terminal:
                    raise SearchBusy("검색이 많습니다. 잠시 후 다시 시도해 주세요.")
                oldest_id, oldest = min(terminal, key=lambda item: item[1].created_at)
                # The TTL is a maximum retention period, not reserved capacity.
                # Never evict pending/running work or release its worker slot.
                # A cancelled worker still holds its slot until _run exits.
                oldest.result = None
                del self._jobs[oldest_id]
            job_id = secrets.token_urlsafe(24)
            while job_id in self._jobs:
                job_id = secrets.token_urlsafe(24)
            job = _Job(self._clock())
            self._jobs[job_id] = job
            self._active += 1
            worker = threading.Thread(target=self._run, args=(job_id, job, copied_subject),
                                      name="personal-search", daemon=True)
            try:
                if self._reaper is None:
                    self._reaper = threading.Thread(target=self._reap, name="personal-search-expiry", daemon=True)
                    self._reaper.start()
                worker.start()
            except Exception:
                self._jobs.pop(job_id, None)
                self._active -= 1
                if self._reaper is not None and not self._reaper.is_alive():
                    self._reaper = None
                self._condition.notify_all()
                raise SearchBusy("검색을 시작하지 못했습니다. 잠시 후 다시 시도해 주세요.") from None
            self._condition.notify_all()
            return {"job_id": job_id, "status": "pending"}

    def _run(self, job_id, job, subject):
        try:
            with self._condition:
                self._expire()
                if self._closed or self._jobs.get(job_id) is not job or job.status != "pending":
                    return
                job.status = "running"
            try:
                result = deepcopy(self._search(subject))
                status = "complete"
            except Exception:
                # Never expose exception text: transports can include URLs,
                # credentials, private addresses or fragments of page content.
                result, status = None, "failed"
            with self._condition:
                self._expire()
                if not self._closed and self._jobs.get(job_id) is job and job.status == "running":
                    job.result, job.status = result, status
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def get(self, job_id):
        if not isinstance(job_id, str):
            return None
        with self._condition:
            self._expire()
            job = self._jobs.get(job_id)
            if job is None:
                return None
            response = {"job_id": job_id, "status": job.status}
            if job.status == "complete":
                response["result"] = deepcopy(job.result)
            elif job.status == "failed":
                response["error"] = {"code": "search_failed", "message": _FAILURE_MESSAGE}
                response["message"] = _FAILURE_MESSAGE
            return response

    def cancel(self, job_id):
        if not isinstance(job_id, str):
            return False
        with self._condition:
            self._expire()
            job = self._jobs.get(job_id)
            if job is None or job.status not in ("pending", "running"):
                return False
            job.status, job.result = "cancelled", None
            self._condition.notify_all()
            return True

    def close(self):
        """Clear the registry without waiting for bounded, in-flight network I/O."""
        with self._condition:
            self._closed = True
            for job in self._jobs.values():
                job.status, job.result = "cancelled", None
            self._jobs.clear()
            self._condition.notify_all()
