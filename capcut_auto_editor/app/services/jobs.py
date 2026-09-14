"""백그라운드 작업 + SSE 진행률.

요청서 7절:
  - **중복 작업 방지** — 같은 세션의 같은 작업은 하나만. 두 번째 요청은
    진행 중인 작업에 다시 연결합니다. (버튼이 멈춘 것처럼 보이면 두 번 누릅니다)
  - **취소가 실제로 동작하게** — 하위 프로세스까지 종료하고 부분 파일을 정리합니다.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .logging_util import get_logger

log = get_logger(__name__)


@dataclass
class Job:
    id: str
    session_id: str
    kind: str
    title: str
    status: str = "running"          # running | done | failed | cancelled
    progress: float = 0.0
    label: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    version: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "progress": round(self.progress, 4),
            "label": self.label,
            "elapsed_sec": round((self.finished_at or time.time()) - self.started_at, 1),
            "result": self.result,
            "error": self.error,
            "warnings": self.warnings,
            "version": self.version,
        }


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: Dict[str, Job] = {}
        self._by_key: Dict[str, str] = {}
        self._condition = threading.Condition(self._lock)

    # ── 조회 ────────────────────────────────────────────────────────────
    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def find_running(self, session_id: str, kind: str) -> Optional[Job]:
        with self._lock:
            job_id = self._by_key.get(f"{session_id}:{kind}")
            job = self._jobs.get(job_id) if job_id else None
            return job if job and job.status == "running" else None

    def for_session(self, session_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [j.to_dict() for j in self._jobs.values() if j.session_id == session_id]

    # ── 실행 ────────────────────────────────────────────────────────────
    def submit(
        self,
        *,
        session_id: str,
        kind: str,
        title: str,
        target: Callable[["JobContext"], Dict[str, Any]],
    ) -> Job:
        key = f"{session_id}:{kind}"
        with self._lock:
            existing_id = self._by_key.get(key)
            existing = self._jobs.get(existing_id) if existing_id else None
            if existing is not None and existing.status == "running":
                # 두 번째 요청은 새 작업을 만들지 않고 진행 중인 작업에 다시 연결합니다.
                log.info("이미 진행 중인 작업에 다시 연결합니다: %s", title)
                return existing

            job = Job(id=uuid.uuid4().hex[:12], session_id=session_id, kind=kind, title=title)
            self._jobs[job.id] = job
            self._by_key[key] = job.id

        def _run() -> None:
            ctx = JobContext(self, job)
            try:
                result = target(ctx)
                with self._condition:
                    if job.cancel_event.is_set():
                        job.status = "cancelled"
                        job.label = "취소했습니다."
                    else:
                        job.status = "done"
                        job.progress = 1.0
                        job.result = result
                        job.label = result.get("label", "완료") if isinstance(result, dict) else "완료"
                    job.finished_at = time.time()
                    job.version += 1
                    self._condition.notify_all()
            except Exception as exc:  # noqa: BLE001 — 사용자에게 그대로 보여 줍니다
                message = str(exc) or exc.__class__.__name__
                cancelled = job.cancel_event.is_set() or exc.__class__.__name__ == "CancelledError"
                log.error("작업 실패 [%s] %s", job.kind, message)
                if not cancelled:
                    log.debug("%s", traceback.format_exc())
                with self._condition:
                    job.status = "cancelled" if cancelled else "failed"
                    job.error = None if cancelled else message
                    job.label = "취소했습니다." if cancelled else "실패했습니다."
                    job.finished_at = time.time()
                    job.version += 1
                    self._condition.notify_all()

        threading.Thread(target=_run, name=f"job-{kind}", daemon=True).start()
        return job

    def cancel(self, job_id: str) -> bool:
        with self._condition:
            job = self._jobs.get(job_id)
            if job is None or job.status != "running":
                return False
            job.cancel_event.set()
            job.label = "취소 중… 실행 중인 프로세스를 정리하고 있습니다."
            job.version += 1
            self._condition.notify_all()
        return True

    def update(self, job: Job, *, progress: Optional[float] = None, label: Optional[str] = None) -> None:
        with self._condition:
            if progress is not None:
                job.progress = max(0.0, min(1.0, progress))
            if label is not None:
                job.label = label
            job.version += 1
            self._condition.notify_all()

    def wait_for_change(self, job: Job, last_version: int, timeout: float = 20.0) -> Job:
        with self._condition:
            self._condition.wait_for(lambda: job.version != last_version, timeout=timeout)
            return job

    def prune(self, max_age_sec: float = 3600.0) -> int:
        cutoff = time.time() - max_age_sec
        removed = 0
        with self._lock:
            for job_id, job in list(self._jobs.items()):
                if job.status != "running" and (job.finished_at or 0) < cutoff:
                    self._jobs.pop(job_id, None)
                    removed += 1
            self._by_key = {
                key: job_id for key, job_id in self._by_key.items() if job_id in self._jobs
            }
        return removed


class JobContext:
    """작업 함수에 넘기는 핸들. 진행률 보고와 취소 확인에 씁니다."""

    def __init__(self, manager: JobManager, job: Job):
        self._manager = manager
        self.job = job

    @property
    def cancel(self) -> threading.Event:
        return self.job.cancel_event

    def progress(self, ratio: float, label: str = "") -> None:
        self._manager.update(self.job, progress=ratio, label=label or self.job.label)

    def stage(self, base: float, span: float) -> Callable[[float, str], None]:
        """하위 작업의 0~1 진행률을 전체 진행률의 일부 구간으로 환산합니다."""

        def _report(ratio: float, label: str = "") -> None:
            self.progress(base + span * max(0.0, min(1.0, ratio)), label)

        return _report

    def warn(self, message: str) -> None:
        with self._manager._condition:  # noqa: SLF001
            if message not in self.job.warnings:
                self.job.warnings.append(message)
            self.job.version += 1
            self._manager._condition.notify_all()  # noqa: SLF001
        log.warning(message)

    def raise_if_cancelled(self) -> None:
        if self.job.cancel_event.is_set():
            from .audio_analysis import CancelledError

            raise CancelledError("사용자가 작업을 취소했습니다.")


MANAGER = JobManager()


def sse_stream(job_id: str):
    """SSE 제너레이터. 진행률과 현재 항목명을 실시간으로 보냅니다."""
    job = MANAGER.get(job_id)
    if job is None:
        yield f"data: {json.dumps({'error': '작업을 찾을 수 없습니다.'}, ensure_ascii=False)}\n\n"
        return

    last_version = -1
    while True:
        payload = job.to_dict()
        if job.version != last_version:
            last_version = job.version
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        if job.status != "running":
            break
        MANAGER.wait_for_change(job, last_version, timeout=15.0)
        if job.version == last_version:
            yield ": keep-alive\n\n"  # 프록시가 끊지 않도록
