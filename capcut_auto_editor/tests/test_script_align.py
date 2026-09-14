"""자막 생성·정리·SRT 검증 (요청서 3.9)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.cut_edit import CutCandidate, CutMap
from app.services.script_align import (
    Subtitle,
    apply_glossary,
    assert_no_overlap,
    build_subtitles,
    sanitize,
    to_srt,
)

US = 1_000_000


def test_sanitize_resolves_overlap_from_stt():
    """STT가 겹치는 구간을 내놓는 실제 사례 (앞 문장 463.87 / 다음 463.47)."""
    subs = [
        Subtitle(1, int(460.0 * US), int(463.87 * US), "앞 문장입니다"),
        Subtitle(2, int(463.47 * US), int(466.0 * US), "다음 문장입니다"),
    ]
    out = sanitize(subs)
    assert assert_no_overlap(out) == []
    assert out[0].end_us <= out[1].start_us


def test_sanitize_pushes_when_previous_cannot_shrink():
    subs = [
        Subtitle(1, 1_000_000, 1_400_000, "가"),
        Subtitle(2, 1_100_000, 1_900_000, "나"),
    ]
    out = sanitize(subs)
    assert assert_no_overlap(out) == []
    assert all(s.duration_us >= 350_000 for s in out)


def test_sanitize_enforces_minimum_duration():
    subs = [Subtitle(1, 0, 100_000, "짧다")]
    out = sanitize(subs)
    assert out[0].duration_us >= 350_000


def test_sanitize_drops_empty_text():
    subs = [Subtitle(1, 0, US, "  "), Subtitle(2, 2 * US, 3 * US, "내용")]
    out = sanitize(subs)
    assert len(out) == 1


def test_srt_is_sanitized_again_on_export():
    """3.9 — 자막을 만들 때와 SRT로 내보낼 때 두 번 정리합니다."""
    subs = [
        Subtitle(1, 0, 2 * US, "앞"),
        Subtitle(2, US, 3 * US, "뒤"),  # 일부러 겹쳐 둡니다
    ]
    text = to_srt(subs)
    assert "00:00:00,000 --> 00:00:01,000" in text
    assert text.count("-->") == 2


def test_glossary_restores_english_terms():
    terms = ["CargoWise", "B/L", "Demurrage", "MCP"]
    assert apply_glossary("카고와이즈에서 확인하세요", terms) == "CargoWise에서 확인하세요"
    assert apply_glossary("cargowise 설정", terms) == "CargoWise 설정"
    assert apply_glossary("데머리지가 붙습니다", terms) == "Demurrage가 붙습니다"


def test_glossary_leaves_unknown_words_alone():
    assert apply_glossary("일반적인 문장입니다", ["CargoWise"]) == "일반적인 문장입니다"


def test_build_subtitles_maps_through_cut_map():
    total = 100 * US
    cut_map = CutMap.build(total, [CutCandidate("a", "silence", 10 * US, 20 * US)])
    segments = [
        {
            "text": "무음 앞의 문장입니다.",
            "start_us": 5 * US,
            "end_us": 8 * US,
            "confidence": 0.9,
            "words": [],
        },
        {
            "text": "무음 뒤의 문장입니다.",
            "start_us": 30 * US,
            "end_us": 33 * US,
            "confidence": 0.9,
            "words": [],
        },
    ]
    subs = build_subtitles(segments, cut_map=cut_map, glossary_terms=[])
    assert len(subs) == 2
    assert subs[0].start_us == 5 * US
    # 앞에서 10초가 잘렸으므로 30초 → 20초
    assert subs[1].start_us == 20 * US
    assert assert_no_overlap(subs) == []
    assert [s.index for s in subs] == [1, 2]


def test_build_subtitles_splits_long_segment_into_single_lines():
    total = 60 * US
    cut_map = CutMap.identity(total)
    segments = [
        {
            "text": "이번 영상에서는 해상 운임 산정 방식을 처음부터 끝까지 정리하고, "
                    "실무에서 자주 틀리는 부분까지 함께 짚어 보겠습니다.",
            "start_us": 0,
            "end_us": 10 * US,
            "confidence": 0.95,
            "words": [],
        }
    ]
    subs = build_subtitles(segments, cut_map=cut_map, glossary_terms=[], max_chars=36)
    assert len(subs) > 1
    assert all(len(s.text) <= 36 for s in subs)
    assert assert_no_overlap(subs) == []
