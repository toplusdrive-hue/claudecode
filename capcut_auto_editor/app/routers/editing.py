"""1차 컷 편집 / 2차 자막 생성 / 3차 트랜지션·효과음."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from .. import config
from ..services import audio_analysis, builder, capcut_draft, session as session_service, transcribe
from ..services.audio_analysis import SilenceSpan
from ..services.cut_edit import (
    CutCandidate,
    CutMap,
    filler_candidates,
    format_tc,
    keep_long_silences,
    merge_candidates,
    repeat_candidates,
    silence_candidates,
)
from ..services.jobs import MANAGER, JobContext
from ..services.logging_util import get_logger
from ..services.script_align import (
    Subtitle,
    active_terms,
    build_subtitles,
    read_script,
    sanitize,
    to_srt,
)
from ..services.sources import SourceTimeline
from ._common import friendly_error, get_session

log = get_logger(__name__)
router = APIRouter(prefix="/api/editing", tags=["editing"])

US = 1_000_000


def _timeline(state: Dict[str, Any]) -> SourceTimeline:
    sources = state.get("sources") or []
    if not sources:
        raise HTTPException(
            status_code=400,
            detail={"message": "0차에서 원본 영상을 먼저 골라 주세요."},
        )
    return SourceTimeline.from_media(sources)


def _candidates(state: Dict[str, Any]) -> List[CutCandidate]:
    return [CutCandidate.from_dict(c) for c in state.get("cut_candidates") or []]


def _cut_map(state: Dict[str, Any]) -> CutMap:
    stored = state.get("cut_map")
    if stored:
        return CutMap.from_dict(stored)
    timeline = _timeline(state)
    return CutMap.build(timeline.total_us, _candidates(state))


def _store_cut_map(state: Dict[str, Any], cut_map: CutMap) -> None:
    state["cut_map"] = cut_map.to_dict()
    state["cut_signature"] = cut_map.signature()


def _cut_summary(state: Dict[str, Any], candidates: List[CutCandidate], cut_map: CutMap) -> Dict[str, Any]:
    total = cut_map.total_orig_us
    by_kind: Dict[str, Dict[str, int]] = {}
    for cand in candidates:
        row = by_kind.setdefault(cand.kind, {"count": 0, "selected": 0, "duration_us": 0})
        row["count"] += 1
        if cand.selected:
            row["selected"] += 1
            row["duration_us"] += cand.duration_us

    ratio = cut_map.removed_ratio
    warnings: List[str] = []
    if ratio > 0.40:
        warnings.append(
            f"원본의 {ratio * 100:.1f}% 가 잘려 나갑니다. 화면만 보여주며 말을 안 하는 시연 구간이 "
            "통째로 날아갔을 수 있습니다. 아래 '긴 무음 살리기'를 눌러 3초 이상 무음을 되살려 보세요."
        )
    if not cut_map.kept:
        warnings.append("남는 구간이 하나도 없습니다. 컷 선택을 줄여 주세요.")

    return {
        "total_us": total,
        "kept_us": cut_map.kept_duration_us,
        "removed_us": total - cut_map.kept_duration_us,
        "removed_ratio": round(ratio, 4),
        "span_count": len(cut_map.kept),
        "signature": cut_map.signature(),
        "by_kind": by_kind,
        "warnings": warnings,
        "total_label": format_tc(total),
        "kept_label": format_tc(cut_map.kept_duration_us),
    }


def _rebuild_candidates(state: Dict[str, Any], *, keep_selection: bool = True) -> List[CutCandidate]:
    """저장된 무음/단어 데이터로 후보만 다시 계산합니다 (전사를 다시 하지 않습니다)."""
    timeline = _timeline(state)
    settings = state.get("cut_settings") or {}
    spans = [SilenceSpan(int(s["start_us"]), int(s["end_us"])) for s in state.get("silence") or []]
    words = (state.get("transcript") or {}).get("words") or []

    groups = [
        silence_candidates(
            spans,
            tail_pad_sec=float(settings.get("tail_pad", 0.35)),
            head_pad_sec=float(settings.get("head_pad", 0.15)),
            total_us=timeline.total_us,
        )
    ]
    if words:
        groups.append(filler_candidates(words, config.load_fillers()))
        groups.append(repeat_candidates(words))

    merged = merge_candidates(groups)

    if keep_selection:
        previous = {c.id: c.selected for c in _candidates(state)}
        for cand in merged:
            if cand.id in previous:
                cand.selected = previous[cand.id]

    # 앞뒤 문맥 채우기
    if words:
        _attach_context(merged, words)

    state["cut_candidates"] = [c.to_dict() for c in merged]
    return merged


def _attach_context(candidates: List[CutCandidate], words: List[Dict[str, Any]]) -> None:
    for cand in candidates:
        if cand.context_before or cand.context_after:
            continue
        before = [w["text"] for w in words if int(w["end_us"]) <= cand.start_us][-6:]
        after = [w["text"] for w in words if int(w["start_us"]) >= cand.end_us][:6]
        cand.context_before = " ".join(before)
        cand.context_after = " ".join(after)


# ── 1차: 분석 ───────────────────────────────────────────────────────────────
@router.post("/{session_id}/analyze")
def analyze(session_id: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """오디오 추출 → 무음 감지 → 전사 → 컷 후보 생성. 백그라운드 작업으로 돕니다."""
    state = get_session(session_id)
    timeline = _timeline(state)
    force = bool(payload.get("force"))

    settings = state.get("cut_settings") or {}
    settings.update({k: v for k, v in (payload.get("settings") or {}).items()})
    state["cut_settings"] = settings
    session_service.save(state)

    def _run(ctx: JobContext) -> Dict[str, Any]:
        local = session_service.load(session_id)
        work = session_service.work_dir(session_id)
        clips = timeline.clips

        # 1) 오디오 추출 (0 ~ 0.20)
        parts: List[Path] = []
        for index, clip in enumerate(clips):
            ctx.raise_if_cancelled()
            dest = work / f"src_{index:02d}.wav"
            if not dest.exists() or force:
                span = 0.20 / max(1, len(clips))
                audio_analysis.extract_wav(
                    clip.path,
                    dest,
                    total_us=clip.duration_us,
                    on_progress=ctx.stage(0.20 * index / max(1, len(clips)), span),
                    cancel=ctx.cancel,
                )
            parts.append(dest)
            ctx.progress(0.20 * (index + 1) / max(1, len(clips)), f"오디오 추출 {index + 1}/{len(clips)}")

        # 2) 이어붙이기 (0.20 ~ 0.24)
        ctx.progress(0.21, "오디오를 하나로 이어붙이는 중")
        merged = work / "timeline.wav"
        audio_analysis.concat_wavs(parts, merged, cancel=ctx.cancel)

        # 3) 무음 감지 (0.24 ~ 0.42)
        ctx.progress(0.24, "무음 구간을 찾는 중")
        spans = audio_analysis.detect_silence(
            merged,
            threshold_db=float(settings.get("threshold_db", -35.0)),
            min_duration=float(settings.get("min_duration", 0.6)),
            total_us=timeline.total_us,
            on_progress=ctx.stage(0.24, 0.18),
            cancel=ctx.cancel,
        )

        # 4) 전사 (0.42 ~ 0.95)
        ctx.progress(0.42, "음성 인식 준비 중")
        result = transcribe.transcribe(
            merged,
            session_id=session_id,
            total_us=timeline.total_us,
            silences=spans,
            on_progress=ctx.stage(0.42, 0.53),
            cancel=ctx.cancel,
            force=force,
        )

        # 5) 후보 생성 (0.95 ~ 1.0)
        ctx.progress(0.96, "컷 후보를 추리는 중")
        local["audio"] = {"wav": str(merged), "total_us": timeline.total_us}
        local["silence"] = [{"start_us": s.start_us, "end_us": s.end_us} for s in spans]
        local["transcript"] = result
        local["cut_settings"] = settings
        candidates = _rebuild_candidates(local, keep_selection=False)
        cut_map = CutMap.build(timeline.total_us, candidates)
        _store_cut_map(local, cut_map)
        session_service.save(local)

        summary = _cut_summary(local, candidates, cut_map)
        for warning in summary["warnings"]:
            ctx.warn(warning)

        return {
            "label": f"무음 {len(spans)}개 · 문장 {len(result['segments'])}개 · 컷 후보 {len(candidates)}개",
            "candidate_count": len(candidates),
            "segment_count": len(result["segments"]),
            "summary": summary,
        }

    job = MANAGER.submit(
        session_id=session_id,
        kind="analyze",
        title="1차 컷 편집 분석 (오디오 · 무음 · 음성 인식)",
        target=_run,
    )
    return job.to_dict()


@router.get("/{session_id}/cuts")
def get_cuts(session_id: str) -> Dict[str, Any]:
    state = get_session(session_id)
    candidates = _candidates(state)
    cut_map = _cut_map(state)
    return {
        "candidates": [c.to_dict() for c in candidates],
        "settings": state.get("cut_settings"),
        "summary": _cut_summary(state, candidates, cut_map),
        "fillers": config.load_fillers(),
        "has_transcript": bool((state.get("transcript") or {}).get("segments")),
    }


@router.post("/{session_id}/cuts/recalc")
def recalc_cuts(session_id: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """슬라이더 값만 바꿔 다시 계산합니다.

    임계값·최소 무음 길이를 바꾸면 **무음 감지를 다시 해야** 하므로 재분석이 필요합니다.
    앞뒤 여유(tail/head)만 바꾼 경우는 저장된 무음 데이터로 즉시 다시 계산합니다.
    """
    state = get_session(session_id)
    old = dict(state.get("cut_settings") or {})
    new = dict(old)
    new.update(payload.get("settings") or {})
    state["cut_settings"] = new

    needs_reanalyze = (
        float(new.get("threshold_db", -35)) != float(old.get("threshold_db", -35))
        or float(new.get("min_duration", 0.6)) != float(old.get("min_duration", 0.6))
    )
    if needs_reanalyze and not payload.get("allow_stale"):
        session_service.save(state)
        return {
            "needs_reanalyze": True,
            "message": (
                "임계값이나 최소 무음 길이를 바꾸면 무음 감지를 다시 해야 합니다. "
                "'다시 분석'을 눌러 주세요. (음성 인식 결과는 그대로 재사용하므로 오래 걸리지 않습니다)"
            ),
            "settings": new,
        }

    candidates = _rebuild_candidates(state)
    cut_map = CutMap.build(_timeline(state).total_us, candidates)
    _store_cut_map(state, cut_map)
    session_service.save(state)
    return {
        "needs_reanalyze": False,
        "candidates": [c.to_dict() for c in candidates],
        "summary": _cut_summary(state, candidates, cut_map),
        "settings": new,
    }


@router.patch("/{session_id}/cuts")
def update_cuts(session_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """체크박스 선택 반영. `selections`는 {id: bool} 또는 유형 일괄 지정."""
    state = get_session(session_id)
    candidates = _candidates(state)
    selections: Dict[str, bool] = {str(k): bool(v) for k, v in (payload.get("selections") or {}).items()}
    kind_toggle = payload.get("kind")
    kind_value = payload.get("value")

    for cand in candidates:
        if cand.id in selections:
            cand.selected = selections[cand.id]
        if kind_toggle and cand.kind == kind_toggle and kind_value is not None:
            cand.selected = bool(kind_value)

    state["cut_candidates"] = [c.to_dict() for c in candidates]
    cut_map = CutMap.build(_timeline(state).total_us, candidates)
    _store_cut_map(state, cut_map)
    session_service.save(state)
    return {
        "candidates": [c.to_dict() for c in candidates],
        "summary": _cut_summary(state, candidates, cut_map),
    }


@router.post("/{session_id}/cuts/keep-long-silence")
def keep_long_silence(session_id: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    state = get_session(session_id)
    candidates = _candidates(state)
    threshold = float(payload.get("threshold_sec") or 3.0)
    changed = keep_long_silences(candidates, threshold_sec=threshold)
    state["cut_candidates"] = [c.to_dict() for c in candidates]
    cut_map = CutMap.build(_timeline(state).total_us, candidates)
    _store_cut_map(state, cut_map)
    session_service.save(state)
    return {
        "changed": changed,
        "message": f"{threshold:g}초 이상 무음 {changed}개를 살렸습니다.",
        "candidates": [c.to_dict() for c in candidates],
        "summary": _cut_summary(state, candidates, cut_map),
    }


@router.get("/{session_id}/preview")
def preview(session_id: str, start_us: int = 0, end_us: int = 0) -> FileResponse:
    """구간 미리듣기용 wav 조각."""
    state = get_session(session_id)
    wav = str((state.get("audio") or {}).get("wav") or "")
    if not wav or not Path(wav).is_file():
        raise HTTPException(status_code=400, detail={"message": "먼저 1차 분석을 실행해 주세요."})
    if end_us <= start_us:
        raise HTTPException(status_code=400, detail={"message": "미리듣기 구간이 잘못되었습니다."})

    pad = 700_000  # 앞뒤 0.7초를 함께 들려줍니다
    begin = max(0, start_us - pad) / US
    length = min(30.0, (end_us - start_us + pad * 2) / US)
    work = session_service.work_dir(session_id) / "preview"
    work.mkdir(parents=True, exist_ok=True)
    dest = work / f"p_{start_us}_{end_us}.wav"
    if not dest.exists():
        try:
            audio_analysis.cut_audio_chunk(Path(wav), dest, begin, length)
        except Exception as exc:
            raise friendly_error(exc) from exc
    return FileResponse(str(dest), media_type="audio/wav")


@router.post("/{session_id}/build-draft")
def build_draft(session_id: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """1차 결과를 캡컷 드래프트로 만듭니다."""
    state = get_session(session_id)
    timeline = _timeline(state)
    candidates = _candidates(state)
    cut_map = CutMap.build(timeline.total_us, candidates)
    _store_cut_map(state, cut_map)

    draft_name = str(payload.get("draft_name") or state.get("draft_name") or "").strip()
    if not draft_name:
        draft_name = f"자동편집_{state['id']}"
    invalid = set('\\/:*?"<>|')
    if invalid & set(draft_name):
        raise HTTPException(
            status_code=400,
            detail={"message": r'드래프트 이름에 \ / : * ? " < > | 는 쓸 수 없습니다.'},
        )
    session_service.save(state)

    def _run(ctx: JobContext) -> Dict[str, Any]:
        ctx.progress(0.05, "캡컷 실행 여부 확인 중")
        local = session_service.load(session_id)

        existing = Path(str(capcut_draft.require_root()) ) / draft_name
        if existing.is_dir():
            ctx.progress(0.12, "기존 드래프트를 백업하는 중")
            record = capcut_draft.backup_draft(existing, tag="1차전")
            local.setdefault("backups", []).insert(0, record)

        ctx.progress(0.25, "드래프트를 만드는 중")
        report = builder.build_cut_draft(
            draft_name=draft_name,
            timeline=timeline,
            cut_map=cut_map,
            session_id=session_id,
        )
        ctx.progress(0.9, "결과를 다시 읽어 확인하는 중")

        local["draft_name"] = report.draft_name
        local["draft_path"] = report.draft_path
        _store_cut_map(local, cut_map)
        session_service.mark_done(local, "stage1")
        # 컷이 바뀌었으니 이후 단계는 다시 해야 합니다.
        session_service.clear_done(local, "stage2", "stage3", "stage4", "stage5")
        session_service.save(local)

        for warning in report.warnings:
            ctx.warn(warning)
        late = capcut_draft.verify_capcut_still_closed()
        if late:
            ctx.warn(late)

        verified = report.details.get("verified", {})
        return {
            "label": f"'{report.draft_name}' 드래프트 생성 완료 · 구간 {report.details['placed_segments']}개",
            "report": report.to_dict(),
            "verified": verified,
        }

    job = MANAGER.submit(
        session_id=session_id, kind="build_draft", title="1차 컷 편집 드래프트 생성", target=_run
    )
    return job.to_dict()


# ── 2차: 자막 ───────────────────────────────────────────────────────────────
@router.post("/{session_id}/subtitles/generate")
def generate_subtitles(session_id: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    state = get_session(session_id)
    transcript = state.get("transcript") or {}
    if not transcript.get("segments"):
        raise HTTPException(
            status_code=400, detail={"message": "먼저 1차 분석(음성 인식)을 끝내 주세요."}
        )

    script_path = str(payload.get("script_path") or "").strip()
    max_chars = int(payload.get("max_chars") or (state.get("subtitle_settings") or {}).get("max_chars", 36))

    script_sentences: Optional[List[str]] = None
    script_notice = ""
    if script_path:
        if not Path(script_path).is_file():
            raise HTTPException(status_code=400, detail={"message": f"'{script_path}' 대본 파일이 없습니다."})
        script_sentences = read_script(script_path)
        script_notice = f"대본 {len(script_sentences)}문장을 읽었습니다."

    cut_map = _cut_map(state)
    glossary_terms = active_terms(config.load_glossary())

    try:
        subtitles = build_subtitles(
            transcript["segments"],
            cut_map=cut_map,
            glossary_terms=glossary_terms,
            script_sentences=script_sentences,
            max_chars=max_chars,
        )
    except Exception as exc:
        raise friendly_error(exc) from exc

    state["subtitles"] = [s.to_dict() for s in subtitles]
    state["script_path"] = script_path
    state["subtitle_settings"] = {"max_chars": max_chars}
    session_service.save(state)

    low_confidence = [s for s in subtitles if s.source == "stt" and s.confidence < 0.6]
    over_limit = [s for s in subtitles if len(s.text) > max_chars]

    warnings: List[str] = []
    if not script_sentences:
        warnings.append(
            "대본 없이 음성 인식 결과만 썼습니다. 신뢰도가 낮은 자막은 아래 표에 표시했으니 확인해 주세요."
        )
    if over_limit:
        warnings.append(
            f"{len(over_limit)}개 자막이 {max_chars}자를 넘습니다. 띄어쓰기 없는 긴 단어는 "
            "하이픈 없이 쪼갤 수 없어 그대로 두었습니다."
        )

    return {
        "subtitles": state["subtitles"],
        "count": len(subtitles),
        "script_notice": script_notice,
        "low_confidence_count": len(low_confidence),
        "warnings": warnings,
        "signature": cut_map.signature(),
        "max_chars": max_chars,
    }


@router.get("/{session_id}/subtitles")
def get_subtitles(session_id: str) -> Dict[str, Any]:
    state = get_session(session_id)
    return {
        "subtitles": state.get("subtitles") or [],
        "max_chars": (state.get("subtitle_settings") or {}).get("max_chars", 36),
        "signature": state.get("cut_signature"),
    }


@router.patch("/{session_id}/subtitles")
def edit_subtitle(session_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """검수 테이블의 인라인 수정."""
    state = get_session(session_id)
    rows = state.get("subtitles") or []
    index = int(payload.get("index", -1))
    target = next((r for r in rows if int(r.get("index", -1)) == index), None)
    if target is None:
        raise HTTPException(status_code=404, detail={"message": f"{index}번 자막을 찾을 수 없습니다."})

    if "text" in payload:
        target["text"] = str(payload["text"]).strip()
        target["edited"] = True
        target["source"] = "mixed"
    if "start_us" in payload:
        target["start_us"] = int(payload["start_us"])
    if "end_us" in payload:
        target["end_us"] = int(payload["end_us"])
    target["duration_us"] = int(target["end_us"]) - int(target["start_us"])

    if payload.get("delete"):
        rows = [r for r in rows if int(r.get("index", -1)) != index]

    cleaned = sanitize([Subtitle.from_dict(r) for r in rows])
    for pos, sub in enumerate(cleaned, start=1):
        sub.index = pos
    state["subtitles"] = [s.to_dict() for s in cleaned]
    session_service.save(state)
    return {"subtitles": state["subtitles"], "count": len(cleaned)}


@router.get("/{session_id}/subtitles/srt", response_class=PlainTextResponse)
def subtitles_srt(session_id: str) -> str:
    state = get_session(session_id)
    subs = [Subtitle.from_dict(r) for r in state.get("subtitles") or []]
    if not subs:
        raise HTTPException(status_code=400, detail={"message": "아직 자막이 없습니다."})
    return to_srt(subs)


@router.post("/{session_id}/subtitles/apply")
def apply_subtitles(session_id: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """자막을 드래프트에 넣습니다. 넣기 전에 컷 서명을 대조합니다 (요청서 3.18)."""
    state = get_session(session_id)
    draft_path = str(state.get("draft_path") or "")
    if not draft_path or not Path(draft_path).is_dir():
        raise HTTPException(
            status_code=400, detail={"message": "먼저 1차 컷 편집으로 드래프트를 만들어 주세요."}
        )
    profile = config.load_style_profile()
    if not profile:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "자막 스타일을 아직 캘리브레이션하지 않았습니다. "
                "캘리브레이션 화면에서 참조 드래프트를 고르거나 수동 입력을 해 주세요."
            },
        )
    subs = [Subtitle.from_dict(r) for r in state.get("subtitles") or []]
    if not subs:
        raise HTTPException(status_code=400, detail={"message": "넣을 자막이 없습니다."})

    cut_map = _cut_map(state)

    def _run(ctx: JobContext) -> Dict[str, Any]:
        ctx.progress(0.1, "컷 서명을 대조하는 중")
        local = session_service.load(session_id)
        ctx.progress(0.2, "드래프트를 백업하는 중")
        record = capcut_draft.backup_draft(Path(draft_path), tag="2차전")
        local.setdefault("backups", []).insert(0, record)

        ctx.progress(0.35, f"자막 {len(subs)}개를 넣는 중")
        report = builder.apply_subtitles(
            draft_path=Path(draft_path),
            subtitles=subs,
            profile=profile,
            cut_map=cut_map,
            session_id=session_id,
        )
        ctx.progress(0.9, "파일을 다시 읽어 확인하는 중")

        session_service.mark_done(local, "stage2")
        session_service.save(local)
        for warning in report.warnings:
            ctx.warn(warning)
        late = capcut_draft.verify_capcut_still_closed()
        if late:
            ctx.warn(late)

        confirmed = report.details.get("confirmed", 0)
        return {
            # 요청한 개수가 아니라 **실제로 확인된 개수**를 표시합니다 (요청서 7절)
            "label": f"파일에서 확인된 자막 {confirmed}개 (요청 {report.details.get('requested', 0)}개)",
            "report": report.to_dict(),
        }

    job = MANAGER.submit(
        session_id=session_id, kind="apply_subtitles", title="2차 자막을 드래프트에 넣기", target=_run
    )
    return job.to_dict()


@router.get("/{session_id}/signature-check")
def signature_check(session_id: str) -> Dict[str, Any]:
    """검수 화면에 미리 띄울 경고 (요청서 3.18)."""
    state = get_session(session_id)
    draft_path = str(state.get("draft_path") or "")
    if not draft_path or not Path(draft_path).is_dir():
        return {"ok": False, "message": "드래프트가 아직 없습니다.", "kind": "no_draft"}

    cut_map = _cut_map(state)
    try:
        builder.check_cut_signature(Path(draft_path), cut_map)
    except builder.CutSignatureMismatch as exc:
        return {
            "ok": False,
            "kind": "mismatch",
            "message": str(exc),
            "draft": exc.draft_sig,
            "current": exc.current_sig,
        }
    except Exception as exc:
        return {"ok": False, "kind": "error", "message": str(exc)}
    return {"ok": True, "signature": cut_map.signature(), "message": "드래프트와 컷 설정이 일치합니다."}


# ── 3차: 트랜지션 / 효과음 ──────────────────────────────────────────────────
@router.get("/{session_id}/image-clips")
def image_clips(session_id: str) -> Dict[str, Any]:
    state = get_session(session_id)
    draft_path = str(state.get("draft_path") or "")
    if not draft_path or not Path(draft_path).is_dir():
        raise HTTPException(status_code=400, detail={"message": "먼저 드래프트를 만들어 주세요."})
    clips = builder.list_image_clips(Path(draft_path))
    profile = config.load_style_profile() or {}
    return {
        "clips": clips,
        "calibrated_transitions": profile.get("transitions") or [],
        "notice": (
            ""
            if clips
            else "드래프트에 이미지 클립이 없습니다. 캡컷에서 이미지를 타임라인에 넣고 저장한 뒤 "
            "이 화면을 새로고침해 주세요."
        ),
    }


@router.post("/{session_id}/transitions/apply")
def apply_transitions(session_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    state = get_session(session_id)
    draft_path = str(state.get("draft_path") or "")
    if not draft_path or not Path(draft_path).is_dir():
        raise HTTPException(status_code=400, detail={"message": "먼저 드래프트를 만들어 주세요."})
    requests = list(payload.get("transitions") or [])
    if not requests:
        raise HTTPException(status_code=400, detail={"message": "적용할 트랜지션을 하나 이상 골라 주세요."})

    try:
        capcut_draft.backup_draft(Path(draft_path), tag="3차전")
        report = builder.apply_transitions(
            draft_path=Path(draft_path), requests=requests, session_id=session_id
        )
    except Exception as exc:
        raise friendly_error(exc) from exc

    state["transitions"] = requests
    session_service.mark_done(state, "stage3")
    session_service.save(state)

    late = capcut_draft.verify_capcut_still_closed()
    if late:
        report.warnings.append(late)
    return report.to_dict()


@router.post("/{session_id}/sfx/apply")
def apply_sfx(session_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    state = get_session(session_id)
    draft_path = str(state.get("draft_path") or "")
    if not draft_path or not Path(draft_path).is_dir():
        raise HTTPException(status_code=400, detail={"message": "먼저 드래프트를 만들어 주세요."})
    placements = list(payload.get("placements") or [])

    try:
        capcut_draft.backup_draft(Path(draft_path), tag="효과음전")
        report = builder.apply_sfx(
            draft_path=Path(draft_path), placements=placements, session_id=session_id
        )
    except Exception as exc:
        raise friendly_error(exc) from exc

    state["sfx"] = placements
    session_service.mark_done(state, "stage3")
    session_service.save(state)

    late = capcut_draft.verify_capcut_still_closed()
    if late:
        report.warnings.append(late)
    return report.to_dict()
