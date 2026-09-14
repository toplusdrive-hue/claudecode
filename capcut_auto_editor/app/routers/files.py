"""경로 입력 세 가지: 앱 내장 찾아보기 / 윈도우 기본 창 / 직접 붙여넣기."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Body

from ..config import BG_DIR, SFX_DIR
from ..services import filesystem, native_picker

router = APIRouter(prefix="/api/files", tags=["files"])


@router.get("/browse")
def browse(path: str = "", kind: str = "video") -> Dict[str, Any]:
    return filesystem.browse(path or None, kind)


@router.post("/expand")
def expand(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    raw = payload.get("paths")
    if isinstance(raw, str):
        raw = [line for line in raw.replace("\r", "").split("\n") if line.strip()]
    return filesystem.expand_inputs(list(raw or []), str(payload.get("kind") or "video"))


@router.post("/diagnose")
def diagnose(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    return filesystem.diagnose_path(str(payload.get("path") or ""))


@router.post("/native-pick")
def native_pick(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    return native_picker.pick(
        mode=str(payload.get("mode") or "files"),
        initial=str(payload.get("initial") or filesystem.last_dir()),
        kind=str(payload.get("kind") or "video"),
        timeout=float(payload.get("timeout") or 180.0),
    )


def _listing(folder: Path, exts: set[str]) -> List[Dict[str, Any]]:
    if not folder.is_dir():
        return []
    return [
        {"name": item.name, "path": str(item), "size": item.stat().st_size}
        for item in sorted(folder.iterdir(), key=lambda c: c.name.lower())
        if item.is_file() and item.suffix.lower() in exts
    ]


@router.get("/sfx")
def sfx() -> Dict[str, Any]:
    items = _listing(SFX_DIR, filesystem.AUDIO_EXTS)
    return {
        "folder": str(SFX_DIR),
        "items": items,
        "notice": (
            "" if items else f"효과음이 없습니다. '{SFX_DIR}' 폴더에 wav 파일을 넣으면 목록에 나타납니다."
        ),
    }


@router.get("/bg")
def backgrounds() -> Dict[str, Any]:
    items = _listing(BG_DIR, filesystem.IMAGE_EXTS)
    return {
        "folder": str(BG_DIR),
        "items": items,
        "notice": (
            "" if items else f"배경 이미지가 없습니다. '{BG_DIR}' 폴더에 1080x1920 이미지를 넣어 주세요."
        ),
    }
