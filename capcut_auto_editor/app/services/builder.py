"""pyCapCut 호출 + 후처리 주입.

**구조는 pyCapCut이, 스타일은 후처리가** 담당하는 2단 구성입니다 (요청서 3.6).
그렇게 나눈 이유:
  - pyCapCut이 만드는 텍스트 소재는 필드가 91개 빠져 있습니다 (3.5)
  - 폰트를 resource_id로만 참조하고 path엔 더미 문자열을 씁니다 (3.6)
  - TextBorder/TextBackground 생성자가 입력값을 재매핑해서, 캘리브레이션으로 읽은
    원시값을 그대로 넣으면 두 번 변환됩니다 (조사 기록 3.5)
"""

from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .capcut_draft import (
    AUTO_SFX_TRACK,
    AUTO_SUBTITLE_TRACK,
    MARKER_KEY,
    ensure_capcut_closed,
    find_font_file,
    finalize_draft,
    inspect_draft,
    parse_font_name,
    read_content,
    require_root,
    to_capcut_path,
    transition_lookup,
    write_content,
)
from .cut_edit import CutMap
from .logging_util import get_logger
from .script_align import Subtitle, assert_no_overlap
from .sources import LocalSpan, SourceTimeline

log = get_logger(__name__)

US = 1_000_000


class BuildError(RuntimeError):
    pass


def _pycapcut():
    try:
        import pycapcut

        return pycapcut
    except ImportError as exc:  # pragma: no cover
        raise BuildError(
            "pycapcut이 설치되어 있지 않습니다. `pip install pycapcut==0.0.3` 을 실행해 주세요."
        ) from exc


# ── 소재 길이 캐시 (요청서 3.8) ──────────────────────────────────────────────
_MATERIAL_CACHE: Dict[str, Any] = {}


def load_material(path: str):
    """VideoMaterial을 만들고 재사용합니다.

    ⚠️ 요청서 3.8 — 같은 파일인데 도구마다 길이가 다릅니다.
        ffprobe 437.766667초 / 반올림 437.767초 / pyCapCut(pymediainfo, ms) 437.766초
        pyCapCut은 **자기가 읽은 길이를 1µs라도 넘으면 거부**합니다.
        → 세그먼트를 만들기 전에 `VideoMaterial.duration`을 읽어 구간을 그 길이에 맞춰 자릅니다.
    """
    pycapcut = _pycapcut()
    key = str(Path(path).resolve())
    cached = _MATERIAL_CACHE.get(key)
    if cached is None:
        cached = pycapcut.VideoMaterial(key)
        _MATERIAL_CACHE[key] = cached
        log.info("소재 '%s' 길이 %d µs (pyCapCut 기준)", Path(path).name, cached.duration)
    return cached


def clamp_span(material, start_us: int, end_us: int) -> Optional[Tuple[int, int]]:
    """구간을 소재 길이 안으로 밀어 넣습니다. 남는 게 없으면 None."""
    limit = int(material.duration)
    start = max(0, min(start_us, limit))
    end = max(0, min(end_us, limit))
    if end - start < 1000:  # 1ms 미만은 버립니다
        return None
    return start, end


# ── 자막 소재 만들기 (후처리 주입) ───────────────────────────────────────────
def resolve_font_path(profile: Dict[str, Any]) -> Tuple[str, List[str]]:
    """캘리브레이션 프로파일에서 실제 폰트 파일 절대 경로를 확정합니다."""
    warnings: List[str] = []
    font = profile.get("font") or {}
    path = str(font.get("path") or "")
    if path and Path(path.replace("/", "\\") if "\\" in path else path).exists():
        return to_capcut_path(path), warnings
    if path and Path(path).exists():
        return to_capcut_path(path), warnings

    family = str(font.get("family") or "")
    style = str(font.get("style") or "")
    if not family and path:
        family, style = parse_font_name(Path(path).name)

    found = find_font_file(family, style)
    if found:
        if path:
            warnings.append(
                f"캘리브레이션에 적힌 폰트 경로('{path}')가 이 PC에 없어 "
                f"'{found}' 로 대체했습니다."
            )
        return to_capcut_path(found), warnings

    warnings.append(
        f"'{family} {style}'.otf/.ttf 파일을 찾지 못했습니다. "
        r"%LOCALAPPDATA%\Microsoft\Windows\Fonts 와 C:\Windows\Fonts 를 확인했습니다. "
        "캡컷에서 글꼴이 기본값으로 바뀌어 보일 수 있습니다."
    )
    return to_capcut_path(path), warnings


def build_text_material(
    profile: Dict[str, Any],
    text: str,
    material_id: str,
    font_path: str,
) -> Dict[str, Any]:
    """캘리브레이션으로 저장해 둔 **캡컷 원본 소재를 통째로 복사**해 텍스트만 갈아 끼웁니다.

    요청서 3.5 — 빈 껍데기에 스타일만 얹으면 안 됩니다.
    원본이 없으면(수동 입력) 필수 필드를 직접 채웁니다.
    """
    raw = profile.get("raw_material")
    text_cfg = profile.get("text") or {}
    bg = profile.get("background") or {}
    border = profile.get("border") or {}
    check_flag = int(profile.get("check_flag") or 31)

    if raw:
        material = copy.deepcopy(raw)
    else:
        material = {
            "id": material_id,
            "type": "text",
            "typesetting": 0,
            "alignment": int(text_cfg.get("alignment", 1)),
            "letter_spacing": text_cfg.get("letter_spacing", 0),
            "line_spacing": text_cfg.get("line_spacing", 0.02),
            "line_feed": 1,
            "line_max_width": text_cfg.get("line_max_width", 0.82),
            "force_apply_line_max_width": False,
            "global_alpha": 1.0,
            "add_type": 0,
            "base_content": "",
            "fixed_height": -1.0,
            "fixed_width": -1.0,
            "font_size": float(text_cfg.get("size", 5.0)),
            "has_shadow": False,
            "recognize_type": 0,
            "shadow_alpha": 0.9,
            "shadow_angle": -45.0,
            "shadow_color": "",
            "shadow_distance": 5.0,
            "shadow_smoothing": 0.45,
            "text_alpha": 1.0,
            "text_curve": None,
            "text_preset_resource_id": "",
            "underline": False,
            "words": {"end_time": [], "start_time": [], "text": []},
        }

    material["id"] = material_id
    material["content"] = _content_json(profile, text, font_path)

    # ⚠️ 요청서 3.4 — background_color만 넣어도 check_flag 비트가 없으면 무시됩니다.
    #   기본 7 / +8 테두리 / +16 배경. 캡컷 자막(흰 배경 + 검정 글씨)은 31.
    #   background_style 이 0이면 배경이 안 나옵니다. 1 이상이어야 합니다.
    if bg:
        material["background_color"] = bg.get("color", "#ffffff")
        material["background_style"] = max(1, int(bg.get("style", 1) or 1))
        material["background_alpha"] = float(bg.get("alpha", 1.0))
        material["background_width"] = float(bg.get("width", 0.28))
        material["background_height"] = float(bg.get("height", 0.28))
        material["background_round_radius"] = float(bg.get("round_radius", 0.4))
        material["background_horizontal_offset"] = float(bg.get("horizontal_offset", 0.0))
        material["background_vertical_offset"] = float(bg.get("vertical_offset", 0.0))
        check_flag |= 16
    if border.get("enabled"):
        material["border_color"] = border.get("color_hex", "#ffffff")
        material["border_width"] = float(border.get("width", 0.08))
        material["border_alpha"] = float(border.get("alpha", 1.0))
        check_flag |= 8

    material["check_flag"] = check_flag
    material["text_color"] = text_cfg.get("color_hex", "#000000")
    material["font_size"] = float(text_cfg.get("size", 5.0))
    if font_path:
        material["font_path"] = font_path  # 캡컷 실물은 절대 경로를 씁니다 (3.6)
    material["font_name"] = str((profile.get("font") or {}).get("family") or material.get("font_name", ""))

    # 단어 타임스탬프는 자막마다 달라야 하므로 원본에서 복사된 값을 비웁니다.
    if isinstance(material.get("words"), dict):
        material["words"] = {"end_time": [], "start_time": [], "text": []}
    return material


def _content_json(profile: Dict[str, Any], text: str, font_path: str) -> str:
    """소재의 `content` 문자열(JSON)을 만듭니다. styles[0].font.path도 절대 경로여야 합니다."""
    raw = profile.get("raw_material") or {}
    base: Dict[str, Any]
    try:
        base = json.loads(raw.get("content") or "{}") if isinstance(raw.get("content"), str) else {}
    except json.JSONDecodeError:
        base = {}

    text_cfg = profile.get("text") or {}
    border = profile.get("border") or {}

    styles = base.get("styles")
    if not styles:
        style: Dict[str, Any] = {
            "fill": {
                "alpha": 1.0,
                "content": {
                    "render_type": "solid",
                    "solid": {"alpha": 1.0, "color": list(text_cfg.get("color_rgb") or [0.0, 0.0, 0.0])},
                },
            },
            "size": float(text_cfg.get("size", 5.0)),
            "bold": bool(text_cfg.get("bold", False)),
            "italic": bool(text_cfg.get("italic", False)),
            "underline": False,
            "strokes": [],
        }
        if border.get("enabled"):
            style["strokes"] = [
                {
                    "content": {
                        "solid": {
                            "alpha": float(border.get("alpha", 1.0)),
                            "color": list(border.get("color_rgb") or [1.0, 1.0, 1.0]),
                        }
                    },
                    "width": float(border.get("width", 0.08)),
                }
            ]
        styles = [style]

    styles = copy.deepcopy(styles)
    styles[0]["range"] = [0, len(text)]
    if font_path:
        font_entry = dict(styles[0].get("font") or {})
        font_entry["path"] = font_path  # ← 여기도 절대 경로 (3.6)
        if (profile.get("font") or {}).get("resource_id"):
            font_entry.setdefault("id", profile["font"]["resource_id"])
        styles[0]["font"] = font_entry
    for extra in styles[1:]:
        extra["range"] = [0, len(text)]

    base["styles"] = styles[:1]
    base["text"] = text
    return json.dumps(base, ensure_ascii=False)


_SEGMENT_SKIP = {
    "id", "material_id", "target_timerange", "source_timerange", "clip",
    "extra_material_refs", "common_keyframes", "keyframe_refs",
    "track_render_index", "render_index", "uniform_scale",
}


def merge_segment_fields(profile: Dict[str, Any], segment: Dict[str, Any]) -> int:
    """캘리브레이션 원본 세그먼트에만 있는 필드를 채워 넣습니다 (요청서 3.5의 27개 필드)."""
    raw = profile.get("raw_segment")
    if not raw:
        return 0
    added = 0
    for key, value in raw.items():
        if key in _SEGMENT_SKIP or key in segment:
            continue
        segment[key] = copy.deepcopy(value)
        added += 1
    return added


# ── 1차: 컷 편집 드래프트 ────────────────────────────────────────────────────
@dataclass
class BuildReport:
    draft_name: str
    draft_path: str
    warnings: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "draft_name": self.draft_name,
            "draft_path": self.draft_path,
            "warnings": self.warnings,
            "details": self.details,
        }


def build_cut_draft(
    *,
    draft_name: str,
    timeline: SourceTimeline,
    cut_map: CutMap,
    session_id: str,
    allow_replace: bool = True,
) -> BuildReport:
    """남는 구간만 이어 붙인 가로 드래프트를 만듭니다."""
    ensure_capcut_closed()
    pycapcut = _pycapcut()
    root = require_root()

    width, height, fps = timeline.canvas
    folder = pycapcut.DraftFolder(str(root))
    script = folder.create_draft(draft_name, width, height, fps=fps, allow_replace=allow_replace)
    script.add_track(pycapcut.TrackType.video, "메인")

    warnings: List[str] = []
    placed = 0
    skipped = 0
    cursor = 0  # 편집본 타임라인 커서

    for kept in cut_map.kept:
        # 구간이 영상 경계를 가로지르면 쪼갭니다 (요청서 4절)
        for span in timeline.split(kept.orig_start_us, kept.orig_end_us):
            material = load_material(span.path)
            clamped = clamp_span(material, span.start_us, span.end_us)
            if clamped is None:
                skipped += 1
                warnings.append(
                    f"'{Path(span.path).name}' 의 {span.start_us / US:.3f}~{span.end_us / US:.3f}초 구간은 "
                    f"소재 길이({material.duration / US:.3f}초)를 벗어나 건너뛰었습니다."
                )
                continue
            start_us, end_us = clamped
            duration = end_us - start_us
            segment = pycapcut.VideoSegment(
                material,
                pycapcut.Timerange(cursor, duration),
                source_timerange=pycapcut.Timerange(start_us, duration),
                speed=1.0,
            )
            script.add_segment(segment, "메인")
            cursor += duration
            placed += 1

    if placed == 0:
        raise BuildError(
            "드래프트에 넣을 구간이 하나도 없습니다. 컷 선택을 너무 많이 하지 않았는지 확인해 주세요."
        )

    script.save()
    draft_path = root / draft_name

    marker = {
        "session_id": session_id,
        "stage": "cut",
        # ⚠️ 요청서 3.18 — 컷 서명. 자막을 넣기 전에 이 값을 대조합니다.
        "cut_signature": cut_map.signature(),
        "timeline_total_us": timeline.total_us,
        "edited_duration_us": cursor,
    }
    result = finalize_draft(draft_path, marker=marker)

    if skipped:
        warnings.append(f"소재 길이를 벗어난 구간 {skipped}개를 건너뛰었습니다.")
    warnings.extend(result["verified"]["problems"])

    return BuildReport(
        draft_name=draft_name,
        draft_path=str(draft_path),
        warnings=warnings,
        details={
            "placed_segments": placed,
            "skipped_segments": skipped,
            "edited_duration_us": cursor,
            "canvas": {"width": width, "height": height, "fps": fps},
            "cut_signature": cut_map.signature(),
            **{k: v for k, v in result.items() if k != "verified"},
            "verified": result["verified"],
        },
    )


# ── 2차: 자막 주입 ──────────────────────────────────────────────────────────
class CutSignatureMismatch(RuntimeError):
    def __init__(self, draft_sig: Dict[str, Any], current_sig: Dict[str, Any]):
        self.draft_sig = draft_sig
        self.current_sig = current_sig
        draft_sec = draft_sig.get("kept_duration_us", 0) / US
        current_sec = current_sig.get("kept_duration_us", 0) / US
        super().__init__(
            "드래프트와 지금 컷 설정이 어긋나 자막을 넣지 않았습니다. "
            f"드래프트는 {draft_sec:.1f}초 / {draft_sig.get('span_count')}구간으로 만들어졌는데, "
            f"지금 컷 설정은 {current_sec:.1f}초 / {current_sig.get('span_count')}구간입니다 "
            f"(차이 {abs(draft_sec - current_sec):.1f}초). "
            "이대로 넣으면 앞부분만 맞고 뒤로 갈수록 자막이 밀립니다. "
            "1차 컷 편집에서 드래프트를 다시 만든 뒤 자막을 넣어 주세요."
        )


def check_cut_signature(draft_path: Path, cut_map: CutMap) -> None:
    """요청서 3.18 — 드래프트와 컷 설정이 어긋나면 자막이 통째로 밀립니다."""
    content = read_content(draft_path)
    marker = content.get(MARKER_KEY) or {}
    draft_sig = marker.get("cut_signature")
    if not draft_sig:
        raise CutSignatureMismatch({"kept_duration_us": 0, "span_count": 0}, cut_map.signature())
    current = cut_map.signature()
    if (
        int(draft_sig.get("span_count", -1)) != current["span_count"]
        or abs(int(draft_sig.get("kept_duration_us", 0)) - current["kept_duration_us"]) > 100_000
    ):
        raise CutSignatureMismatch(draft_sig, current)


def remove_track(content: Dict[str, Any], name: str) -> int:
    """같은 이름의 트랙을 지웁니다 (2차/3차를 여러 번 눌러도 쌓이지 않게)."""
    tracks = content.get("tracks") or []
    removed_material_ids: set[str] = set()
    keep: List[Dict[str, Any]] = []
    removed = 0
    for track in tracks:
        if track.get("name") == name:
            removed += 1
            for segment in track.get("segments", []) or []:
                if segment.get("material_id"):
                    removed_material_ids.add(str(segment["material_id"]))
                for ref in segment.get("extra_material_refs", []) or []:
                    removed_material_ids.add(str(ref))
            continue
        keep.append(track)
    content["tracks"] = keep

    if removed_material_ids:
        materials = content.get("materials") or {}
        for key, items in list(materials.items()):
            if not isinstance(items, list):
                continue
            materials[key] = [
                item
                for item in items
                if not (isinstance(item, dict) and str(item.get("id", "")) in removed_material_ids)
            ]
        content["materials"] = materials
    return removed


def apply_subtitles(
    *,
    draft_path: Path,
    subtitles: Sequence[Subtitle],
    profile: Dict[str, Any],
    cut_map: CutMap,
    session_id: str,
    position: Optional[Dict[str, float]] = None,
    verify_signature: bool = True,
) -> BuildReport:
    """드래프트에 자막 트랙을 넣습니다.

    시간은 **편집본 타임라인 기준**입니다. 넣기 전에 컷 서명을 대조합니다 (3.18).
    """
    ensure_capcut_closed()
    draft_path = Path(draft_path)
    if verify_signature:
        check_cut_signature(draft_path, cut_map)

    overlaps = assert_no_overlap(list(subtitles))
    if overlaps:
        raise BuildError("자막 시간이 겹칩니다:\n- " + "\n- ".join(overlaps[:5]))

    pycapcut = _pycapcut()
    warnings: List[str] = []
    font_path, font_warnings = resolve_font_path(profile)
    warnings.extend(font_warnings)

    # 기존 자동자막 트랙 제거 후 다시 만듭니다.
    content = read_content(draft_path)
    removed = remove_track(content, AUTO_SUBTITLE_TRACK)
    write_content(draft_path, content)

    script = pycapcut.ScriptFile.load_template(str(draft_path / "draft_content.json"))
    script.add_track(pycapcut.TrackType.text, AUTO_SUBTITLE_TRACK)

    pos = position or (profile.get("position") or {})
    clip_settings = pycapcut.ClipSettings(
        transform_x=float(pos.get("x", 0.0)),
        transform_y=float(pos.get("y", -0.73)),
        scale_x=float(pos.get("scale_x", 1.0)),
        scale_y=float(pos.get("scale_y", 1.0)),
    )

    material_ids: List[str] = []
    for sub in subtitles:
        segment = pycapcut.TextSegment(
            sub.text,
            pycapcut.Timerange(sub.start_us, max(1, sub.duration_us)),
            clip_settings=clip_settings,
        )
        script.add_segment(segment, AUTO_SUBTITLE_TRACK)
        material_ids.append(segment.material_id)

    script.save()

    # ── 후처리 주입 ──────────────────────────────────────────────────────
    content = read_content(draft_path)
    injected = _inject_text_style(content, material_ids, profile, font_path)

    marker = content.get(MARKER_KEY) or {}
    marker.update(
        {
            "session_id": session_id,
            "subtitle_count": len(subtitles),
            "subtitle_signature": cut_map.signature(),
        }
    )
    content[MARKER_KEY] = marker
    write_content(draft_path, content)

    result = finalize_draft(draft_path, marker=marker)
    verified = result["verified"]
    warnings.extend(verified["problems"])

    # 화면에는 **요청한 개수가 아니라 실제로 확인된 개수**를 표시합니다 (요청서 7절)
    confirmed = _count_track_segments(read_content(draft_path), AUTO_SUBTITLE_TRACK)
    if confirmed != len(subtitles):
        warnings.append(
            f"자막 {len(subtitles)}개를 넣으려 했는데 파일에서 확인된 건 {confirmed}개입니다. "
            "드래프트를 다시 확인해 주세요."
        )

    return BuildReport(
        draft_name=draft_path.name,
        draft_path=str(draft_path),
        warnings=warnings,
        details={
            "requested": len(subtitles),
            "confirmed": confirmed,
            "removed_previous_tracks": removed,
            "style_injected": injected,
            "font_path": font_path,
            "check_flag": profile.get("check_flag"),
            "background_style": (profile.get("background") or {}).get("style"),
            "verified": verified,
        },
    )


def _inject_text_style(
    content: Dict[str, Any],
    material_ids: Sequence[str],
    profile: Dict[str, Any],
    font_path: str,
) -> Dict[str, int]:
    """pyCapCut이 만든 빈 텍스트 소재를 캘리브레이션 원본 기반 소재로 교체합니다."""
    id_set = {str(mid) for mid in material_ids}
    materials = content.setdefault("materials", {})
    texts: List[Dict[str, Any]] = materials.get("texts") or []

    replaced = 0
    for index, item in enumerate(texts):
        item_id = str(item.get("id", ""))
        if item_id not in id_set:
            continue
        parsed = item.get("content")
        try:
            text_value = json.loads(parsed).get("text", "") if isinstance(parsed, str) else ""
        except json.JSONDecodeError:
            text_value = ""
        texts[index] = build_text_material(profile, text_value, item_id, font_path)
        replaced += 1
    materials["texts"] = texts

    segment_fields = 0
    for track in content.get("tracks", []) or []:
        if track.get("type") != "text":
            continue
        for segment in track.get("segments", []) or []:
            if str(segment.get("material_id", "")) in id_set:
                segment_fields += merge_segment_fields(profile, segment)

    return {"materials_replaced": replaced, "segment_fields_added": segment_fields}


def _count_track_segments(content: Dict[str, Any], track_name: str) -> int:
    for track in content.get("tracks", []) or []:
        if track.get("name") == track_name:
            return len(track.get("segments", []) or [])
    return 0


# ── 3차: 트랜지션 / 효과음 ──────────────────────────────────────────────────
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

# 세로(1080x1920) 실측값 — 상단 문구 +0.6338 / 하단 문구 -0.7394 (요청서 3.2)
VERTICAL_SUBTITLE_Y = -0.7394
VERTICAL_HEADLINE_Y = 0.6338


def list_image_clips(draft_path: Path) -> List[Dict[str, Any]]:
    """드래프트의 이미지 클립을 찾습니다.

    요청서 3차 — `materials.videos[].type == "photo"` 로 **1차 판정**하고,
    확장자로 **교차 검증**합니다.
    """
    content = read_content(draft_path)
    materials = content.get("materials", {}) or {}
    by_id: Dict[str, Dict[str, Any]] = {}
    for item in materials.get("videos") or []:
        if item.get("id"):
            by_id[str(item["id"])] = item

    clips: List[Dict[str, Any]] = []
    for track_index, track in enumerate(content.get("tracks", []) or []):
        if track.get("type") != "video":
            continue
        segments = track.get("segments", []) or []
        for seg_index, segment in enumerate(segments):
            material = by_id.get(str(segment.get("material_id", "")))
            if material is None:
                continue
            is_photo = material.get("type") == "photo"
            suffix = Path(str(material.get("path", ""))).suffix.lower()
            by_ext = suffix in IMAGE_EXTS
            if not is_photo and not by_ext:
                continue
            timerange = segment.get("target_timerange") or {}
            clips.append(
                {
                    "track_index": track_index,
                    "track_name": track.get("name") or "",
                    "segment_index": seg_index,
                    "segment_id": str(segment.get("id", "")),
                    "name": str(material.get("material_name") or Path(str(material.get("path", ""))).name),
                    "path": str(material.get("path", "")),
                    "start_us": int(timerange.get("start", 0) or 0),
                    "duration_us": int(timerange.get("duration", 0) or 0),
                    "type_photo": is_photo,
                    "ext_match": by_ext,
                    "cross_checked": is_photo and by_ext,
                    "has_previous": seg_index > 0,
                    "has_next": seg_index + 1 < len(segments),
                }
            )
    return clips


def apply_transitions(
    *,
    draft_path: Path,
    requests: Sequence[Dict[str, Any]],
    session_id: str,
) -> BuildReport:
    """트랜지션을 붙입니다.

    ⚠️ **트랜지션은 앞쪽 세그먼트에 붙습니다** (pyCapCut 규칙).
    `requests`: [{"track_index":.., "segment_index":.., "enum_name":.., "duration_us":..}, ...]
      segment_index는 **트랜지션을 소유할(=앞쪽) 세그먼트**입니다.
    """
    ensure_capcut_closed()
    pycapcut = _pycapcut()
    from pycapcut import TransitionType

    content = read_content(draft_path)
    tracks = content.get("tracks") or []
    materials = content.setdefault("materials", {})
    transitions: List[Dict[str, Any]] = materials.get("transitions") or []

    warnings: List[str] = []
    applied = 0

    # 같은 세그먼트에 트랜지션이 두 번 붙지 않게 기존 것을 걷어냅니다.
    existing_ids = {str(t.get("id")) for t in transitions}

    for req in requests:
        track_index = int(req.get("track_index", 0))
        segment_index = int(req.get("segment_index", 0))
        enum_name = str(req.get("enum_name") or "")
        duration_us = int(req.get("duration_us") or 500_000)

        if track_index >= len(tracks):
            warnings.append(f"{track_index}번 트랙이 없어 건너뛰었습니다.")
            continue
        segments = tracks[track_index].get("segments") or []
        if segment_index >= len(segments) or segment_index < 0:
            warnings.append(f"{track_index}번 트랙에 {segment_index}번 세그먼트가 없어 건너뛰었습니다.")
            continue
        if segment_index + 1 >= len(segments):
            warnings.append(
                f"{track_index}번 트랙의 {segment_index}번은 마지막 세그먼트라 "
                "뒤에 이어질 클립이 없습니다. 트랜지션을 붙이지 않았습니다."
            )
            continue

        member = getattr(TransitionType, enum_name, None)
        if member is None:
            warnings.append(f"'{enum_name}' 트랜지션을 찾지 못했습니다.")
            continue

        segment = segments[segment_index]
        refs: List[str] = list(segment.get("extra_material_refs") or [])
        # 이 세그먼트에 이미 붙어 있는 트랜지션 제거
        for ref in list(refs):
            if ref in existing_ids and any(
                str(t.get("id")) == ref and t.get("type") == "transition" for t in transitions
            ):
                refs.remove(ref)
                transitions = [t for t in transitions if str(t.get("id")) != ref]

        transition = pycapcut.video_segment.Transition(member, duration_us)
        payload = transition.export_json()
        transitions.append(payload)
        refs.append(payload["id"])
        segment["extra_material_refs"] = refs
        applied += 1

    materials["transitions"] = transitions
    write_content(draft_path, content)

    marker = (read_content(draft_path).get(MARKER_KEY) or {})
    marker.update({"session_id": session_id, "transition_count": applied})
    result = finalize_draft(draft_path, marker=marker)
    warnings.extend(result["verified"]["problems"])

    confirmed = result["verified"]["transition_count"]
    return BuildReport(
        draft_name=Path(draft_path).name,
        draft_path=str(draft_path),
        warnings=warnings,
        details={"requested": len(requests), "applied": applied, "confirmed_in_file": confirmed},
    )


def apply_sfx(
    *,
    draft_path: Path,
    placements: Sequence[Dict[str, Any]],
    session_id: str,
) -> BuildReport:
    """효과음 트랙을 만듭니다.

    `placements`: [{"path":.., "start_us":.., "volume":..}, ...]
    """
    ensure_capcut_closed()
    pycapcut = _pycapcut()

    content = read_content(draft_path)
    removed = remove_track(content, AUTO_SFX_TRACK)
    write_content(draft_path, content)

    warnings: List[str] = []
    if not placements:
        return BuildReport(
            Path(draft_path).name, str(draft_path), [],
            {"requested": 0, "applied": 0, "removed_previous_tracks": removed},
        )

    script = pycapcut.ScriptFile.load_template(str(draft_path / "draft_content.json"))
    script.add_track(pycapcut.TrackType.audio, AUTO_SFX_TRACK)

    applied = 0
    cursor_guard: List[Tuple[int, int]] = []
    for item in placements:
        path = str(item.get("path") or "")
        if not Path(path).is_file():
            warnings.append(f"효과음 파일 '{path}' 이(가) 없어 건너뛰었습니다.")
            continue
        try:
            material = pycapcut.AudioMaterial(path)
        except Exception as exc:
            warnings.append(f"효과음 '{Path(path).name}' 을(를) 읽지 못했습니다: {exc}")
            continue

        start = max(0, int(item.get("start_us") or 0))
        duration = int(material.duration)
        # 같은 트랙에서 겹치면 pyCapCut이 거부하므로 뒤로 밉니다.
        for begin, end in cursor_guard:
            if start < end and begin < start + duration:
                start = end
        cursor_guard.append((start, start + duration))

        segment = pycapcut.AudioSegment(
            material,
            pycapcut.Timerange(start, duration),
            source_timerange=pycapcut.Timerange(0, duration),
            volume=float(item.get("volume", 1.0)),
        )
        script.add_segment(segment, AUTO_SFX_TRACK)
        applied += 1

    script.save()
    marker = (read_content(draft_path).get(MARKER_KEY) or {})
    marker.update({"session_id": session_id, "sfx_count": applied})
    result = finalize_draft(draft_path, marker=marker)
    warnings.extend(result["verified"]["problems"])

    confirmed = _count_track_segments(read_content(draft_path), AUTO_SFX_TRACK)
    return BuildReport(
        draft_name=Path(draft_path).name,
        draft_path=str(draft_path),
        warnings=warnings,
        details={
            "requested": len(placements),
            "applied": applied,
            "confirmed": confirmed,
            "removed_previous_tracks": removed,
        },
    )


# ── 5차: 세로 드래프트 ──────────────────────────────────────────────────────
def _has_final_consonant(text: str) -> bool:
    """마지막 글자에 받침이 있는지. 한글 조사(와/과, 은/는)를 고르는 데 씁니다."""
    if not text:
        return False
    last = text[-1]
    if not ("가" <= last <= "힣"):
        return False
    return (ord(last) - 0xAC00) % 28 != 0


def _join_ko(items: List[str]) -> str:
    """['가방', '구간'] → '가방과 구간'. 받침에 따라 와/과를 고릅니다."""
    if not items:
        return ""
    joined = items[0]
    for item in items[1:]:
        joined += ("과 " if _has_final_consonant(joined) else "와 ") + item
    return joined


def build_vertical_draft(
    *,
    draft_name: str,
    timeline: SourceTimeline,
    cut_map: CutMap,
    edit_start_us: int,
    edit_end_us: int,
    subtitles: Sequence[Subtitle],
    profile: Dict[str, Any],
    background_image: Optional[str],
    overlay_scale: float,
    overlay_y: float,
    subtitle_y: Optional[float],
    session_id: str,
    allow_replace: bool = True,
) -> BuildReport:
    """1080x1920 세로 드래프트.

    ⚠️ 사용자가 고르는 구간은 자막 타임라인 = **컷 편집 후** 시각인데,
       실제로 잘라 쓸 소재는 **원본**입니다. 그 사이 잘려나간 컷이 있으면
       원본에서는 여러 조각이 됩니다. → 편집본 → 원본 역변환을 반드시 합니다.
    """
    ensure_capcut_closed()
    pycapcut = _pycapcut()
    root = require_root()

    width, height = 1080, 1920
    _, _, fps = timeline.canvas

    folder = pycapcut.DraftFolder(str(root))
    script = folder.create_draft(draft_name, width, height, fps=fps, allow_replace=allow_replace)

    warnings: List[str] = []

    # 편집본 구간 → 원본 조각들 (역변환) → 영상 경계로 다시 쪼개기
    orig_ranges = cut_map.edit_range_to_orig_ranges(edit_start_us, edit_end_us)
    if not orig_ranges:
        raise BuildError("선택한 구간이 컷 편집으로 전부 잘려나갔습니다. 다른 구간을 골라 주세요.")

    script.add_track(pycapcut.TrackType.video, "배경")
    script.add_track(pycapcut.TrackType.video, "오버레이", relative_index=1)

    total = 0
    if background_image and Path(background_image).is_file():
        bg_material = load_material(background_image)
    else:
        bg_material = None
        warnings.append(
            "배경 이미지를 고르지 않았습니다. assets/bg/ 에 이미지를 넣으면 상하 여백을 채울 수 있습니다."
        )

    placed = 0
    used_sources: set[int] = set()
    for orig_start, orig_end in orig_ranges:
        for span in timeline.split(orig_start, orig_end):
            used_sources.add(span.source_index)
            material = load_material(span.path)
            clamped = clamp_span(material, span.start_us, span.end_us)
            if clamped is None:
                continue
            start_us, end_us = clamped
            duration = end_us - start_us
            segment = pycapcut.VideoSegment(
                material,
                pycapcut.Timerange(total, duration),
                source_timerange=pycapcut.Timerange(start_us, duration),
                speed=1.0,
                clip_settings=pycapcut.ClipSettings(
                    scale_x=overlay_scale, scale_y=overlay_scale, transform_y=overlay_y
                ),
            )
            script.add_segment(segment, "오버레이")
            total += duration
            placed += 1

    if placed == 0:
        raise BuildError("세로 드래프트에 넣을 영상 구간이 없습니다.")

    crossed_sources = len(used_sources)
    if placed > 1:
        # 조각이 생기는 이유는 두 가지입니다: 컷으로 잘려나간 구간, 그리고 영상 경계.
        reasons: List[str] = []
        if len(orig_ranges) > 1:
            reasons.append("컷 편집으로 잘려나간 구간")
        if crossed_sources > 1:
            reasons.append("영상 파일 경계")
        why = _join_ko(reasons) or "분할 지점"
        warnings.append(
            f"선택한 구간은 {why} 때문에 원본에서 {placed}조각으로 나뉩니다. "
            "조각들을 순서대로 이어 붙였습니다."
        )

    if bg_material is not None:
        bg_clamped = clamp_span(bg_material, 0, total)
        if bg_clamped is not None:
            script.add_segment(
                pycapcut.VideoSegment(
                    bg_material,
                    pycapcut.Timerange(0, bg_clamped[1]),
                    source_timerange=pycapcut.Timerange(0, bg_clamped[1]),
                    clip_settings=pycapcut.ClipSettings(scale_x=1.0, scale_y=1.0, transform_x=0.0, transform_y=0.0),
                ),
                "배경",
            )

    # 구간 안에 들어오는 자막만 골라 0 기준으로 당깁니다.
    picked: List[Subtitle] = []
    for sub in subtitles:
        start = max(sub.start_us, edit_start_us)
        end = min(sub.end_us, edit_end_us)
        if end - start < 100_000:
            continue
        picked.append(Subtitle(0, start - edit_start_us, end - edit_start_us, sub.text, sub.source, sub.confidence))

    from .script_align import sanitize

    picked = sanitize(picked)
    for pos, sub in enumerate(picked, start=1):
        sub.index = pos

    material_ids: List[str] = []
    if picked:
        script.add_track(pycapcut.TrackType.text, AUTO_SUBTITLE_TRACK)
        pos_cfg = profile.get("position") or {}
        y_value = subtitle_y if subtitle_y is not None else _rescale_y(profile, height)
        clip_settings = pycapcut.ClipSettings(
            transform_x=float(pos_cfg.get("x", 0.0)),
            transform_y=float(y_value),
            scale_x=float(pos_cfg.get("scale_x", 1.0)),
            scale_y=float(pos_cfg.get("scale_y", 1.0)),
        )
        for sub in picked:
            segment = pycapcut.TextSegment(
                sub.text,
                pycapcut.Timerange(sub.start_us, max(1, sub.duration_us)),
                clip_settings=clip_settings,
            )
            script.add_segment(segment, AUTO_SUBTITLE_TRACK)
            material_ids.append(segment.material_id)

    script.save()
    draft_path = root / draft_name

    if material_ids:
        font_path, font_warnings = resolve_font_path(profile)
        warnings.extend(font_warnings)
        content = read_content(draft_path)
        _inject_text_style(content, material_ids, profile, font_path)
        write_content(draft_path, content)

    marker = {
        "session_id": session_id,
        "stage": "vertical",
        "edit_start_us": edit_start_us,
        "edit_end_us": edit_end_us,
        "orig_ranges": orig_ranges,
    }
    result = finalize_draft(draft_path, marker=marker)
    warnings.extend(result["verified"]["problems"])

    return BuildReport(
        draft_name=draft_name,
        draft_path=str(draft_path),
        warnings=warnings,
        details={
            "duration_us": total,
            "clip_pieces": placed,
            "orig_range_count": len(orig_ranges),
            "subtitle_count": len(picked),
            "overlay_scale": overlay_scale,
            "overlay_y": overlay_y,
            "verified": result["verified"],
        },
    )


def _rescale_y(profile: Dict[str, Any], new_height: int) -> float:
    """세로 캔버스에서 쓸 자막 Y(정규화)를 정합니다.

    요청서 3.2 — 정규화 좌표는 **캔버스 절반 높이가 1**입니다. 가로(16:9)에서 읽은
    값을 픽셀로 환산해 세로(9:16)에 그대로 넣으면 화면 한가운데로 올라옵니다.
    화면상 "아래쪽"이라는 의미를 유지하려면 **정규화 값을 그대로** 써야 합니다.

    - 캘리브레이션 캔버스가 이미 세로면 그 값을 그대로 사용
    - 가로에서 읽었으면 세로 실측값(하단 문구 -0.7394)을 기본값으로 사용
    """
    pos = profile.get("position") or {}
    canvas = profile.get("canvas") or {}
    old_width = int(canvas.get("width") or 1920)
    old_height = int(canvas.get("height") or 1080)
    y_norm = float(pos.get("y", VERTICAL_SUBTITLE_Y))

    if old_height >= old_width:  # 이미 세로 캔버스에서 읽은 값
        return y_norm
    return VERTICAL_SUBTITLE_Y
