"""드래프트 후처리 교정 검증 (요청서 3.3 / 3.4 / 3.6 / 3.10 / 3.11 / 3.12)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import capcut_draft as cd

TEMPLATE_DRAFT_ID = "792BD5DA-E961-4821-B10E-F51E4683DEC0"


def make_draft(root: Path, name: str = "테스트드래프트", *, ratio="original", width=1920, height=1080) -> Path:
    """pyCapCut이 만든 것과 같은 모양의 드래프트를 흉내 냅니다."""
    path = root / name
    path.mkdir(parents=True, exist_ok=True)
    content = {
        "canvas_config": {"width": width, "height": height, "ratio": ratio},
        "duration": 5_000_000,
        "fps": 30.0,
        "materials": {
            "videos": [
                {"id": "v1", "type": "video", "path": "C:/a.mp4", "material_name": "a.mp4"},
                {"id": "p1", "type": "photo", "path": "C:/b.png", "material_name": "b.png"},
            ],
            "texts": [{"id": "t1", "content": json.dumps({"text": "자막", "styles": [{}]}), "check_flag": 7}],
            "transitions": [],
        },
        "tracks": [
            {
                "type": "video", "name": "메인", "id": "trk1",
                # pyCapCut은 track_render_index를 전부 0으로 둡니다
                "segments": [
                    {"id": "s1", "material_id": "v1", "track_render_index": 0, "render_index": 0,
                     "target_timerange": {"start": 0, "duration": 3_000_000}, "extra_material_refs": []},
                    {"id": "s2", "material_id": "p1", "track_render_index": 0, "render_index": 0,
                     "target_timerange": {"start": 3_000_000, "duration": 2_000_000}, "extra_material_refs": []},
                ],
            },
            {
                "type": "text", "name": "자동자막", "id": "trk2",
                "segments": [
                    {"id": "s3", "material_id": "t1", "track_render_index": 0, "render_index": 15000,
                     "target_timerange": {"start": 0, "duration": 2_000_000}, "extra_material_refs": []},
                ],
            },
        ],
    }
    (path / "draft_content.json").write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    (path / "draft_meta_info.json").write_text(
        json.dumps({"draft_fold_path": "", "draft_name": "", "draft_id": TEMPLATE_DRAFT_ID}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


# ── 3.3 레이어 ───────────────────────────────────────────────────────────
def test_fix_track_render_index_prevents_hidden_subtitles(tmp_path):
    path = make_draft(tmp_path)
    content = cd.read_content(path)

    before = [s["track_render_index"] for t in content["tracks"] for s in t["segments"]]
    assert before == [0, 0, 0]  # pyCapCut 상태: 비디오와 텍스트가 같은 레이어

    changed = cd.fix_track_render_index(content)
    after = [[s["track_render_index"] for s in t["segments"]] for t in content["tracks"]]
    assert after == [[0, 0], [1]]  # 트랙 순서대로 다시 매겨졌습니다
    assert changed == 1


def test_inspect_draft_reports_layer_problem(tmp_path):
    path = make_draft(tmp_path)
    report = cd.inspect_draft(path)
    assert any("track_render_index" in p for p in report["problems"])
    assert report["tracks"][1]["layered_correctly"] is False


# ── 3.10 캔버스 비율 ─────────────────────────────────────────────────────
def test_fix_canvas_ratio_for_vertical(tmp_path):
    path = make_draft(tmp_path, "세로", ratio="original", width=1080, height=1920)
    content = cd.read_content(path)
    assert cd.fix_canvas_ratio(content) is True
    assert content["canvas_config"]["ratio"] == "9:16"


def test_fix_canvas_ratio_leaves_horizontal_alone(tmp_path):
    path = make_draft(tmp_path, "가로")
    content = cd.read_content(path)
    assert cd.fix_canvas_ratio(content) is False
    assert content["canvas_config"]["ratio"] == "original"


# ── 3.11 메타 경로 / draft_id ────────────────────────────────────────────
def test_fix_meta_paths_overwrites_with_real_path(tmp_path):
    path = make_draft(tmp_path)
    meta = cd.fix_meta_paths(path)
    assert meta["draft_fold_path"] == str(path)
    assert meta["draft_name"] == path.name
    saved = json.loads((path / "draft_meta_info.json").read_text(encoding="utf-8"))
    assert saved["draft_fold_path"] == str(path)


def test_template_draft_id_is_replaced(tmp_path):
    """추가 발견 — 번들 템플릿의 draft_id가 고정 상수라 모든 드래프트가 같은 id를 갖습니다."""
    first = cd.fix_meta_paths(make_draft(tmp_path, "A"))
    second = cd.fix_meta_paths(make_draft(tmp_path, "B"))
    assert first["draft_id"] != TEMPLATE_DRAFT_ID
    assert second["draft_id"] != TEMPLATE_DRAFT_ID
    assert first["draft_id"] != second["draft_id"]


# ── 3.12 레지스트리 등록 ─────────────────────────────────────────────────
def test_register_in_registry_creates_entry(tmp_path):
    path = make_draft(tmp_path)
    cd.register_in_registry(path)
    registry = json.loads((tmp_path / "root_meta_info.json").read_text(encoding="utf-8"))
    entries = registry["all_draft_store"]
    assert len(entries) == 1
    assert entries[0]["draft_fold_path"] == str(path)
    assert entries[0]["draft_name"] == path.name


def test_register_in_registry_is_idempotent(tmp_path):
    path = make_draft(tmp_path)
    cd.register_in_registry(path)
    cd.register_in_registry(path)
    registry = json.loads((tmp_path / "root_meta_info.json").read_text(encoding="utf-8"))
    assert len(registry["all_draft_store"]) == 1


def test_finalize_draft_fixes_everything_and_verifies(tmp_path):
    path = make_draft(tmp_path, "세로", ratio="original", width=1080, height=1920)
    result = cd.finalize_draft(path, marker={"session_id": "s1"})
    assert result["layer_fixes"] == 1
    assert result["ratio_fixed"] is True
    assert result["registry"]["registered"] is True
    assert result["verified"]["problems"] == []
    assert result["verified"]["canvas"]["ratio"] == "9:16"
    assert result["verified"]["marker"]["session_id"] == "s1"


# ── 백업 / 되돌리기 ──────────────────────────────────────────────────────
def test_backup_and_restore_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "BACKUP_DIR", tmp_path / "backups")
    (tmp_path / "backups").mkdir()
    path = make_draft(tmp_path)

    record = cd.backup_draft(path, tag="테스트")
    assert Path(record["path"]).is_dir()

    content = cd.read_content(path)
    content["duration"] = 999
    cd.write_content(path, content)
    assert cd.read_content(path)["duration"] == 999

    cd.restore_backup(Path(record["path"]), path)
    assert cd.read_content(path)["duration"] == 5_000_000


# ── 3.6 폰트 계열 이름 매칭 ──────────────────────────────────────────────
def test_font_family_must_match_exactly(tmp_path, monkeypatch):
    """'Bold'만 맞아도 통과시키면 Arial Bold → NotoSansKR-Bold 오매칭이 납니다."""
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    for name in ["NotoSansKR-Bold.otf", "Pretendard-Bold.otf", "Pretendard-Regular.otf"]:
        (fonts / name).write_bytes(b"x")
    monkeypatch.setattr(cd, "font_dirs", lambda: [fonts])

    assert cd.find_font_file("Pretendard", "Bold").endswith("Pretendard-Bold.otf")
    assert cd.find_font_file("Pretendard", "Regular").endswith("Pretendard-Regular.otf")
    # 계열 이름이 없으면 다른 글꼴로 대체하지 않고 실패합니다
    assert cd.find_font_file("Arial", "Bold") is None


def test_parse_font_name():
    assert cd.parse_font_name("Pretendard-Bold.otf") == ("Pretendard", "Bold")
    assert cd.parse_font_name("NotoSansKR_SemiBold.ttf") == ("NotoSansKR", "SemiBold")
    assert cd.parse_font_name("malgun.ttf") == ("malgun", "")


# ── 캘리브레이션 ─────────────────────────────────────────────────────────
def make_capcut_style_draft(root: Path) -> Path:
    """캡컷이 만든 자막(흰 배경 + 검정 글씨)을 흉내 냅니다."""
    path = root / "참조"
    path.mkdir(parents=True, exist_ok=True)
    material = {
        "id": "t1",
        "content": json.dumps({
            "text": "원본 자막",
            "styles": [{
                "fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [0, 0, 0]}}},
                "range": [0, 5], "size": 5.0, "bold": True, "italic": False,
                "strokes": [{"content": {"solid": {"alpha": 1.0, "color": [1, 1, 1]}}, "width": 0.08}],
                "font": {"id": "res123", "path": "C:/Users/u/AppData/Local/Microsoft/Windows/Fonts/Pretendard-Bold.otf"},
            }],
        }, ensure_ascii=False),
        "check_flag": 31,
        "background_color": "#ffffff", "background_style": 1, "background_alpha": 1.0,
        "background_width": 0.28, "background_height": 0.28, "background_round_radius": 0.4,
        "text_color": "#000000", "font_size": 5.0,
        "border_color": "#ffffff", "border_width": 0.08,
        "alignment": 1, "letter_spacing": 0, "line_spacing": 0.02, "line_max_width": 0.82,
        "words": {"start_time": [], "end_time": [], "text": []},
        # 캡컷 실물에만 있는 필드들
        "template_scene": "default", "source": 0, "shadow_alpha": 0.9, "border_mode": 0,
        "layer_weight": 1, "recognize_type": 0, "typesetting": 0, "line_feed": 1,
    }
    content = {
        "canvas_config": {"width": 1080, "height": 1920, "ratio": "9:16"},
        "duration": 5_000_000, "fps": 30.0,
        "materials": {"videos": [], "texts": [material], "transitions": [
            {"id": "tr1", "effect_id": "6725771847444468236", "resource_id": "r1",
             "name": "故障", "duration": 500000, "is_overlap": False}
        ]},
        "tracks": [
            {"type": "video", "name": "", "segments": []},
            {"type": "text", "name": "자막", "segments": [{
                "id": "seg1", "material_id": "t1", "track_render_index": 1, "render_index": 15000,
                "target_timerange": {"start": 0, "duration": 2_000_000},
                "clip": {"transform": {"x": 0.0, "y": -0.7394}, "scale": {"x": 1.0, "y": 1.0}},
                "extra_material_refs": [],
                "template_scene": "default", "state": 0, "enable_video_mask": True,
                "responsive_layout": {}, "visible": True,
            }]},
        ],
    }
    (path / "draft_content.json").write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    return path


def test_calibration_captures_whole_original_material(tmp_path):
    """3.5 — 원본 소재를 통째로 저장해야 합니다."""
    path = make_capcut_style_draft(tmp_path)
    result = cd.calibrate_text_style(path)
    profile = result.profile

    assert profile["check_flag"] == 31
    assert profile["background"]["style"] == 1
    assert profile["background"]["color"] == "#ffffff"
    assert profile["text"]["color_hex"] == "#000000"
    assert profile["font"]["family"] == "Pretendard"
    assert profile["font"]["style"] == "Bold"
    assert profile["position"]["y"] == pytest.approx(-0.7394)
    assert profile["position"]["y_px"] == pytest.approx(-709.8, abs=0.2)
    assert profile["canvas"] == {"width": 1080, "height": 1920}
    # 원본이 통째로 보관되어야 합니다
    assert profile["raw_material"]["template_scene"] == "default"
    assert profile["raw_segment"]["enable_video_mask"] is True
    assert profile["raw_material_field_count"] > 15


def test_calibration_warns_when_background_style_is_zero(tmp_path):
    path = make_capcut_style_draft(tmp_path)
    content = cd.read_content(path)
    content["materials"]["texts"][0]["background_style"] = 0
    cd.write_content(path, content)

    result = cd.calibrate_text_style(path)
    assert result.profile["background"]["style"] == 1  # 1로 올려 저장
    assert any("background_style" in w for w in result.warnings)


def test_calibration_rejects_draft_without_text(tmp_path):
    path = make_draft(tmp_path, "빈드래프트")
    content = cd.read_content(path)
    content["tracks"] = [t for t in content["tracks"] if t["type"] != "text"]
    cd.write_content(path, content)
    with pytest.raises(ValueError, match="텍스트 트랙이 없습니다"):
        cd.calibrate_text_style(path)


def test_transition_effect_id_reverse_lookup(tmp_path):
    """3.7 — 이름은 중국어라 못 쓰지만 effect_id 역조회는 정확합니다."""
    path = make_capcut_style_draft(tmp_path)
    out = cd.calibrate_transitions(path)
    assert len(out) == 1
    assert out[0]["effect_id"] == "6725771847444468236"
    assert out[0]["matched"] is True
    assert out[0]["enum_name"]  # pycapcut 멤버 이름을 찾았습니다


def test_manual_profile_fills_required_fields():
    result = cd.manual_style_profile({"canvas_height": 1080, "position_y_px": -394})
    profile = result.profile
    assert profile["manual"] is True
    assert profile["check_flag"] == 31
    assert profile["background"]["style"] >= 1
    assert profile["position"]["y"] == pytest.approx(-394 / 540, abs=1e-6)
    assert any("추정값" in w for w in result.warnings)


def test_manual_profile_rejects_background_style_zero():
    result = cd.manual_style_profile({"background_style": 0})
    assert result.profile["background"]["style"] == 1
    assert any("background_style" in w for w in result.warnings)
