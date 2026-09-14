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


# ── 캡컷이 파싱하는 파일을 오염시키지 않기 ────────────────────────────────
def test_marker_lives_outside_draft_content(tmp_path):
    """draft_content.json 은 캡컷이 파싱하는 파일입니다.

    캡컷에 없는 최상위 키를 넣으면 드래프트를 열 때 문제가 될 수 있으므로
    우리 표식은 별도 파일에 둡니다.
    """
    path = make_draft(tmp_path)
    cd.finalize_draft(path, marker={"session_id": "s1", "cut_signature": {"span_count": 3}})

    content = cd.read_content(path)
    assert cd.MARKER_KEY not in content
    assert (path / cd.MARKER_FILE).is_file()

    marker = cd.read_marker(path)
    assert marker["session_id"] == "s1"
    assert marker["cut_signature"]["span_count"] == 3


def test_legacy_marker_inside_content_is_removed(tmp_path):
    """예전 버전이 넣어 둔 키는 다음 저장 때 걷어냅니다."""
    path = make_draft(tmp_path)
    content = cd.read_content(path)
    content[cd.MARKER_KEY] = {"session_id": "old", "cut_signature": {"span_count": 1}}
    cd.write_content(path, content)

    # 사이드카가 없어도 예전 값을 읽어 줍니다
    assert cd.read_marker(path)["session_id"] == "old"

    result = cd.finalize_draft(path)
    assert result["legacy_marker_removed"] is True
    assert cd.MARKER_KEY not in cd.read_content(path)


def test_marker_updates_merge(tmp_path):
    path = make_draft(tmp_path)
    cd.write_marker(path, {"session_id": "s1", "cut_signature": {"span_count": 2}})
    cd.write_marker(path, {"subtitle_count": 5})
    marker = cd.read_marker(path)
    assert marker["session_id"] == "s1"
    assert marker["subtitle_count"] == 5


# ── 캡컷 버전 메타 ────────────────────────────────────────────────────────
def test_calibration_captures_version_meta(tmp_path):
    """번들 템플릿은 app_version 6.7.0 을 주장합니다. 실물 값을 가져와야 합니다."""
    path = make_capcut_style_draft(tmp_path)
    content = cd.read_content(path)
    content["version"] = 360000
    content["new_version"] = "63.0.0"
    content["platform"] = {"app_id": 359289, "app_source": "cc", "app_version": "9.3.0", "os": "windows"}
    cd.write_content(path, content)

    profile = cd.calibrate_text_style(path).profile
    meta = profile["version_meta"]
    assert meta["platform"]["app_version"] == "9.3.0"
    assert meta["new_version"] == "63.0.0"
    assert meta["version"] == 360000


def test_finalize_applies_reference_version(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(
        config, "load_style_profile",
        lambda: {"version_meta": {
            "platform": {"app_id": 359289, "app_source": "cc", "app_version": "9.3.0", "os": "windows"},
            "new_version": "63.0.0",
        }},
    )
    path = make_draft(tmp_path)
    content = cd.read_content(path)
    content["platform"] = {"app_version": "6.7.0"}  # pyCapCut 번들 값
    cd.write_content(path, content)

    result = cd.finalize_draft(path)
    assert result["version_applied"]["platform"]["app_version"] == "9.3.0"
    assert cd.read_content(path)["platform"]["app_version"] == "9.3.0"
    assert cd.read_content(path)["new_version"] == "63.0.0"


def test_finalize_without_profile_leaves_version_alone(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "load_style_profile", lambda: None)
    path = make_draft(tmp_path)
    content = cd.read_content(path)
    content["platform"] = {"app_version": "6.7.0"}
    cd.write_content(path, content)

    result = cd.finalize_draft(path)
    assert result["version_applied"] == {}
    assert cd.read_content(path)["platform"]["app_version"] == "6.7.0"


# ── 레지스트리 안전장치 ──────────────────────────────────────────────────
def test_registry_is_never_clobbered_when_unreadable(tmp_path, monkeypatch):
    """⚠️ root_meta_info.json 은 캡컷 프로젝트 목록의 정본입니다.

    읽지 못했다고 새로 만들어 덮어쓰면 사용자의 프로젝트가 통째로 사라집니다.
    """
    monkeypatch.setattr(cd, "BACKUP_DIR", tmp_path / "backups")
    (tmp_path / "backups").mkdir()
    path = make_draft(tmp_path)
    registry = tmp_path / "root_meta_info.json"
    broken = '{"all_draft_store": [{"draft_name": "중요한프로젝트"'  # 잘린 JSON
    registry.write_text(broken, encoding="utf-8")

    with pytest.raises(cd.RegistryUnreadable, match="해석하지 못해"):
        cd.register_in_registry(path)

    # 원본이 그대로 남아 있어야 합니다
    assert registry.read_text(encoding="utf-8") == broken


def test_registry_backup_is_taken_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "BACKUP_DIR", tmp_path / "backups")
    (tmp_path / "backups").mkdir()
    path = make_draft(tmp_path)
    registry = tmp_path / "root_meta_info.json"
    registry.write_text(
        json.dumps({"all_draft_store": [{"draft_fold_path": "C:/기존", "draft_name": "기존"}]}),
        encoding="utf-8",
    )

    result = cd.register_in_registry(path)
    assert result["entries_before"] == 1
    assert result["entries_after"] == 2
    backup = Path(result["registry_backup"])
    assert backup.is_file()
    # 백업에는 등록 전 상태가 들어 있어야 합니다
    assert len(json.loads(backup.read_text(encoding="utf-8"))["all_draft_store"]) == 1
    # 기존 항목이 살아 있어야 합니다
    written = json.loads(registry.read_text(encoding="utf-8"))
    assert any(e["draft_name"] == "기존" for e in written["all_draft_store"])


def test_registry_handles_bom(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "BACKUP_DIR", tmp_path / "backups")
    (tmp_path / "backups").mkdir()
    path = make_draft(tmp_path)
    registry = tmp_path / "root_meta_info.json"
    registry.write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"all_draft_store": []}).encode("utf-8")
    )
    result = cd.register_in_registry(path)
    assert result["entries_after"] == 1


def test_restore_registry_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(cd, "BACKUP_DIR", tmp_path / "backups")
    (tmp_path / "backups").mkdir()
    path = make_draft(tmp_path)
    registry = tmp_path / "root_meta_info.json"
    original = {"all_draft_store": [{"draft_fold_path": "C:/기존", "draft_name": "기존"}]}
    registry.write_text(json.dumps(original), encoding="utf-8")

    result = cd.register_in_registry(path)
    assert len(json.loads(registry.read_text(encoding="utf-8"))["all_draft_store"]) == 2

    cd.restore_registry(Path(result["registry_backup"]), tmp_path)
    restored = json.loads(registry.read_text(encoding="utf-8"))
    assert len(restored["all_draft_store"]) == 1
    assert restored["all_draft_store"][0]["draft_name"] == "기존"


# ── pyCapCut이 떨어뜨리는 필드 복원 ──────────────────────────────────────
# ⚠️ 실제로 겪은 문제 — 이 도구가 만든 드래프트를 열면 캡컷이 종료됐습니다.
#    결과물을 번들 템플릿과 필드 단위로 비교해 보니 아래가 사라져 있었습니다.
def test_bundled_template_keys_are_restored(tmp_path):
    path = make_draft(tmp_path)
    content = cd.read_content(path)
    # pyCapCut의 dumps()가 만들어 내는 상태를 재현합니다
    content["canvas_config"] = {"width": 1080, "height": 1920, "ratio": "original"}
    content["materials"].pop("common_mask", None)
    cd.write_content(path, content)

    cd.finalize_draft(path)
    restored = cd.read_content(path)

    assert "background" in restored["canvas_config"], "dumps()가 떨어뜨린 background 가 복원되지 않았습니다"
    template = cd.extract_skeleton(cd.bundled_template())
    for key in template["materials_keys"]:
        assert key in restored["materials"], f"materials.{key} 가 복원되지 않았습니다"


def test_restore_does_not_overwrite_our_values(tmp_path):
    path = make_draft(tmp_path)
    content = cd.read_content(path)
    content["canvas_config"] = {"width": 1080, "height": 1920, "ratio": "original", "background": "우리값"}
    cd.write_content(path, content)

    cd.finalize_draft(path)
    restored = cd.read_content(path)
    assert restored["canvas_config"]["background"] == "우리값"
    assert restored["canvas_config"]["width"] == 1080


def test_inspect_reports_dropped_fields(tmp_path):
    path = make_draft(tmp_path)
    content = cd.read_content(path)
    content["canvas_config"] = {"width": 1920, "height": 1080, "ratio": "original"}
    content["materials"].pop("common_mask", None)
    cd.write_content(path, content)

    report = cd.inspect_draft(path)
    assert any("canvas_config" in p and "background" in p for p in report["problems"])
    assert any("materials" in p and "common_mask" in p for p in report["problems"])


def test_skeleton_from_reference_fills_video_material_fields(tmp_path, monkeypatch):
    """참조 드래프트의 비디오 소재에만 있는 필드를 새 드래프트에 채웁니다."""
    reference = {
        "canvas_config": {"width": 1080, "height": 1920, "ratio": "9:16", "background": None},
        "materials": {
            "videos": [{
                "id": "ref", "type": "video", "path": "C:/ref.mp4", "duration": 1000,
                "width": 100, "height": 100,
                # 캡컷 실물에만 있는 필드들
                "stable": {"matrix_path": "", "stable_level": 0}, "matting": {"flag": 0},
                "video_algorithm": {}, "aigc_type": "none",
            }],
            "texts": [], "transitions": [],
        },
        "tracks": [{"type": "video", "name": "", "segments": [], "attribute": 0, "flag": 0}],
    }
    skeleton = cd.extract_skeleton(reference)
    assert skeleton["video_material"]["aigc_type"] == "none"

    from app import config

    monkeypatch.setattr(config, "load_style_profile", lambda: {"draft_skeleton": skeleton})

    path = make_draft(tmp_path)
    cd.finalize_draft(path)
    material = cd.read_content(path)["materials"]["videos"][0]
    assert material["aigc_type"] == "none"
    assert material["matting"] == {"flag": 0}
    # 우리 값은 그대로여야 합니다
    assert material["id"] == "v1"
    assert material["path"] == "C:/a.mp4"


def test_calibration_stores_draft_skeleton(tmp_path):
    path = make_capcut_style_draft(tmp_path)
    profile = cd.calibrate_text_style(path).profile
    skeleton = profile["draft_skeleton"]
    assert "canvas_extra" in skeleton
    assert "materials_keys" in skeleton
    assert isinstance(skeleton["materials_keys"], list)


# ── ⚠️ pyCapCut 0.0.3 — 자막이 없는 소재를 가리키는 버그 ────────────────
def test_pycapcut_leaves_text_speed_dangling():
    """버그를 소스 수준에서 재현합니다. 이게 캡컷이 꺼지는 원인이었습니다.

    TextSegment 는 MediaSegment 를 상속해 Speed 객체를 갖고 그 id 를
    extra_material_refs 에 넣지만, ScriptFile.add_segment 는 VideoSegment 와
    AudioSegment 에만 materials.speeds 를 채웁니다.
    """
    import pycapcut

    script = pycapcut.ScriptFile(1920, 1080, 30)
    script.add_track(pycapcut.TrackType.text, "자막")
    script.add_segment(pycapcut.TextSegment("테스트", pycapcut.Timerange(0, 1_000_000)), "자막")
    content = json.loads(script.dumps())

    refs = content["tracks"][0]["segments"][0]["extra_material_refs"]
    assert refs, "TextSegment 는 speed id 를 참조합니다"
    assert content["materials"]["speeds"] == [], "pyCapCut은 텍스트의 speed를 목록에 넣지 않습니다"
    assert find_dangling(content), "끊어진 참조가 재현되어야 합니다"


def find_dangling(content):
    return cd.find_dangling_refs(content)


def _text_draft(root, count=3):
    """자막이 든 드래프트를 pyCapCut으로 만듭니다(끊어진 참조가 생깁니다)."""
    import pycapcut

    path = root / "자막드래프트"
    path.mkdir(parents=True, exist_ok=True)
    script = pycapcut.ScriptFile(1080, 1920, 30)
    script.add_track(pycapcut.TrackType.text, "자동자막")
    for i in range(count):
        script.add_segment(
            pycapcut.TextSegment(f"자막 {i}", pycapcut.Timerange(i * 2_000_000, 1_500_000)),
            "자동자막",
        )
    (path / "draft_content.json").write_text(script.dumps(), encoding="utf-8")
    (path / "draft_meta_info.json").write_text("{}", encoding="utf-8")
    return path


def test_finalize_repairs_dangling_text_refs(tmp_path):
    path = _text_draft(tmp_path, count=5)
    before = cd.read_content(path)
    assert len(cd.find_dangling_refs(before)) == 5

    result = cd.finalize_draft(path)
    assert result["refs_fixed"]["added_speeds"] == 5
    assert result["refs_fixed"]["dropped_refs"] == 0

    after = cd.read_content(path)
    assert cd.find_dangling_refs(after) == [], "끊어진 참조가 남아 있으면 캡컷이 꺼집니다"
    # 채워 넣은 speed 소재가 실제로 있어야 합니다
    speed_ids = {s["id"] for s in after["materials"]["speeds"]}
    for track in after["tracks"]:
        for segment in track["segments"]:
            for ref in segment["extra_material_refs"]:
                assert ref in speed_ids


def test_added_speed_material_has_capcut_shape(tmp_path):
    path = _text_draft(tmp_path, count=1)
    cd.finalize_draft(path)
    speed = cd.read_content(path)["materials"]["speeds"][0]
    assert sorted(speed) == ["curve_speed", "id", "mode", "speed", "type"]
    assert speed["type"] == "speed"
    assert speed["speed"] == 1.0
    assert speed["mode"] == 0
    assert speed["curve_speed"] is None


def test_unresolvable_ref_on_non_text_track_is_dropped(tmp_path):
    path = make_draft(tmp_path)
    content = cd.read_content(path)
    content["tracks"][0]["segments"][0]["extra_material_refs"] = ["없는소재id"]
    cd.write_content(path, content)

    result = cd.finalize_draft(path)
    assert result["refs_fixed"]["dropped_refs"] == 1
    assert cd.read_content(path)["tracks"][0]["segments"][0]["extra_material_refs"] == []


def test_inspect_reports_dangling_refs(tmp_path):
    path = _text_draft(tmp_path, count=2)
    report = cd.inspect_draft(path)
    assert any("extra_material_refs" in p for p in report["problems"])
    assert any("캡컷이 드래프트를 여는 도중 종료" in p for p in report["problems"])


def test_valid_refs_are_left_alone(tmp_path):
    path = make_draft(tmp_path)
    content = cd.read_content(path)
    content["materials"]["speeds"] = [
        {"curve_speed": None, "id": "speed1", "mode": 0, "speed": 1.0, "type": "speed"}
    ]
    content["tracks"][0]["segments"][0]["extra_material_refs"] = ["speed1"]
    cd.write_content(path, content)

    result = cd.finalize_draft(path)
    assert result["refs_fixed"] == {"added_speeds": 0, "dropped_refs": 0, "broken_material_ids": []}
    assert cd.read_content(path)["tracks"][0]["segments"][0]["extra_material_refs"] == ["speed1"]
