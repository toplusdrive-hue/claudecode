"""자막 스타일 캘리브레이션 + 트랜지션 가져오기 (1차보다 먼저)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException

from .. import config
from ..services import capcut_draft, session as session_service
from ._common import friendly_error, get_session

router = APIRouter(prefix="/api/calibration", tags=["calibration"])


@router.get("/drafts")
def drafts() -> Dict[str, Any]:
    try:
        root = capcut_draft.require_root()
    except Exception as exc:
        raise friendly_error(exc) from exc
    return {"root": str(root), "drafts": capcut_draft.list_drafts(root)}


@router.get("/profile")
def profile() -> Dict[str, Any]:
    saved = config.load_style_profile()
    return {
        "profile": saved,
        "exists": saved is not None,
        "notice": (
            ""
            if saved
            else "자막 스타일을 아직 캘리브레이션하지 않았습니다. 캡컷에서 자막을 하나 만든 드래프트를 "
            "골라 스타일을 가져오면, 그 모양 그대로 자막이 들어갑니다."
        ),
    }


@router.post("/from-draft")
def from_draft(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    draft_path = str(payload.get("draft_path") or "")
    if not draft_path:
        raise HTTPException(status_code=400, detail={"message": "참조할 드래프트를 골라 주세요."})
    try:
        result = capcut_draft.calibrate_text_style(Path(draft_path))
    except Exception as exc:
        raise friendly_error(exc) from exc

    profile_data = result.profile
    # 폰트 파일이 이 PC에 실제로 있는지 함께 확인합니다.
    font = profile_data.get("font") or {}
    resolved = capcut_draft.find_font_file(font.get("family", ""), font.get("style", ""))
    if resolved:
        profile_data["font"]["resolved_path"] = capcut_draft.to_capcut_path(resolved)
    else:
        result.warnings.append(
            f"'{font.get('family','')} {font.get('style','')}' 글꼴 파일을 이 PC에서 찾지 못했습니다. "
            "드래프트에 적힌 경로를 그대로 씁니다. 캡컷에서 글꼴이 다르게 보이면 글꼴을 설치해 주세요."
        )

    transitions = capcut_draft.calibrate_transitions(Path(draft_path))
    profile_data["transitions"] = transitions
    config.save_style_profile(profile_data)

    unmatched = [t for t in transitions if not t["matched"]]
    if unmatched:
        result.warnings.append(
            f"트랜지션 {len(unmatched)}개는 effect_id로 pycapcut 목록에서 찾지 못했습니다. "
            "캡컷 버전이 pycapcut보다 새로우면 생길 수 있습니다."
        )

    return {
        "profile": profile_data,
        "warnings": result.warnings,
        "summary": _summary(profile_data),
        "transitions": transitions,
    }


@router.post("/manual")
def manual(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    try:
        result = capcut_draft.manual_style_profile(payload)
    except Exception as exc:
        raise friendly_error(exc) from exc
    existing = config.load_style_profile() or {}
    result.profile["transitions"] = existing.get("transitions", [])
    config.save_style_profile(result.profile)
    return {
        "profile": result.profile,
        "warnings": result.warnings,
        "summary": _summary(result.profile),
        "estimated": True,
    }


@router.post("/transitions/from-draft")
def transitions_from_draft(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    draft_path = str(payload.get("draft_path") or "")
    if not draft_path:
        raise HTTPException(status_code=400, detail={"message": "참조할 드래프트를 골라 주세요."})
    try:
        transitions = capcut_draft.calibrate_transitions(Path(draft_path))
    except Exception as exc:
        raise friendly_error(exc) from exc

    profile_data = config.load_style_profile() or {}
    profile_data["transitions"] = transitions
    config.save_style_profile(profile_data)

    notice = ""
    if not transitions:
        notice = (
            f"'{Path(draft_path).name}' 드래프트에 트랜지션이 없습니다. "
            "캡컷에서 원하는 트랜지션을 클립 사이에 한 번 적용해 저장한 뒤 다시 가져와 주세요."
        )
    return {"transitions": transitions, "notice": notice}


@router.get("/transitions/all")
def transitions_all() -> Dict[str, Any]:
    items = capcut_draft.all_transitions()
    return {
        "transitions": items,
        "count": len(items),
        "notice": (
            "전체 목록의 이름은 중국어입니다. 한국어 UI 이름과 매칭되지 않으므로, "
            "정확히 원하는 트랜지션은 캡컷에서 한 번 적용한 뒤 '가져오기'로 받아 오는 쪽이 확실합니다."
        ),
    }


def _summary(profile: Dict[str, Any]) -> Dict[str, Any]:
    background = profile.get("background") or {}
    text = profile.get("text") or {}
    position = profile.get("position") or {}
    canvas = profile.get("canvas") or {}
    check_flag = int(profile.get("check_flag") or 0)
    return {
        "font": f"{(profile.get('font') or {}).get('family','')} {(profile.get('font') or {}).get('style','')}".strip(),
        "font_path": (profile.get("font") or {}).get("resolved_path") or (profile.get("font") or {}).get("path", ""),
        "font_size": text.get("size"),
        "text_color": text.get("color_hex"),
        "background_color": background.get("color"),
        "background_style": background.get("style"),
        "check_flag": check_flag,
        "check_flag_detail": {
            "base": bool(check_flag & 7),
            "border": bool(check_flag & 8),
            "background": bool(check_flag & 16),
        },
        "position_y": position.get("y"),
        "position_y_px": position.get("y_px"),
        "canvas": f"{canvas.get('width')}x{canvas.get('height')}",
        "raw_material_fields": profile.get("raw_material_field_count", 0),
        "raw_segment_fields": profile.get("raw_segment_field_count", 0),
        "manual": bool(profile.get("manual")),
    }
