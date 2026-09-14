"""세션 관리, 0차 프로젝트 준비, 작업(job) 조회."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse

from ..services import capcut_draft, session as session_service
from ..services.audio_analysis import probe
from ..services.jobs import MANAGER, sse_stream
from ..services.logging_util import get_logger
from ..services.sources import SourceTimeline
from ._common import friendly_error, get_session

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["sessions"])


@router.get("/sessions")
def list_sessions() -> Dict[str, Any]:
    return {"sessions": session_service.list_sessions()}


@router.post("/sessions")
def create_session(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    return session_service.create(str(payload.get("title") or ""))


@router.get("/sessions/{session_id}")
def read_session(session_id: str) -> Dict[str, Any]:
    state = get_session(session_id)
    return {"session": state, "steps": session_service.step_status(state)}


@router.patch("/sessions/{session_id}")
def rename_session(session_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    state = get_session(session_id)
    title = str(payload.get("title") or "").strip()
    if title:
        state["title"] = title
    return session_service.save(state)


@router.delete("/sessions/{session_id}")
def remove_session(session_id: str) -> Dict[str, Any]:
    session_service.delete(session_id)
    return {"ok": True}


@router.post("/sessions/{session_id}/sources")
def set_sources(session_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """0차 — 원본 영상 여러 개를 순서대로 등록하고 ffprobe로 정보를 읽습니다."""
    state = get_session(session_id)
    paths: List[str] = [str(p) for p in (payload.get("paths") or [])]

    media: List[Dict[str, Any]] = []
    problems: List[Dict[str, str]] = []
    for path in paths:
        if not Path(path).is_file():
            problems.append({"path": path, "message": f"'{path}' 파일이 없습니다."})
            continue
        try:
            info = probe(path).to_dict()
        except Exception as exc:
            problems.append({"path": path, "message": str(exc)})
            continue
        if not info["has_audio"]:
            problems.append(
                {"path": path, "message": f"'{info['name']}' 에 오디오 트랙이 없어 컷 편집과 자막을 만들 수 없습니다."}
            )
        media.append(info)

    state["sources"] = media
    timeline = SourceTimeline.from_media(media)
    warnings = timeline.mixed_warnings()
    state["warnings"] = warnings

    # 소스가 바뀌면 이후 단계는 전부 다시 해야 합니다.
    session_service.clear_done(state, "stage1", "stage2", "stage3", "stage4", "stage5")
    state["audio"] = {}
    state["silence"] = []
    state["transcript"] = {}
    state["cut_candidates"] = []
    state["cut_map"] = None
    state["subtitles"] = []
    if media:
        session_service.mark_done(state, "stage0")
    else:
        session_service.clear_done(state, "stage0")
    session_service.save(state)

    width, height, fps = timeline.canvas
    return {
        "session": state,
        "steps": session_service.step_status(state),
        "timeline": timeline.to_dict(),
        "canvas": {"width": width, "height": height, "fps": fps},
        "warnings": warnings,
        "problems": problems,
    }


@router.post("/sessions/{session_id}/backup")
def backup(session_id: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """드래프트 폴더 전체 백업. 단계 실행 전 스냅샷에도 씁니다."""
    state = get_session(session_id)
    target = str(payload.get("draft_path") or state.get("draft_path") or "")
    if not target:
        raise HTTPException(
            status_code=400,
            detail={"message": "백업할 드래프트가 없습니다. 먼저 1차 컷 편집으로 드래프트를 만들어 주세요."},
        )
    try:
        record = capcut_draft.backup_draft(Path(target), str(payload.get("tag") or ""))
    except Exception as exc:
        raise friendly_error(exc) from exc
    state.setdefault("backups", []).insert(0, record)
    session_service.save(state)
    return {"backup": record, "backups": state["backups"]}


@router.get("/sessions/{session_id}/backups")
def backups(session_id: str) -> Dict[str, Any]:
    state = get_session(session_id)
    draft_name = Path(str(state.get("draft_path") or "")).name
    return {"backups": capcut_draft.list_backups(draft_name or None)}


@router.post("/sessions/{session_id}/restore")
def restore(session_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    state = get_session(session_id)
    backup_path = str(payload.get("backup_path") or "")
    draft_path = str(payload.get("draft_path") or state.get("draft_path") or "")
    if not backup_path or not draft_path:
        raise HTTPException(status_code=400, detail={"message": "되돌릴 백업과 대상 드래프트를 모두 지정해 주세요."})
    try:
        result = capcut_draft.restore_backup(Path(backup_path), Path(draft_path))
    except Exception as exc:
        raise friendly_error(exc) from exc
    return {"ok": True, **result, "verified": capcut_draft.inspect_draft(Path(draft_path))}


@router.get("/registry-backups")
def registry_backups() -> Dict[str, Any]:
    """캡컷 프로젝트 목록(root_meta_info.json)의 백업 목록."""
    return {
        "backups": capcut_draft.list_registry_backups(),
        "note": (
            "이 파일은 캡컷 프로젝트 목록의 정본입니다. 도구가 목록에 드래프트를 등록하기 전마다 "
            "따로 보관해 둡니다. 캡컷 목록이 이상해졌다면 등록 직전 시점으로 되돌릴 수 있습니다."
        ),
    }


@router.post("/registry-restore")
def registry_restore(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    backup_path = str(payload.get("backup_path") or "")
    if not backup_path:
        raise HTTPException(status_code=400, detail={"message": "되돌릴 백업을 골라 주세요."})
    try:
        root = capcut_draft.require_root()
        result = capcut_draft.restore_registry(Path(backup_path), root)
    except Exception as exc:
        raise friendly_error(exc) from exc
    return {"ok": True, **result}


@router.get("/sessions/{session_id}/draft-check")
def draft_check(session_id: str) -> Dict[str, Any]:
    """드래프트를 다시 읽어 실제 상태를 확인합니다 (요청서 7절 — 적용 후 검증)."""
    state = get_session(session_id)
    draft_path = str(state.get("draft_path") or "")
    if not draft_path or not Path(draft_path).is_dir():
        raise HTTPException(status_code=400, detail={"message": "확인할 드래프트가 아직 없습니다."})
    return capcut_draft.inspect_draft(Path(draft_path))


# ── 작업(job) ───────────────────────────────────────────────────────────────
@router.get("/jobs/{job_id}")
def job_status(job_id: str) -> Dict[str, Any]:
    job = MANAGER.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"message": "작업을 찾을 수 없습니다."})
    return job.to_dict()


@router.get("/jobs/{job_id}/stream")
def job_stream(job_id: str) -> StreamingResponse:
    return StreamingResponse(
        sse_stream(job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.post("/jobs/{job_id}/cancel")
def job_cancel(job_id: str) -> Dict[str, Any]:
    ok = MANAGER.cancel(job_id)
    return {
        "ok": ok,
        "message": "취소 요청을 보냈습니다. 실행 중인 프로세스를 정리하는 데 몇 초 걸릴 수 있습니다."
        if ok
        else "이미 끝난 작업입니다.",
    }


@router.get("/sessions/{session_id}/jobs")
def session_jobs(session_id: str) -> Dict[str, Any]:
    return {"jobs": MANAGER.for_session(session_id)}
