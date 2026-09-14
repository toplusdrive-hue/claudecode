"""컷 후보 · 타임코드 매핑 검증 (요청서 3.17 / 3.18 / 5차)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.audio_analysis import SilenceSpan
from app.services.cut_edit import (
    CutCandidate,
    CutMap,
    filler_candidates,
    keep_long_silences,
    merge_candidates,
    repeat_candidates,
    silence_candidates,
)

US = 1_000_000


def test_silence_padding_is_asymmetric():
    """3.17 — 말 끝난 뒤 여유(0.35)와 다음 말 시작 전 여유(0.15)는 서로 다릅니다."""
    spans = [SilenceSpan(10 * US, 14 * US)]
    out = silence_candidates(spans, tail_pad_sec=0.35, head_pad_sec=0.15, total_us=100 * US)
    assert len(out) == 1
    assert out[0].start_us == int(10.35 * US)
    assert out[0].end_us == int(13.85 * US)


def test_longer_tail_pad_keeps_more_audio():
    """꼬리를 0.15 → 0.35로 늘리면 남는 길이가 늘고 컷 개수가 줄어야 합니다."""
    spans = [SilenceSpan(i * US, i * US + 800_000) for i in range(1, 40)]
    short = silence_candidates(spans, tail_pad_sec=0.15, head_pad_sec=0.15, total_us=60 * US)
    long = silence_candidates(spans, tail_pad_sec=0.35, head_pad_sec=0.15, total_us=60 * US)
    assert sum(c.duration_us for c in long) < sum(c.duration_us for c in short)
    assert len(long) <= len(short)


def test_tail_pad_swallowing_silence_drops_candidate():
    spans = [SilenceSpan(5 * US, 5 * US + 400_000)]  # 0.4초 무음
    out = silence_candidates(spans, tail_pad_sec=0.35, head_pad_sec=0.15, total_us=60 * US)
    assert out == []  # 여유를 빼면 남는 게 없으므로 자르지 않습니다


def test_trailing_silence_keeps_full_range():
    total = 30 * US
    spans = [SilenceSpan(28 * US, total)]
    out = silence_candidates(spans, tail_pad_sec=0.35, head_pad_sec=0.15, total_us=total)
    assert out[0].end_us == total


def _words(pairs):
    return [
        {"text": text, "start_us": int(start * US), "end_us": int(end * US)}
        for text, start, end in pairs
    ]


def test_filler_detection_multiword():
    words = _words([("그러니까", 1.0, 1.3), ("이제", 1.3, 1.6), ("운임이", 2.0, 2.4)])
    out = filler_candidates(words, ["어", "그러니까 이제"])
    assert len(out) == 1
    assert "그러니까 이제" in out[0].label


def test_repeat_detection_keeps_last_utterance():
    words = _words([("운임이", 1.0, 1.4), ("운임이", 1.45, 1.85), ("올랐습니다", 1.9, 2.5)])
    out = repeat_candidates(words)
    assert len(out) == 1
    # 앞쪽 반복을 자르고 뒤를 남깁니다
    assert out[0].start_us < int(1.45 * US)


def test_repeat_ignores_distant_repetition():
    words = _words([("운임이", 1.0, 1.4), ("운임이", 5.0, 5.4)])
    assert repeat_candidates(words) == []


def test_merge_prefers_silence_over_filler():
    silence = CutCandidate("s0", "silence", 1000, 5000)
    filler = CutCandidate("f0", "filler", 3000, 7000)
    merged = merge_candidates([[filler], [silence]])
    assert len(merged) == 1
    assert merged[0].kind == "silence"
    assert (merged[0].start_us, merged[0].end_us) == (1000, 7000)


def test_cut_map_roundtrip_and_signature():
    total = 100 * US
    cuts = [CutCandidate("a", "silence", 10 * US, 20 * US), CutCandidate("b", "filler", 50 * US, 55 * US)]
    cut_map = CutMap.build(total, cuts)

    assert cut_map.kept_duration_us == 85 * US
    assert cut_map.signature() == {"kept_duration_us": 85 * US, "span_count": 3}

    # 원본 5초 → 편집본 5초 (앞에 잘린 게 없음)
    assert cut_map.orig_to_edit(5 * US) == 5 * US
    # 원본 30초 → 앞에서 10초가 잘렸으므로 20초
    assert cut_map.orig_to_edit(30 * US) == 20 * US
    # 잘려나간 구간은 None
    assert cut_map.orig_to_edit(15 * US) is None
    assert cut_map.orig_to_edit_clamped(15 * US) == 10 * US
    # 역변환
    assert cut_map.edit_to_orig(20 * US) == 30 * US

    restored = CutMap.from_dict(cut_map.to_dict())
    assert restored.signature() == cut_map.signature()


def test_edit_range_splits_into_multiple_original_pieces():
    """5차 함정 — 편집본에서 이어져 보여도 원본에서는 여러 조각입니다."""
    total = 100 * US
    cuts = [CutCandidate("a", "silence", 30 * US, 40 * US)]
    cut_map = CutMap.build(total, cuts)
    # 편집본 25~45초는 편집본 기준 20초 분량입니다.
    #   편집본 25~30초 → 원본 25~30초 (앞에 잘린 게 없음)
    #   편집본 30~45초 → 원본 40~55초 (30~40초가 잘려 나갔으므로 10초 밀림)
    pieces = cut_map.edit_range_to_orig_ranges(25 * US, 45 * US)
    assert pieces == [(25 * US, 30 * US), (40 * US, 55 * US)]
    assert sum(end - start for start, end in pieces) == 20 * US


def test_keep_long_silences_deselects():
    cands = [
        CutCandidate("a", "silence", 0, 4 * US),
        CutCandidate("b", "silence", 10 * US, 11 * US),
        CutCandidate("c", "filler", 20 * US, 25 * US),
    ]
    changed = keep_long_silences(cands, threshold_sec=3.0)
    assert changed == 1
    assert cands[0].selected is False
    assert cands[1].selected is True
    assert cands[2].selected is True  # 필러는 건드리지 않습니다


def test_removed_ratio_warning_threshold():
    total = 100 * US
    cuts = [CutCandidate("a", "silence", 0, 45 * US)]
    cut_map = CutMap.build(total, cuts)
    assert cut_map.removed_ratio > 0.40
