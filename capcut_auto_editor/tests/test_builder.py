"""pyCapCut 실물로 드래프트를 만들어 후처리까지 검증합니다.

캡컷 자체는 없어도 됩니다. 확인하는 것은 **파일에 무엇이 쓰였는가** 입니다.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import builder, capcut_draft as cd
from app.services.cut_edit import CutCandidate, CutMap
from app.services.script_align import Subtitle
from app.services.sources import SourceTimeline

US = 1_000_000


@pytest.fixture
def draft_root(tmp_path, monkeypatch):
    root = tmp_path / "com.lveditor.draft"
    root.mkdir()
    monkeypatch.setattr(cd, "resolve_draft_root", lambda: root)
    monkeypatch.setattr(cd, "BACKUP_DIR", tmp_path / "backups")
    (tmp_path / "backups").mkdir()
    return root


@pytest.fixture
def timeline(clips):
    import pymediainfo

    media = []
    for path in clips:
        info = pymediainfo.MediaInfo.parse(str(path))
        track = info.video_tracks[0]
        media.append({
            "path": str(path), "name": path.name,
            "duration_us": int(track.duration * 1e3),
            "width": track.width, "height": track.height, "fps": 30.0,
        })
    return SourceTimeline.from_media(media)


@pytest.fixture
def profile(tmp_path):
    from tests.test_capcut_draft import make_capcut_style_draft

    path = make_capcut_style_draft(tmp_path / "ref")
    result = cd.calibrate_text_style(path)
    return result.profile


# ── 3.8 소재 길이 ────────────────────────────────────────────────────────
def test_material_duration_clamping(clips):
    """pyCapCut은 자기가 읽은 길이를 1µs라도 넘으면 거부합니다."""
    material = builder.load_material(str(clips[0]))
    assert material.duration > 0

    # 1µs 초과 → None (건너뜀)
    assert builder.clamp_span(material, 0, material.duration + 1)[1] == material.duration
    # ffprobe 반올림이 pyCapCut보다 큰 경우를 흉내 냅니다
    over = builder.clamp_span(material, material.duration - 500, material.duration + 1000)
    assert over is None or over[1] <= material.duration


def test_segment_creation_fails_without_clamping(clips):
    """방어가 없으면 실제로 pyCapCut이 거부하는지 확인합니다."""
    import pycapcut

    material = builder.load_material(str(clips[0]))
    with pytest.raises(ValueError, match="超出了素材时长"):
        pycapcut.VideoSegment(
            material,
            pycapcut.Timerange(0, material.duration + 1),
            source_timerange=pycapcut.Timerange(0, material.duration + 1),
        )


# ── 1차 드래프트 ─────────────────────────────────────────────────────────
def test_build_cut_draft_end_to_end(draft_root, timeline, clips):
    cuts = [CutCandidate("a", "silence", 2 * US, 3 * US)]
    cut_map = CutMap.build(timeline.total_us, cuts)

    report = builder.build_cut_draft(
        draft_name="테스트편집", timeline=timeline, cut_map=cut_map, session_id="sess1"
    )
    path = Path(report.draft_path)
    assert path.is_dir()
    assert report.warnings == []

    content = cd.read_content(path)
    # 3.10 — ratio 교정
    assert content["canvas_config"]["ratio"] == "original"
    # 3.3 — 레이어 교정
    for index, track in enumerate(content["tracks"]):
        for segment in track["segments"]:
            assert segment["track_render_index"] == index
    # 3.18 — 컷 서명 기록. 단, draft_content.json 안이 아니라 별도 파일에 남깁니다.
    assert cd.MARKER_KEY not in content, (
        "캡컷이 파싱하는 파일에 우리 키를 넣으면 안 됩니다. 드래프트를 열 때 문제가 될 수 있습니다."
    )
    assert (path / cd.MARKER_FILE).is_file()
    marker = cd.read_marker(path)
    assert marker["cut_signature"] == cut_map.signature()
    # 3.11 — 메타 경로
    meta = json.loads((path / "draft_meta_info.json").read_text(encoding="utf-8"))
    assert meta["draft_fold_path"] == str(path)
    # 3.12 — 레지스트리 등록
    registry = json.loads((draft_root / "root_meta_info.json").read_text(encoding="utf-8"))
    assert any(e["draft_name"] == "테스트편집" for e in registry["all_draft_store"])
    # 영상 경계를 넘는 구간이 쪼개져 여러 세그먼트가 됩니다
    assert report.details["placed_segments"] >= 3


def test_build_draft_splits_at_source_boundary(draft_root, timeline):
    cut_map = CutMap.identity(timeline.total_us)
    report = builder.build_cut_draft(
        draft_name="경계테스트", timeline=timeline, cut_map=cut_map, session_id="s"
    )
    content = cd.read_content(Path(report.draft_path))
    segments = content["tracks"][0]["segments"]
    assert len(segments) == 2  # 영상 2개 → 2조각
    material_ids = {s["material_id"] for s in segments}
    assert len(material_ids) == 2
    # 편집본 타임라인이 빈틈없이 이어져야 합니다
    assert segments[0]["target_timerange"]["duration"] == segments[1]["target_timerange"]["start"]


# ── 2차 자막 주입 ────────────────────────────────────────────────────────
def _make_draft_with_subs(draft_root, timeline, profile, subs, name="자막테스트"):
    cut_map = CutMap.identity(timeline.total_us)
    builder.build_cut_draft(draft_name=name, timeline=timeline, cut_map=cut_map, session_id="s")
    return builder.apply_subtitles(
        draft_path=draft_root / name, subtitles=subs, profile=profile,
        cut_map=cut_map, session_id="s",
    ), cut_map


def test_apply_subtitles_injects_calibrated_style(draft_root, timeline, profile, monkeypatch):
    monkeypatch.setattr(builder, "resolve_font_path", lambda p: ("C:/Fonts/Pretendard-Bold.otf", []))
    subs = [
        Subtitle(1, 0, 2 * US, "첫 번째 자막입니다"),
        Subtitle(2, 2 * US + 100_000, 4 * US, "두 번째 자막입니다"),
    ]
    report, _ = _make_draft_with_subs(draft_root, timeline, profile, subs)
    content = cd.read_content(Path(report.draft_path))

    texts = content["materials"]["texts"]
    assert len(texts) == 2
    for material in texts:
        # 3.4 — check_flag 비트
        assert material["check_flag"] == 31
        assert material["background_style"] >= 1
        assert material["background_color"] == "#ffffff"
        # 3.6 — 폰트 절대 경로
        assert material["font_path"] == "C:/Fonts/Pretendard-Bold.otf"
        parsed = json.loads(material["content"])
        assert parsed["styles"][0]["font"]["path"] == "C:/Fonts/Pretendard-Bold.otf"
        assert parsed["styles"][0]["range"] == [0, len(parsed["text"])]
        # 3.5 — 원본에만 있던 필드가 살아 있어야 합니다
        assert material["template_scene"] == "default"

    # 3.3 — 텍스트 트랙이 영상 트랙보다 위 레이어
    text_track = next(t for t in content["tracks"] if t["type"] == "text")
    text_index = content["tracks"].index(text_track)
    assert all(s["track_render_index"] == text_index for s in text_track["segments"])
    assert text_index > 0

    # 세그먼트에도 원본 필드가 채워졌습니다
    assert text_track["segments"][0]["enable_video_mask"] is True

    # 파일에서 다시 센 개수를 보고합니다
    assert report.details["confirmed"] == 2
    assert report.details["requested"] == 2


def test_apply_subtitles_twice_does_not_duplicate(draft_root, timeline, profile, monkeypatch):
    monkeypatch.setattr(builder, "resolve_font_path", lambda p: ("C:/F/Pretendard-Bold.otf", []))
    subs = [Subtitle(1, 0, 2 * US, "자막")]
    report, cut_map = _make_draft_with_subs(draft_root, timeline, profile, subs)

    again = builder.apply_subtitles(
        draft_path=Path(report.draft_path), subtitles=subs, profile=profile,
        cut_map=cut_map, session_id="s",
    )
    content = cd.read_content(Path(report.draft_path))
    text_tracks = [t for t in content["tracks"] if t["name"] == cd.AUTO_SUBTITLE_TRACK]
    assert len(text_tracks) == 1
    assert len(content["materials"]["texts"]) == 1
    assert again.details["confirmed"] == 1


def test_apply_subtitles_blocks_on_cut_signature_mismatch(draft_root, timeline, profile):
    """⚠️ 3.18 — 드래프트와 컷 설정이 어긋나면 자막이 통째로 밀립니다."""
    cut_map = CutMap.identity(timeline.total_us)
    builder.build_cut_draft(draft_name="서명테스트", timeline=timeline, cut_map=cut_map, session_id="s")

    # 드래프트를 만든 뒤 컷 선택을 바꿉니다
    changed = CutMap.build(timeline.total_us, [CutCandidate("x", "silence", US, 4 * US)])
    with pytest.raises(builder.CutSignatureMismatch) as exc:
        builder.apply_subtitles(
            draft_path=draft_root / "서명테스트",
            subtitles=[Subtitle(1, 0, US, "자막")],
            profile=profile, cut_map=changed, session_id="s",
        )
    assert "자막이 밀립니다" in str(exc.value)


def test_apply_subtitles_rejects_overlapping_subtitles(draft_root, timeline, profile):
    cut_map = CutMap.identity(timeline.total_us)
    builder.build_cut_draft(draft_name="겹침", timeline=timeline, cut_map=cut_map, session_id="s")
    overlapping = [Subtitle(1, 0, 2 * US, "가"), Subtitle(2, US, 3 * US, "나")]
    with pytest.raises(builder.BuildError, match="겹칩니다"):
        builder.apply_subtitles(
            draft_path=draft_root / "겹침", subtitles=overlapping, profile=profile,
            cut_map=cut_map, session_id="s",
        )


# ── 3차 ──────────────────────────────────────────────────────────────────
def test_list_image_clips_cross_checks_type_and_extension(draft_root, timeline):
    cut_map = CutMap.identity(timeline.total_us)
    report = builder.build_cut_draft(draft_name="이미지", timeline=timeline, cut_map=cut_map, session_id="s")
    path = Path(report.draft_path)

    content = cd.read_content(path)
    content["materials"]["videos"].append(
        {"id": "photo1", "type": "photo", "path": "C:/assets/cut.png", "material_name": "cut.png"}
    )
    content["tracks"][0]["segments"].append({
        "id": "segP", "material_id": "photo1", "track_render_index": 0, "render_index": 0,
        "target_timerange": {"start": 9 * US, "duration": US}, "extra_material_refs": [],
    })
    cd.write_content(path, content)

    clips = builder.list_image_clips(path)
    assert len(clips) == 1
    assert clips[0]["cross_checked"] is True
    assert clips[0]["has_next"] is False  # 마지막 세그먼트


def test_apply_transitions_attaches_to_previous_segment(draft_root, timeline):
    cut_map = CutMap.identity(timeline.total_us)
    report = builder.build_cut_draft(draft_name="트랜지션", timeline=timeline, cut_map=cut_map, session_id="s")
    path = Path(report.draft_path)

    lookup = cd.transition_lookup()
    enum_name = lookup["6725771847444468236"]

    out = builder.apply_transitions(
        draft_path=path,
        requests=[{"track_index": 0, "segment_index": 0, "enum_name": enum_name, "duration_us": 500_000}],
        session_id="s",
    )
    assert out.details["applied"] == 1
    assert out.details["confirmed_in_file"] == 1

    content = cd.read_content(path)
    transition = content["materials"]["transitions"][0]
    assert transition["effect_id"] == "6725771847444468236"
    # 앞쪽 세그먼트에 붙습니다
    assert transition["id"] in content["tracks"][0]["segments"][0]["extra_material_refs"]
    assert transition["id"] not in content["tracks"][0]["segments"][1]["extra_material_refs"]


def test_apply_transitions_refuses_on_last_segment(draft_root, timeline):
    cut_map = CutMap.identity(timeline.total_us)
    report = builder.build_cut_draft(draft_name="마지막", timeline=timeline, cut_map=cut_map, session_id="s")
    lookup = cd.transition_lookup()
    enum_name = lookup["6725771847444468236"]
    out = builder.apply_transitions(
        draft_path=Path(report.draft_path),
        requests=[{"track_index": 0, "segment_index": 1, "enum_name": enum_name}],
        session_id="s",
    )
    assert out.details["applied"] == 0
    assert any("마지막 세그먼트" in w for w in out.warnings)


# ── 5차 세로 ─────────────────────────────────────────────────────────────
def test_build_vertical_draft_reverses_cut_map(draft_root, timeline, profile, monkeypatch):
    monkeypatch.setattr(builder, "resolve_font_path", lambda p: ("C:/F/Pretendard-Bold.otf", []))
    # 2~3초를 잘라내면 편집본 1.5~3.5초는 원본에서 두 조각이 됩니다
    cut_map = CutMap.build(timeline.total_us, [CutCandidate("a", "silence", 2 * US, 3 * US)])
    subs = [Subtitle(1, int(1.6 * US), int(3.0 * US), "세로 자막")]

    report = builder.build_vertical_draft(
        draft_name="세로테스트", timeline=timeline, cut_map=cut_map,
        edit_start_us=int(1.5 * US), edit_end_us=int(3.5 * US),
        subtitles=subs, profile=profile, background_image=None,
        overlay_scale=1.8, overlay_y=-0.078125, subtitle_y=None, session_id="s",
    )
    assert report.details["orig_range_count"] == 2
    # 경고 문구의 조각 수는 영상 경계 분할까지 반영한 실제 개수여야 합니다
    assert any(f"{report.details['clip_pieces']}조각" in w for w in report.warnings)
    assert any("컷 편집으로 잘려나간 구간" in w for w in report.warnings)

    content = cd.read_content(Path(report.draft_path))
    canvas = content["canvas_config"]
    assert (canvas["width"], canvas["height"], canvas["ratio"]) == (1080, 1920, "9:16")
    # dumps()가 떨어뜨리는 background 가 되살아나 있어야 합니다
    assert "background" in canvas

    overlay = next(t for t in content["tracks"] if t.get("name") == "오버레이")
    for segment in overlay["segments"]:
        assert segment["clip"]["scale"] == {"x": 1.8, "y": 1.8}
        assert segment["clip"]["transform"]["y"] == pytest.approx(-0.078125)

    text_track = next(t for t in content["tracks"] if t["type"] == "text")
    assert len(text_track["segments"]) == 1
    # 세로 캔버스에서는 정규화 값을 그대로 유지합니다 (픽셀 환산이 아님)
    assert text_track["segments"][0]["clip"]["transform"]["y"] == pytest.approx(-0.7394)
    assert text_track["segments"][0]["target_timerange"]["start"] == pytest.approx(0.1 * US, abs=2000)


def test_vertical_rescale_y_from_horizontal_profile():
    horizontal = {"canvas": {"width": 1920, "height": 1080}, "position": {"y": -0.7296}}
    assert builder._rescale_y(horizontal, 1920) == pytest.approx(builder.VERTICAL_SUBTITLE_Y)

    vertical = {"canvas": {"width": 1080, "height": 1920}, "position": {"y": -0.7394}}
    assert builder._rescale_y(vertical, 1920) == pytest.approx(-0.7394)


def test_korean_particle_selection():
    """받침에 따라 와/과를 고릅니다 ('구간와'가 아니라 '구간과')."""
    assert builder._join_ko(["컷 편집으로 잘려나간 구간", "영상 파일 경계"]) == "컷 편집으로 잘려나간 구간과 영상 파일 경계"
    assert builder._join_ko(["영상 파일 경계", "구간"]) == "영상 파일 경계와 구간"
    assert builder._join_ko(["하나"]) == "하나"
    assert builder._join_ko([]) == ""
