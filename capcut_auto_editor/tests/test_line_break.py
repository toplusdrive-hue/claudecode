"""자막 줄바꿈 규칙 검증 (요청서 2차)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.line_break import has_mid_word_break, is_punct_only, split_subtitle

MAX = 36


def test_always_single_line_and_within_limit():
    text = (
        "안녕하세요, 오늘은 포워딩 실무에서 자주 쓰는 B/L 발행 절차를 정리해 보겠습니다. "
        "생각보다 어렵지 않으니 끝까지 보시면 바로 따라 하실 수 있습니다."
    )
    pieces = split_subtitle(text, MAX)
    assert pieces
    for piece in pieces:
        assert "\n" not in piece.text
        assert len(piece.text) <= MAX


def test_punctuation_stays_with_previous_chunk():
    pieces = split_subtitle("첫 문장입니다. 두 번째 문장입니다.", MAX)
    assert pieces[0].text.endswith(".")
    assert not pieces[1].text.startswith(".")


def test_punctuation_only_fragment_is_dropped():
    assert split_subtitle(".", MAX) == []
    assert split_subtitle("…", MAX) == []
    assert is_punct_only("...")


def test_no_mid_word_break_korean_and_english():
    korean = "데머리지와 디텐션은 완전히 다른 개념인데 실무에서는 자주 뒤섞여 쓰이고 있습니다"
    english = "CargoWise automation workflow configuration requires a properly scoped API token"
    for text in (korean, english):
        pieces = split_subtitle(text, MAX)
        assert not has_mid_word_break(text, pieces)
        for piece in pieces:
            assert not piece.text.startswith(" ") and not piece.text.endswith(" ")
        # 하이픈을 끼워 넣지 않습니다
        assert all("-" not in p.text or "-" in text for p in pieces)


def test_even_division_avoids_short_tail():
    """앞에서부터 꽉 채우면 [34자] + [5자] 같은 짧은 꼬리가 생깁니다."""
    text = "이번 분기 해상 운임은 지난 분기 대비 크게 올랐고 앞으로도 더 오를 전망입니다"
    pieces = split_subtitle(text, MAX)
    assert len(pieces) >= 2
    shortest = min(len(p.text) for p in pieces)
    longest = max(len(p.text) for p in pieces)
    # 고르게 나뉘면 가장 짧은 조각이 가장 긴 조각의 절반 이상입니다
    assert shortest >= longest * 0.5, [p.text for p in pieces]


def test_unbreakable_single_token_is_left_alone():
    text = "A" * 60
    pieces = split_subtitle(text, MAX)
    assert len(pieces) == 1
    assert pieces[0].text == text


def test_decimal_point_is_not_a_sentence_end():
    pieces = split_subtitle("운임이 3.5배 올랐습니다", MAX)
    assert len(pieces) == 1
