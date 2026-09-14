"""대본 정렬, 용어 사전, 자막 생성, SRT 내보내기.

요청서 3.9 — 자막 시간이 겹치면 pyCapCut이 거부합니다.
    음성 인식은 겹치는 구간을 심심찮게 내놓습니다.
    → **자막을 만들 때와 SRT로 내보낼 때 두 번** 정리합니다.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .cut_edit import CutMap
from .line_break import split_subtitle
from .logging_util import get_logger

log = get_logger(__name__)

US = 1_000_000
MIN_SUB_US = 350_000  # 자막 하나의 최소 길이 0.35초 (요청서 3.9)


@dataclass
class Subtitle:
    index: int
    start_us: int
    end_us: int
    text: str
    source: str = "stt"       # stt | script | mixed
    confidence: float = 1.0
    edited: bool = False

    @property
    def duration_us(self) -> int:
        return self.end_us - self.start_us

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "start_us": self.start_us,
            "end_us": self.end_us,
            "duration_us": self.duration_us,
            "text": self.text,
            "source": self.source,
            "confidence": round(self.confidence, 3),
            "edited": self.edited,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Subtitle":
        return cls(
            index=int(raw.get("index", 0)),
            start_us=int(raw["start_us"]),
            end_us=int(raw["end_us"]),
            text=str(raw.get("text", "")),
            source=str(raw.get("source", "stt")),
            confidence=float(raw.get("confidence", 1.0)),
            edited=bool(raw.get("edited", False)),
        )


# ── 용어 사전 ────────────────────────────────────────────────────────────────
def active_terms(glossary: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    terms: List[str] = []
    for entries in glossary.values():
        for entry in entries:
            if entry.get("enabled", True) and entry.get("term"):
                terms.append(str(entry["term"]))
    return sorted(set(terms), key=len, reverse=True)


_KO_HINTS = {
    "비엘": "B/L", "비엘씨": "B/L", "에이치비엘": "HBL", "엠비엘": "MBL",
    "포워딩": "Forwarding", "인코텀즈": "Incoterms", "에프씨엘": "FCL",
    "엘씨엘": "LCL", "씨비엠": "CBM", "이티디": "ETD", "이티에이": "ETA",
    "데머리지": "Demurrage", "디텐션": "Detention", "컨사이니": "Consignee",
    "쉬퍼": "Shipper", "부킹": "Booking", "매니페스트": "Manifest",
    "카고와이즈": "CargoWise", "프롬프트": "Prompt", "토큰": "Token",
    "컨텍스트": "Context", "에이전트": "Agent", "에이피아이": "API",
    "엘엘엠": "LLM", "엠씨피": "MCP", "워크플로우": "Workflow", "워크플로": "Workflow",
    "오토메이션": "Automation",
}


def apply_glossary(text: str, terms: Sequence[str]) -> str:
    """물류/AI 영어 표현을 원문 표기로 되돌립니다.

    1) 한글 음차 표기를 영어로 치환 (STT가 '카고와이즈'로 받아쓴 경우)
    2) 대소문자가 틀린 영어 표기를 사전 표기로 교정
    """
    if not text:
        return text
    result = text
    term_set = {t.lower(): t for t in terms}

    for ko, en in _KO_HINTS.items():
        if en.lower() in term_set and ko in result:
            result = result.replace(ko, term_set[en.lower()])

    def _fix(match: re.Match) -> str:
        word = match.group(0)
        canonical = term_set.get(word.lower())
        return canonical if canonical else word

    if term_set:
        pattern = re.compile(
            r"\b(" + "|".join(re.escape(t) for t in sorted(term_set.values(), key=len, reverse=True)) + r")\b",
            re.IGNORECASE,
        )
        result = pattern.sub(_fix, result)
    return result


# ── 대본 읽기 ────────────────────────────────────────────────────────────────
_SRT_BLOCK = re.compile(r"\d+\s*\n\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}\s*\n")


def read_script(path: str) -> List[str]:
    """대본 파일(txt/srt/md)에서 문장 목록을 뽑습니다."""
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    suffix = Path(path).suffix.lower()

    if suffix == ".srt":
        raw = _SRT_BLOCK.sub("\n", raw)
    if suffix == ".md":
        raw = re.sub(r"^#{1,6}\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"[*_`>]", "", raw)
        raw = re.sub(r"^\s*[-+]\s+", "", raw, flags=re.MULTILINE)

    raw = re.sub(r"\r\n?", "\n", raw)
    sentences: List[str] = []
    for block in raw.split("\n"):
        block = block.strip()
        if not block:
            continue
        for piece in re.split(r"(?<=[.!?…])\s+", block):
            piece = piece.strip()
            if piece:
                sentences.append(piece)
    return sentences


def _norm_for_match(text: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", (text or "").lower())


def align_script(
    stt_segments: Sequence[Dict[str, Any]],
    script_sentences: Sequence[str],
    *,
    min_score: float = 62.0,
) -> List[Tuple[int, Optional[str], float]]:
    """STT 세그먼트와 대본 문장을 순서대로 정렬합니다.

    반환: [(stt_index, 대본 문장 or None, 유사도), ...]
    성립 구간은 **대본을 정본**으로, 타임코드는 STT에서 가져옵니다.
    즉흥 발화(매칭 실패)는 STT 그대로 둡니다.
    """
    try:
        from rapidfuzz import fuzz
    except Exception:
        log.warning("rapidfuzz가 없어 대본 정렬을 건너뜁니다. STT 결과를 그대로 씁니다.")
        return [(i, None, 0.0) for i in range(len(stt_segments))]

    normalized_script = [_norm_for_match(s) for s in script_sentences]
    out: List[Tuple[int, Optional[str], float]] = []
    cursor = 0
    # 순서를 지키되 앞뒤로 조금씩 흔들리는 것은 허용합니다.
    window = 12

    for idx, segment in enumerate(stt_segments):
        target = _norm_for_match(segment.get("text", ""))
        if not target:
            out.append((idx, None, 0.0))
            continue
        best_score = 0.0
        best_pos = -1
        upper = min(len(script_sentences), cursor + window)
        for pos in range(cursor, upper):
            if not normalized_script[pos]:
                continue
            score = fuzz.ratio(target, normalized_script[pos])
            if score > best_score:
                best_score = score
                best_pos = pos
        if best_pos >= 0 and best_score >= min_score:
            out.append((idx, script_sentences[best_pos], best_score))
            cursor = best_pos + 1
        else:
            out.append((idx, None, best_score))
    return out


# ── 자막 생성 ────────────────────────────────────────────────────────────────
def build_subtitles(
    stt_segments: Sequence[Dict[str, Any]],
    *,
    cut_map: CutMap,
    glossary_terms: Sequence[str],
    script_sentences: Optional[Sequence[str]] = None,
    max_chars: int = 36,
    min_duration_us: int = MIN_SUB_US,
) -> List[Subtitle]:
    """STT 결과(소스 타임라인 기준)를 편집본 타임라인의 한 줄 자막으로 바꿉니다."""
    alignment: Dict[int, Tuple[Optional[str], float]] = {}
    if script_sentences:
        for idx, sentence, score in align_script(stt_segments, script_sentences):
            alignment[idx] = (sentence, score)

    raw: List[Subtitle] = []
    for idx, segment in enumerate(stt_segments):
        words = segment.get("words") or []
        matched_text, match_score = alignment.get(idx, (None, 0.0))
        text = matched_text if matched_text else segment.get("text", "")
        text = apply_glossary(str(text).strip(), glossary_terms)
        if not text:
            continue

        source = "script" if matched_text else "stt"
        confidence = (
            min(1.0, match_score / 100.0)
            if matched_text
            else float(segment.get("confidence", 1.0) or 1.0)
        )

        pieces = split_subtitle(text, max_chars)
        if not pieces:
            continue

        seg_start = int(segment["start_us"])
        seg_end = int(segment["end_us"])
        spans = _distribute(pieces, seg_start, seg_end, words, use_words=not matched_text)

        for piece, (start_us, end_us) in zip(pieces, spans):
            raw.append(
                Subtitle(
                    index=0,
                    start_us=start_us,
                    end_us=end_us,
                    text=piece.text,
                    source=source,
                    confidence=confidence,
                )
            )

    # 1차 정리: 원본(소스) 타임라인에서의 겹침 해소
    raw = sanitize(raw, min_duration_us=min_duration_us)

    # 컷 맵 적용 → 편집본 타임라인
    mapped: List[Subtitle] = []
    for sub in raw:
        start = cut_map.orig_to_edit(sub.start_us)
        end = cut_map.orig_to_edit(sub.end_us)
        if start is None:
            start = cut_map.orig_to_edit_clamped(sub.start_us)
        if end is None:
            end = cut_map.orig_to_edit_clamped(sub.end_us)
        if end <= start:
            continue
        mapped.append(
            Subtitle(0, start, end, sub.text, sub.source, sub.confidence, sub.edited)
        )

    # 2차 정리: 매핑 후 다시 겹칠 수 있으므로 한 번 더
    mapped = sanitize(mapped, min_duration_us=min_duration_us)
    for pos, sub in enumerate(mapped, start=1):
        sub.index = pos
    return mapped


def _distribute(
    pieces, seg_start: int, seg_end: int, words: Sequence[Dict[str, Any]], *, use_words: bool
) -> List[Tuple[int, int]]:
    """조각별 시간 배분. 단어 타임스탬프가 있으면 그것을 우선 씁니다."""
    total_chars = sum(max(1, p.length) for p in pieces)
    duration = max(1, seg_end - seg_start)

    if use_words and words:
        cursor = 0
        spans: List[Tuple[int, int]] = []
        word_list = list(words)
        for pos, piece in enumerate(pieces):
            take = max(1, round(len(word_list) * piece.length / total_chars))
            chunk = word_list[cursor : cursor + take]
            if pos == len(pieces) - 1:
                chunk = word_list[cursor:]
            if chunk:
                start = int(chunk[0].get("start_us", seg_start))
                end = int(chunk[-1].get("end_us", seg_end))
                spans.append((start, max(start + 1, end)))
            else:
                fallback = seg_start + round(duration * cursor / max(1, len(word_list)))
                spans.append((fallback, fallback + 1))
            cursor += len(chunk)
        if len(spans) == len(pieces):
            return spans

    spans = []
    cursor_chars = 0
    for piece in pieces:
        start = seg_start + round(duration * cursor_chars / total_chars)
        cursor_chars += max(1, piece.length)
        end = seg_start + round(duration * cursor_chars / total_chars)
        spans.append((start, max(start + 1, end)))
    return spans


def sanitize(subs: List[Subtitle], *, min_duration_us: int = MIN_SUB_US) -> List[Subtitle]:
    """겹침 해소 + 최소 길이 보장.

    - 앞 자막 끝을 당겨서 해결하고, 그래도 안 되면 뒤 자막을 밉니다.
    - 최소 길이(0.35초)를 못 채우면 다음 자막 시작 직전까지 늘립니다.
    """
    ordered = sorted((s for s in subs if s.text.strip()), key=lambda s: (s.start_us, s.end_us))
    out: List[Subtitle] = []

    for sub in ordered:
        sub = Subtitle(0, sub.start_us, sub.end_us, sub.text, sub.source, sub.confidence, sub.edited)
        if out:
            prev = out[-1]
            if sub.start_us < prev.end_us:
                # 1) 앞 자막 끝을 당겨 본다
                if sub.start_us - prev.start_us >= min_duration_us:
                    prev.end_us = sub.start_us
                else:
                    # 2) 앞을 못 줄이면 뒤를 민다
                    shift = prev.end_us - sub.start_us
                    sub.start_us += shift
                    sub.end_us += shift
        if sub.end_us <= sub.start_us:
            sub.end_us = sub.start_us + min_duration_us
        out.append(sub)

    # 최소 길이 확보 (뒤 자막 시작을 넘지 않는 선에서)
    for pos, sub in enumerate(out):
        if sub.duration_us >= min_duration_us:
            continue
        limit = out[pos + 1].start_us if pos + 1 < len(out) else sub.start_us + min_duration_us
        sub.end_us = max(sub.end_us, min(sub.start_us + min_duration_us, limit))
        if sub.end_us <= sub.start_us:
            sub.end_us = sub.start_us + 1

    return [s for s in out if s.end_us > s.start_us and s.text.strip()]


def assert_no_overlap(subs: Sequence[Subtitle]) -> List[str]:
    """겹침이 남아 있는지 검사합니다 (드래프트 주입 전 최종 확인)."""
    problems: List[str] = []
    for prev, cur in zip(subs, subs[1:]):
        if cur.start_us < prev.end_us:
            problems.append(
                f"{prev.index}번과 {cur.index}번 자막이 겹칩니다 "
                f"({prev.end_us / US:.3f}초 > {cur.start_us / US:.3f}초)"
            )
    return problems


# ── SRT ──────────────────────────────────────────────────────────────────────
def _srt_time(us: int) -> str:
    ms = max(0, us) // 1000
    return f"{ms // 3600000:02d}:{(ms % 3600000) // 60000:02d}:{(ms % 60000) // 1000:02d},{ms % 1000:03d}"


def to_srt(subs: Sequence[Subtitle], *, min_duration_us: int = MIN_SUB_US) -> str:
    """SRT 문자열로 내보냅니다. 내보낼 때도 **다시 한 번** 정리합니다 (3.9)."""
    cleaned = sanitize(list(subs), min_duration_us=min_duration_us)
    lines: List[str] = []
    for pos, sub in enumerate(cleaned, start=1):
        lines.append(str(pos))
        lines.append(f"{_srt_time(sub.start_us)} --> {_srt_time(sub.end_us)}")
        lines.append(sub.text)
        lines.append("")
    return "\n".join(lines)
