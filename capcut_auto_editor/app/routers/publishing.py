"""4차 내보내기·마케팅 문구 / 5차 세로용 영상."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Body, HTTPException

from .. import config
from ..services import builder, capcut_draft, prompt_builder, session as session_service
from ..services.builder import VERTICAL_HEADLINE_Y, VERTICAL_SUBTITLE_Y
from ..services.cut_edit import CutMap, format_tc
from ..services.jobs import MANAGER, JobContext
from ..services.script_align import Subtitle
from ..services.sources import SourceTimeline
from ._common import friendly_error, get_session

router = APIRouter(prefix="/api/publishing", tags=["publishing"])

US = 1_000_000


def _subs(state: Dict[str, Any]) -> List[Subtitle]:
    rows = state.get("subtitles") or []
    if not rows:
        raise HTTPException(status_code=400, detail={"message": "먼저 2차에서 자막을 만들어 주세요."})
    return [Subtitle.from_dict(r) for r in rows]


@router.get("/channel")
def channel() -> Dict[str, Any]:
    return prompt_builder.load_channel_profile()


@router.post("/channel")
def set_channel(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    return prompt_builder.save_channel_profile(payload)


@router.get("/{session_id}/export-info")
def export_info(session_id: str) -> Dict[str, Any]:
    """4차 기본 경로는 '캡컷에서 직접 내보내기' 안내입니다."""
    state = get_session(session_id)
    draft_path = str(state.get("draft_path") or "")
    settings = config.load_settings()
    return {
        "draft_name": state.get("draft_name", ""),
        "draft_path": draft_path,
        "guide": [
            "캡컷을 실행하고 프로젝트 목록에서 "
            f"'{state.get('draft_name') or '(드래프트 이름)'}' 을 엽니다.",
            "이미 열어 둔 상태였다면 **완전히 종료했다가 다시 열어야** 이 도구가 넣은 내용이 보입니다.",
            "타임라인과 자막을 눈으로 확인한 뒤 우측 상단 '내보내기'를 누릅니다.",
        ],
        "auto_export": {
            "enabled": bool(settings.get("enable_auto_export")),
            "available": False if not _controller_importable() else True,
            "requires_capcut_running": True,
            "risks": [
                "자동 내보내기는 pycapcut이 **캡컷 UI를 직접 조작**하는 방식입니다. "
                "이 기능만 캡컷이 실행 중이어야 합니다 (다른 모든 단계와 반대).",
                "pycapcut 소스의 창 탐색 조건이 중국어 제목 'CapCut专业版'으로 하드코딩되어 있어 "
                "한국어/영어 캡컷에서는 창을 찾지 못할 가능성이 높습니다.",
                "pycapcut 문서에 '캡컷 6 이하만 지원'이라고 적혀 있습니다. 검증 대상은 9.3.0.3969 입니다.",
                "실패해도 드래프트는 그대로 남습니다. 캡컷에서 직접 내보내면 됩니다.",
            ],
        },
    }


def _controller_importable() -> bool:
    try:
        from pycapcut.jianying_controller import JianyingController  # noqa: F401

        return True
    except Exception:
        return False


@router.post("/{session_id}/auto-export")
def auto_export(session_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """자동 내보내기. **기본 OFF, 확인 체크박스 필수.**"""
    state = get_session(session_id)
    if not payload.get("confirmed"):
        raise HTTPException(
            status_code=400,
            detail={
                "message": "자동 내보내기는 캡컷 UI를 직접 조작합니다. "
                "위험을 확인했다는 체크박스를 먼저 눌러 주세요."
            },
        )
    draft_name = str(state.get("draft_name") or "")
    if not draft_name:
        raise HTTPException(status_code=400, detail={"message": "내보낼 드래프트가 없습니다."})

    output_path = str(payload.get("output_path") or "")

    def _run(ctx: JobContext) -> Dict[str, Any]:
        ctx.progress(0.05, "캡컷 창을 찾는 중")
        try:
            from pycapcut.jianying_controller import JianyingController
        except Exception as exc:
            raise RuntimeError(
                "자동 내보내기 모듈을 불러오지 못했습니다. 윈도우에서 `pip install uiautomation` 이 "
                f"되어 있어야 합니다. ({exc})"
            ) from exc

        if not capcut_draft.running_capcut_processes():
            raise RuntimeError(
                "자동 내보내기는 캡컷이 **실행 중**이어야 합니다 (다른 단계와 반대). "
                "캡컷을 실행해 프로젝트 목록 화면에 둔 뒤 다시 눌러 주세요."
            )

        ctx.progress(0.2, "캡컷을 조작해 내보내는 중 (창을 건드리지 마세요)")
        controller = JianyingController()
        controller.export_draft(draft_name, output_path or None)
        ctx.progress(1.0, "내보내기 완료")
        return {"label": "자동 내보내기 완료", "output_path": output_path}

    job = MANAGER.submit(
        session_id=session_id, kind="auto_export", title="자동 내보내기 (미검증 기능)", target=_run
    )
    return job.to_dict()


@router.post("/{session_id}/marketing-prompt")
def marketing_prompt(session_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    state = get_session(session_id)
    subs = _subs(state)
    answers = {
        "takeaway": str(payload.get("takeaway") or ""),
        "situation": str(payload.get("situation") or ""),
        "tool_name": str(payload.get("tool_name") or ""),
    }
    duration = subs[-1].end_us if subs else 0

    text = prompt_builder.build_marketing_prompt(
        answers=answers, subtitles=subs, video_duration_us=duration
    )
    saved = prompt_builder.save_prompt(session_id, "마케팅문구", text)

    state.setdefault("publishing", {})["answers"] = answers
    state["publishing"]["prompt_path"] = saved
    session_service.mark_done(state, "stage4")
    session_service.save(state)

    return {
        "prompt": text,
        "saved_path": saved,
        "char_count": len(text),
        "subtitle_count": len(subs),
        "questions": [
            "이번 영상에서 시청자가 가져가는 가장 큰 하나는?",
            "어떤 상황에 놓인 사람에게 필요한가? (직무, 업무 상황)",
            "다루는 도구나 기능의 정확한 이름은?",
        ],
    }


# ── 5차 세로 영상 ───────────────────────────────────────────────────────────
@router.get("/{session_id}/vertical/timeline")
def vertical_timeline(session_id: str) -> Dict[str, Any]:
    """자막 타임라인을 그대로 보여 주고 시작/끝을 고르게 합니다."""
    state = get_session(session_id)
    subs = _subs(state)
    settings = config.load_settings()
    return {
        "subtitles": [s.to_dict() for s in subs],
        "total_us": subs[-1].end_us,
        "total_label": format_tc(subs[-1].end_us),
        "recommended_sec": [20, 30],
        "defaults": {
            "overlay_scale": settings.get("vertical_overlay_scale", 1.8),
            "overlay_y": settings.get("vertical_overlay_y", -0.078125),
            "subtitle_y": VERTICAL_SUBTITLE_Y,
            "headline_y": VERTICAL_HEADLINE_Y,
        },
        "notice": (
            "여기 보이는 시각은 **컷 편집 후** 기준입니다. 실제로 잘라 쓸 소재는 원본이라, "
            "그 사이 잘려나간 컷이 있으면 원본에서는 여러 조각이 됩니다. "
            "도구가 자동으로 역변환해 이어 붙입니다."
        ),
    }


@router.post("/{session_id}/vertical/preview-range")
def vertical_preview_range(session_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """고른 구간이 원본에서 몇 조각이 되는지 미리 보여 줍니다."""
    state = get_session(session_id)
    start_us = int(payload.get("start_us") or 0)
    end_us = int(payload.get("end_us") or 0)
    if end_us <= start_us:
        raise HTTPException(status_code=400, detail={"message": "끝 지점이 시작 지점보다 뒤여야 합니다."})

    cut_map = CutMap.from_dict(state["cut_map"]) if state.get("cut_map") else None
    if cut_map is None:
        raise HTTPException(status_code=400, detail={"message": "컷 정보가 없습니다. 1차를 먼저 끝내 주세요."})

    timeline = SourceTimeline.from_media(state.get("sources") or [])
    ranges = cut_map.edit_range_to_orig_ranges(start_us, end_us)
    pieces: List[Dict[str, Any]] = []
    for orig_start, orig_end in ranges:
        for span in timeline.split(orig_start, orig_end):
            pieces.append(
                {
                    **span.to_dict(),
                    "source_name": timeline.clips[span.source_index].name,
                    "label": f"{format_tc(span.start_us)} ~ {format_tc(span.end_us)}",
                }
            )

    duration = end_us - start_us
    notes: List[str] = []
    if duration < 20 * US or duration > 30 * US:
        notes.append(
            f"고른 구간이 {duration / US:.1f}초입니다. 쇼츠·릴스는 20~30초가 무난합니다. "
            "(강제하지는 않습니다)"
        )
    if len(pieces) > 1:
        notes.append(
            f"컷 편집 때문에 원본에서 {len(pieces)}조각으로 나뉩니다. 순서대로 이어 붙입니다."
        )
    return {"orig_ranges": ranges, "pieces": pieces, "duration_us": duration, "notes": notes}


@router.post("/{session_id}/vertical/build")
def vertical_build(session_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    state = get_session(session_id)
    subs = _subs(state)
    settings = config.load_settings()
    profile = config.load_style_profile()
    if not profile:
        raise HTTPException(
            status_code=400, detail={"message": "자막 스타일 캘리브레이션을 먼저 해 주세요."}
        )

    start_us = int(payload.get("start_us") or 0)
    end_us = int(payload.get("end_us") or 0)
    if end_us <= start_us:
        raise HTTPException(status_code=400, detail={"message": "끝 지점이 시작 지점보다 뒤여야 합니다."})

    timeline = SourceTimeline.from_media(state.get("sources") or [])
    cut_map = CutMap.from_dict(state["cut_map"]) if state.get("cut_map") else CutMap.identity(timeline.total_us)

    draft_name = str(payload.get("draft_name") or f"세로_{state['id']}")
    background = str(payload.get("background_image") or "")
    overlay_scale = float(payload.get("overlay_scale") or settings.get("vertical_overlay_scale", 1.8))
    overlay_y = float(payload.get("overlay_y") or settings.get("vertical_overlay_y", -0.078125))
    subtitle_y = payload.get("subtitle_y")

    def _run(ctx: JobContext) -> Dict[str, Any]:
        ctx.progress(0.1, "구간을 원본 타임코드로 되돌리는 중")
        local = session_service.load(session_id)
        ctx.progress(0.3, "세로 드래프트를 만드는 중")
        report = builder.build_vertical_draft(
            draft_name=draft_name,
            timeline=timeline,
            cut_map=cut_map,
            edit_start_us=start_us,
            edit_end_us=end_us,
            subtitles=subs,
            profile=profile,
            background_image=background or None,
            overlay_scale=overlay_scale,
            overlay_y=overlay_y,
            subtitle_y=float(subtitle_y) if subtitle_y is not None else None,
            session_id=session_id,
        )
        ctx.progress(0.9, "파일을 다시 읽어 확인하는 중")

        local.setdefault("vertical", {})
        local["vertical"].update(
            {
                "draft_name": report.draft_name,
                "draft_path": report.draft_path,
                "start_us": start_us,
                "end_us": end_us,
            }
        )
        session_service.mark_done(local, "stage5")
        session_service.save(local)

        for warning in report.warnings:
            ctx.warn(warning)
        late = capcut_draft.verify_capcut_still_closed()
        if late:
            ctx.warn(late)
        return {
            "label": f"'{report.draft_name}' 세로 드래프트 생성 완료 "
            f"(자막 {report.details['subtitle_count']}개)",
            "report": report.to_dict(),
        }

    job = MANAGER.submit(
        session_id=session_id, kind="vertical_build", title="5차 세로용 드래프트 생성", target=_run
    )
    return job.to_dict()


@router.post("/{session_id}/vertical/prompt")
def vertical_prompt(session_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    state = get_session(session_id)
    subs = _subs(state)
    start_us = int(payload.get("start_us") or 0)
    end_us = int(payload.get("end_us") or (subs[-1].end_us if subs else 0))
    platform = str(payload.get("platform") or "shorts")

    picked = [s for s in subs if s.end_us > start_us and s.start_us < end_us]
    answers = {"takeaway": str(payload.get("takeaway") or "")}

    text = prompt_builder.build_vertical_text_prompt(
        platform=platform,
        subtitles=picked,
        answers=answers,
        clip_duration_us=end_us - start_us,
    )
    saved = prompt_builder.save_prompt(session_id, f"세로문구_{platform}", text)
    return {
        "prompt": text,
        "saved_path": saved,
        "platform": platform,
        "subtitle_count": len(picked),
        "position_hint": {
            "headline_y": VERTICAL_HEADLINE_Y,
            "subtitle_y": VERTICAL_SUBTITLE_Y,
            "note": "상단 문구 +0.6338 / 하단 문구 -0.7394 (1080x1920 실측, 정규화 좌표)",
        },
    }
