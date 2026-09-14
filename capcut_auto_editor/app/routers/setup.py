"""환경 점검, 설정, 사전, 로그."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Body, HTTPException

from .. import config
from ..services import capcut_draft, transcribe
from ..services.audio_analysis import MediaToolMissing
from ..services.logging_util import recent_logs

router = APIRouter(prefix="/api/setup", tags=["setup"])


@router.get("/status")
def status() -> Dict[str, Any]:
    settings = config.load_settings()
    roots = config.detect_draft_roots()
    resolved = config.resolve_draft_root()
    pycapcut_version = config.pycapcut_version()
    processes = capcut_draft.running_capcut_processes()

    warnings: List[str] = []
    blocks: List[str] = []

    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info[:2] != config.VERIFIED_PYTHON:
        warnings.append(
            f"검증된 파이썬은 {config.VERIFIED_PYTHON[0]}.{config.VERIFIED_PYTHON[1]} 인데 "
            f"지금은 {py_version} 입니다. 3.12 이상은 미검증입니다."
        )

    if pycapcut_version is None:
        blocks.append("pycapcut이 설치되어 있지 않습니다. `pip install pycapcut==0.0.3` 을 실행해 주세요.")
    elif pycapcut_version != config.VERIFIED_PYCAPCUT_VERSION:
        warnings.append(
            f"검증된 pycapcut은 {config.VERIFIED_PYCAPCUT_VERSION} 인데 지금은 {pycapcut_version} 입니다. "
            "드래프트 구조가 달라 자막이 안 보일 수 있습니다."
        )

    ffmpeg = config.ffmpeg_path()
    ffprobe = config.ffprobe_path()
    if not ffmpeg or not ffprobe:
        blocks.append(
            "ffmpeg / ffprobe를 찾을 수 없습니다. `winget install Gyan.FFmpeg` 로 설치한 뒤 "
            "터미널을 새로 열거나, 아래에서 경로를 직접 지정해 주세요."
        )

    if resolved is None:
        blocks.append(
            "캡컷 드래프트 폴더를 찾지 못했습니다. 캡컷을 한 번 실행해 프로젝트를 하나 만들거나, "
            "아래에서 경로를 직접 지정해 주세요."
        )

    try:
        import faster_whisper  # noqa: F401

        whisper_ready = True
        whisper_error = ""
    except ImportError as exc:
        whisper_ready = False
        whisper_error = f"faster-whisper가 설치되어 있지 않습니다 ({exc}). `pip install faster-whisper==1.2.1`"

    model_name = settings.get("whisper_model", "medium")
    model_cached = transcribe.model_is_cached(model_name)

    return {
        "python": {"version": py_version, "verified": f"{config.VERIFIED_PYTHON[0]}.{config.VERIFIED_PYTHON[1]}"},
        "pycapcut": {"version": pycapcut_version, "verified": config.VERIFIED_PYCAPCUT_VERSION},
        "capcut": {
            "verified_version": config.VERIFIED_CAPCUT_VERSION,
            "running": bool(processes),
            "processes": processes,
            "process_count": len(processes),
        },
        "draft_roots": roots,
        "draft_root": str(resolved) if resolved else "",
        "ffmpeg": ffmpeg or "",
        "ffprobe": ffprobe or "",
        "whisper": {
            "ready": whisper_ready,
            "error": whisper_error,
            "model": model_name,
            "cached": model_cached,
            "download_mb": transcribe.download_estimate_mb(model_name),
            "notice": (
                ""
                if model_cached
                else f"'{model_name}' 모델을 아직 내려받지 않았습니다. 1차 컷 편집을 처음 실행하면 "
                f"약 {transcribe.download_estimate_mb(model_name)}MB를 내려받습니다. "
                "네트워크 속도에 따라 수 분 걸리고, 그동안 진행률이 천천히 올라갑니다."
            ),
        },
        "settings": settings,
        "warnings": warnings,
        "blocks": blocks,
    }


@router.post("/draft-root")
def set_draft_root(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    raw = str(payload.get("path") or "").strip().strip('"')
    path = Path(raw)
    if not path.is_dir():
        raise HTTPException(
            status_code=400,
            detail={"message": f"'{raw}' 폴더가 없습니다. com.lveditor.draft 폴더의 경로를 넣어 주세요."},
        )
    if not capcut_draft.is_target_folder(path):
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"'{path.name}' 은(는) 클라우드/템플릿 폴더입니다. "
                "com.lveditor.draft 폴더를 골라 주세요."
            },
        )
    config.save_settings({"draft_root": str(path)})
    return {"ok": True, "draft_root": str(path), "drafts": capcut_draft.list_drafts(path)}


@router.post("/media-tools")
def set_media_tools(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """ffmpeg / ffprobe 경로를 직접 지정합니다.

    폴더(`C:\\ffmpeg\\bin`)를 넣으면 그 안에서 두 실행 파일을 찾고,
    실행 파일 경로를 직접 넣어도 됩니다. 둘 다 찾지 못하면 저장하지 않고
    **무엇을 못 찾았는지** 알려 줍니다.
    """
    raw = str(payload.get("path") or "").strip().strip('"')
    if not raw:
        raise HTTPException(status_code=400, detail={"message": "경로를 입력해 주세요."})

    target = Path(raw)
    if not target.exists():
        raise HTTPException(
            status_code=400,
            detail={"message": f"'{raw}' 경로가 없습니다. ffmpeg.exe 가 들어 있는 bin 폴더를 넣어 주세요."},
        )

    folder = target if target.is_dir() else target.parent
    config.save_settings({"ffmpeg": str(folder), "ffprobe": str(folder)})

    ffmpeg = config.ffmpeg_path()
    ffprobe = config.ffprobe_path()
    missing = [name for name, found in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if not found]
    if missing:
        config.save_settings({"ffmpeg": "", "ffprobe": ""})
        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    f"'{folder}' 안에서 {' 와 '.join(missing)} 을(를) 찾지 못했습니다. "
                    "ffmpeg.exe 와 ffprobe.exe 는 같은 폴더에 있어야 합니다. "
                    "압축을 푼 뒤 bin 폴더를 지정해 주세요."
                ),
                "found": {"ffmpeg": ffmpeg or "", "ffprobe": ffprobe or ""},
            },
        )

    return {"ok": True, "ffmpeg": ffmpeg, "ffprobe": ffprobe, "folder": str(folder)}


@router.get("/settings")
def get_settings() -> Dict[str, Any]:
    return config.load_settings()


@router.post("/settings")
def update_settings(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    return config.save_settings(payload)


@router.get("/fillers")
def get_fillers() -> Dict[str, Any]:
    return {"words": config.load_fillers()}


@router.post("/fillers")
def set_fillers(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    return {"words": config.save_fillers(list(payload.get("words") or []))}


@router.get("/glossary")
def get_glossary() -> Dict[str, Any]:
    return config.load_glossary()


@router.post("/glossary")
def set_glossary(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    return config.save_glossary(payload)


@router.get("/fonts")
def fonts() -> Dict[str, Any]:
    return {"fonts": capcut_draft.list_installed_fonts()}


@router.get("/logs")
def logs(after: int = 0) -> Dict[str, Any]:
    return {"logs": recent_logs(after)}


@router.get("/capcut-running")
def capcut_running() -> Dict[str, Any]:
    processes = capcut_draft.running_capcut_processes()
    return {
        "running": bool(processes),
        "processes": processes,
        "count": len(processes),
        "message": (
            "캡컷이 실행 중입니다. 드래프트를 건드리는 작업은 캡컷을 완전히 종료한 뒤에만 할 수 있습니다."
            if processes
            else "캡컷이 실행 중이지 않습니다."
        ),
    }
