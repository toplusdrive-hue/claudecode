"""라우터 공용 헬퍼."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException

from ..services import session as session_service
from ..services.builder import BuildError, CutSignatureMismatch
from ..services.capcut_draft import CapCutRunning, DraftRootMissing
from ..services.audio_analysis import MediaToolMissing


def get_session(session_id: str) -> Dict[str, Any]:
    try:
        return session_service.load(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def friendly_error(exc: Exception) -> HTTPException:
    """예외를 한국어 안내가 담긴 HTTP 응답으로 바꿉니다.

    모든 실패는 조용히 넘어가지 않고 **무엇이 왜 실패했고 무엇을 하면 되는지**
    보여 줍니다 (요청서 5절).
    """
    if isinstance(exc, CapCutRunning):
        return HTTPException(
            status_code=409,
            detail={"message": str(exc), "kind": "capcut_running", "processes": exc.processes},
        )
    if isinstance(exc, CutSignatureMismatch):
        return HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "kind": "cut_signature_mismatch",
                "draft": exc.draft_sig,
                "current": exc.current_sig,
            },
        )
    if isinstance(exc, DraftRootMissing):
        return HTTPException(status_code=400, detail={"message": str(exc), "kind": "draft_root_missing"})
    if isinstance(exc, MediaToolMissing):
        return HTTPException(status_code=400, detail={"message": str(exc), "kind": "ffmpeg_missing"})
    if isinstance(exc, (BuildError, ValueError, FileNotFoundError)):
        return HTTPException(status_code=400, detail={"message": str(exc), "kind": "invalid"})
    if isinstance(exc, HTTPException):
        return exc
    return HTTPException(status_code=500, detail={"message": str(exc) or exc.__class__.__name__, "kind": "unknown"})
