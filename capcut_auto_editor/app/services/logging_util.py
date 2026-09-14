"""날짜별 파일 로그 + UI 로그 패널용 링 버퍼."""

from __future__ import annotations

import logging
import sys
import threading
from collections import deque
from datetime import datetime
from typing import Any, Deque, Dict, List

from ..config import LOG_DIR

_LOCK = threading.Lock()
_BUFFER: Deque[Dict[str, Any]] = deque(maxlen=600)
_SEQ = 0
_CONFIGURED = False


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        global _SEQ
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        with _LOCK:
            _SEQ += 1
            _BUFFER.append(
                {
                    "seq": _SEQ,
                    "time": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                    "level": record.levelname,
                    "name": record.name.replace("app.services.", "").replace("app.routers.", ""),
                    "message": message,
                }
            )


def setup_logging() -> None:
    """콘솔 + 날짜별 파일 + UI 버퍼에 로그를 흘립니다.

    pythonw.exe로 띄우면 sys.stdout이 None이므로 (요청서 3.16)
    콘솔 핸들러는 stdout이 있을 때만 붙입니다.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{datetime.now():%Y-%m-%d}.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    if sys.stdout is not None:
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(fmt)
        root.addHandler(stream)

    root.addHandler(_BufferHandler())
    logging.getLogger("app").info("로그 파일: %s", log_path)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def recent_logs(after_seq: int = 0, limit: int = 300) -> List[Dict[str, Any]]:
    with _LOCK:
        rows = [row for row in _BUFFER if row["seq"] > after_seq]
    return rows[-limit:]


def safe_print(*parts: Any) -> None:
    """pythonw.exe에서 sys.stdout이 None일 때도 죽지 않는 출력 (요청서 3.16)."""
    text = " ".join(str(p) for p in parts)
    stream = sys.stdout
    if stream is None:
        return
    try:
        stream.write(text + "\n")
        stream.flush()
    except Exception:
        pass
