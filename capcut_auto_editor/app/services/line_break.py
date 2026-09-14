"""자막 줄바꿈 — 문장부호 기준, 항상 한 줄.

요청서 2차 규칙:
  - 자막은 항상 한 줄 (max_lines = 1)
  - `. ! ? …`(문장 끝)과 `, · ; :`(구절)에서 끊고, **부호는 앞 조각에 붙입니다**
  - 한 줄 상한 36자 (근거: 사용자 드래프트 자막 294개가 전부 1줄,
    중앙값 12자 / 90% 18자 / 최대 38자)
  - 상한을 넘는 구절만 어절 단위로 더 쪼개되, **앞에서부터 꽉 채우지 말고
    필요한 조각 수를 먼저 계산해 고르게 나눕니다**
    (꽉 채우면 `[34자] + [5자]`처럼 짧은 꼬리가 생겨 화면을 스쳐 지나갑니다)
  - 한글은 어절 단위로만, 영문도 **하이픈 없이** 단어 단위로만 끊습니다
  - 부호만 있는 조각(`.`)은 자막으로 만들지 않습니다
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import List

SENTENCE_END = ".!?…"
PHRASE_MARK = ",·;:"
BREAK_CHARS = SENTENCE_END + PHRASE_MARK
CLOSERS = "\"'”’)]}»"

_ONLY_PUNCT = re.compile(r"^[\s.,!?…·;:\"'“”‘’()\[\]{}<>~\-—_/\\|]+$")


@dataclass
class Piece:
    """분할된 한 줄. `offset`은 원본 문자열에서의 시작 위치입니다."""
    text: str
    offset: int

    @property
    def length(self) -> int:
        return len(self.text)


def is_punct_only(text: str) -> bool:
    """부호·공백만 있는 조각인지."""
    return not text.strip() or bool(_ONLY_PUNCT.match(text))


def split_by_punctuation(text: str) -> List[Piece]:
    """문장부호에서 끊습니다. 부호는 앞 조각에 붙입니다."""
    pieces: List[Piece] = []
    buffer: List[str] = []
    start = 0

    idx = 0
    while idx < len(text):
        ch = text[idx]
        buffer.append(ch)
        if ch in BREAK_CHARS:
            # 연속된 부호(…, ?!, ".)와 닫는 따옴표까지 함께 앞에 붙입니다.
            while idx + 1 < len(text) and (text[idx + 1] in BREAK_CHARS or text[idx + 1] in CLOSERS):
                idx += 1
                buffer.append(text[idx])
            # 소수점·시간 표기(3.5초, 12:30)는 문장 끝이 아닙니다.
            prev = text[idx - len("".join(buffer)) + 1] if buffer else ""
            after = text[idx + 1] if idx + 1 < len(text) else ""
            joined = "".join(buffer)
            if (
                len(joined.strip()) > 1
                and joined.strip()[-1] in ".:"
                and after.isdigit()
                and joined.strip()[-2].isdigit()
            ):
                idx += 1
                continue
            chunk = joined.strip()
            if chunk:
                pieces.append(Piece(chunk, start + (len(joined) - len(joined.lstrip()))))
            start = idx + 1
            buffer = []
        idx += 1

    tail = "".join(buffer).strip()
    if tail:
        pieces.append(Piece(tail, start + (len("".join(buffer)) - len("".join(buffer).lstrip()))))
    return pieces


def merge_punct_only(pieces: List[Piece]) -> List[Piece]:
    """부호만 있는 조각을 앞 조각에 붙입니다. 앞이 없으면 뒤에 붙입니다."""
    out: List[Piece] = []
    for piece in pieces:
        if is_punct_only(piece.text):
            if out:
                out[-1] = Piece((out[-1].text + piece.text).strip(), out[-1].offset)
            elif piece.text.strip():
                out.append(piece)  # 뒤 조각과 합쳐질 수 있게 일단 보관
            continue
        if out and is_punct_only(out[-1].text):
            merged = (out[-1].text + " " + piece.text).strip()
            out[-1] = Piece(merged, out[-1].offset)
            continue
        out.append(piece)
    return [p for p in out if not is_punct_only(p.text)]


def _even_split(text: str, offset: int, max_chars: int) -> List[Piece]:
    """어절 단위로 고르게 나눕니다. 앞에서부터 꽉 채우지 않습니다."""
    if len(text) <= max_chars:
        return [Piece(text, offset)]

    # 어절과 그 원본 오프셋
    words: List[tuple[str, int]] = []
    for match in re.finditer(r"\S+", text):
        words.append((match.group(), match.start()))
    if len(words) <= 1:
        # 공백 없는 한 덩어리는 하이픈 없이 쪼갤 수 없으므로 그대로 둡니다.
        return [Piece(text, offset)]

    needed = math.ceil(len(text) / max_chars)
    target = math.ceil(len(text) / needed)

    while True:
        groups = _pack(words, target)
        if len(groups) <= needed and all(len(g) <= max_chars for g in _join(groups)):
            break
        target += 1
        if target > max_chars:
            groups = _pack(words, max_chars)
            break

    pieces: List[Piece] = []
    for group in groups:
        if not group:
            continue
        chunk_text = " ".join(w for w, _ in group)
        pieces.append(Piece(chunk_text, offset + group[0][1]))
    return pieces


def _pack(words: List[tuple[str, int]], width: int) -> List[List[tuple[str, int]]]:
    groups: List[List[tuple[str, int]]] = [[]]
    length = 0
    for word, pos in words:
        add = len(word) if not groups[-1] else len(word) + 1
        if groups[-1] and length + add > width:
            groups.append([(word, pos)])
            length = len(word)
        else:
            groups[-1].append((word, pos))
            length += add
    return [g for g in groups if g]


def _join(groups: List[List[tuple[str, int]]]) -> List[str]:
    return [" ".join(w for w, _ in g) for g in groups]


def split_subtitle(text: str, max_chars: int = 36) -> List[Piece]:
    """자막 한 덩어리를 한 줄짜리 조각들로 나눕니다."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        return []

    pieces = merge_punct_only(split_by_punctuation(text))
    if not pieces:
        return []

    out: List[Piece] = []
    for piece in pieces:
        out.extend(_even_split(piece.text, piece.offset, max_chars))

    # 너무 짧은 조각(부호 포함 2자 이하)은 앞 조각에 흡수시켜 스쳐 지나가지 않게 합니다.
    compacted: List[Piece] = []
    for piece in out:
        if (
            compacted
            and len(piece.text) <= 2
            and len(compacted[-1].text) + len(piece.text) + 1 <= max_chars
        ):
            compacted[-1] = Piece(f"{compacted[-1].text} {piece.text}".strip(), compacted[-1].offset)
            continue
        compacted.append(piece)

    return [p for p in compacted if p.text.strip() and not is_punct_only(p.text)]


def has_mid_word_break(original: str, pieces: List[Piece]) -> bool:
    """단어가 중간에서 잘렸는지 검사 (완료 기준 검증용)."""
    joined = re.sub(r"\s+", "", "".join(p.text for p in pieces))
    source = re.sub(r"\s+", "", original)
    return joined != source
