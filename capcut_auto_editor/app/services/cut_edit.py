"""컷 후보 생성과 원본↔편집본 타임코드 매핑.

요청서 3.17 — 무음 구간의 앞뒤 여유는 **반드시 따로** 둡니다.
    무음 시작 = 말이 끝난 직후 → `tail_pad` (기본 0.35초, 넉넉히)
    무음 끝   = 다음 말 시작 직전 → `head_pad` (기본 0.15초)
    하나의 패딩으로 처리하면 말끝의 자음·여운이 잘려 들립니다.

요청서 3.18 — 컷 서명(남는 길이 + 구간 수)을 드래프트에 기록해 두고,
    자막을 넣기 전에 비교합니다. 다르면 자막이 통째로 밀립니다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .audio_analysis import SilenceSpan

US = 1_000_000

CUT_TYPES = {
    "silence": "무음",
    "filler": "필러",
    "repeat": "반복",
}


@dataclass
class CutCandidate:
    id: str
    kind: str            # silence | filler | repeat
    start_us: int
    end_us: int
    selected: bool = True
    label: str = ""
    context_before: str = ""
    context_after: str = ""

    @property
    def duration_us(self) -> int:
        return self.end_us - self.start_us

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "kind_label": CUT_TYPES.get(self.kind, self.kind),
            "start_us": self.start_us,
            "end_us": self.end_us,
            "duration_us": self.duration_us,
            "selected": self.selected,
            "label": self.label,
            "context_before": self.context_before,
            "context_after": self.context_after,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "CutCandidate":
        return cls(
            id=str(raw["id"]),
            kind=str(raw["kind"]),
            start_us=int(raw["start_us"]),
            end_us=int(raw["end_us"]),
            selected=bool(raw.get("selected", True)),
            label=str(raw.get("label", "")),
            context_before=str(raw.get("context_before", "")),
            context_after=str(raw.get("context_after", "")),
        )


def format_tc(us: int) -> str:
    total_ms = max(0, us) // 1000
    ms = total_ms % 1000
    total_s = total_ms // 1000
    return f"{total_s // 3600:02d}:{(total_s % 3600) // 60:02d}:{total_s % 60:02d}.{ms:03d}"


# ── 후보 생성 ────────────────────────────────────────────────────────────────
def silence_candidates(
    spans: Sequence[SilenceSpan],
    *,
    tail_pad_sec: float,
    head_pad_sec: float,
    total_us: int,
    min_keep_us: int = 60_000,
) -> List[CutCandidate]:
    """무음 구간에서 컷 후보를 만듭니다 (3.17의 비대칭 패딩 적용)."""
    tail_pad = int(round(tail_pad_sec * US))
    head_pad = int(round(head_pad_sec * US))
    out: List[CutCandidate] = []
    for idx, span in enumerate(spans):
        start = span.start_us + tail_pad
        end = span.end_us - head_pad
        # 영상 맨 끝의 무음은 뒤쪽에 이어질 말이 없으므로 head_pad가 필요 없습니다.
        if span.end_us >= total_us - 1000:
            end = span.end_us
        if end - start < min_keep_us:
            continue
        out.append(
            CutCandidate(
                id=f"s{idx}",
                kind="silence",
                start_us=max(0, start),
                end_us=min(total_us, end),
                label=f"무음 {(end - start) / US:.2f}초",
            )
        )
    return out


_PUNCT = re.compile(r"[.,!?…·\"'“”‘’()\[\]~\-—]")


def _norm(text: str) -> str:
    return _PUNCT.sub("", text or "").strip().lower()


def filler_candidates(
    words: Sequence[Dict[str, Any]],
    fillers: Sequence[str],
    *,
    pad_us: int = 30_000,
) -> List[CutCandidate]:
    """필러워드 구간을 찾습니다.

    `words`: [{"text":..., "start_us":..., "end_us":...}, ...] (소스 타임라인 기준)
    여러 어절로 된 필러("그러니까 이제")도 이어 붙여 비교합니다.
    """
    normalized = [_norm(w.get("text", "")) for w in words]
    filler_list = sorted({_norm(f) for f in fillers if _norm(f)}, key=lambda s: -len(s.split()))
    max_span = max((len(f.split()) for f in filler_list), default=1)

    out: List[CutCandidate] = []
    used: set[int] = set()
    for start_idx in range(len(words)):
        if start_idx in used:
            continue
        for span_len in range(min(max_span, len(words) - start_idx), 0, -1):
            phrase = " ".join(normalized[start_idx : start_idx + span_len]).strip()
            if not phrase or phrase not in filler_list:
                continue
            indices = range(start_idx, start_idx + span_len)
            if any(i in used for i in indices):
                break
            used.update(indices)
            start_us = int(words[start_idx]["start_us"]) - pad_us
            end_us = int(words[start_idx + span_len - 1]["end_us"]) + pad_us
            out.append(
                CutCandidate(
                    id=f"f{start_idx}",
                    kind="filler",
                    start_us=max(0, start_us),
                    end_us=end_us,
                    label=f"필러 '{' '.join(w.get('text','') for w in words[start_idx:start_idx+span_len])}'",
                    context_before=" ".join(w.get("text", "") for w in words[max(0, start_idx - 5) : start_idx]),
                    context_after=" ".join(
                        w.get("text", "") for w in words[start_idx + span_len : start_idx + span_len + 5]
                    ),
                )
            )
            break
    out.sort(key=lambda c: c.start_us)
    return out


def repeat_candidates(
    words: Sequence[Dict[str, Any]],
    *,
    max_phrase_words: int = 4,
    similarity: int = 92,
    pad_us: int = 30_000,
) -> List[CutCandidate]:
    """말더듬(바로 뒤에 같은 말을 다시 하는 구간)을 찾습니다.

    앞쪽 반복을 잘라내고 **마지막 발화를 남깁니다.**
    """
    try:
        from rapidfuzz import fuzz

        def score(a: str, b: str) -> float:
            return fuzz.ratio(a, b)
    except Exception:  # rapidfuzz가 없으면 완전 일치만 봅니다
        def score(a: str, b: str) -> float:
            return 100.0 if a == b else 0.0

    normalized = [_norm(w.get("text", "")) for w in words]
    out: List[CutCandidate] = []
    consumed: set[int] = set()

    idx = 0
    while idx < len(words):
        if idx in consumed or not normalized[idx]:
            idx += 1
            continue
        matched = False
        for span_len in range(min(max_phrase_words, (len(words) - idx) // 2), 0, -1):
            left = " ".join(normalized[idx : idx + span_len])
            right = " ".join(normalized[idx + span_len : idx + 2 * span_len])
            if not left or not right:
                continue
            if score(left, right) < similarity:
                continue
            gap = int(words[idx + span_len]["start_us"]) - int(words[idx + span_len - 1]["end_us"])
            if gap > 1_200_000:  # 1.2초 넘게 벌어졌으면 말더듬이 아니라 의도된 반복
                continue
            start_us = int(words[idx]["start_us"]) - pad_us
            end_us = int(words[idx + span_len - 1]["end_us"]) + pad_us
            out.append(
                CutCandidate(
                    id=f"r{idx}",
                    kind="repeat",
                    start_us=max(0, start_us),
                    end_us=end_us,
                    label=f"반복 '{' '.join(w.get('text','') for w in words[idx:idx+span_len])}'",
                    context_before=" ".join(w.get("text", "") for w in words[max(0, idx - 5) : idx]),
                    context_after=" ".join(
                        w.get("text", "") for w in words[idx + span_len : idx + span_len + 6]
                    ),
                )
            )
            consumed.update(range(idx, idx + span_len))
            idx += span_len
            matched = True
            break
        if not matched:
            idx += 1
    return out


def merge_candidates(groups: Iterable[Sequence[CutCandidate]]) -> List[CutCandidate]:
    """유형별 후보를 합치되, 겹치는 후보는 우선순위(무음 > 필러 > 반복)로 하나만 남깁니다."""
    priority = {"silence": 0, "filler": 1, "repeat": 2}
    pool: List[CutCandidate] = []
    for group in groups:
        pool.extend(group)
    pool.sort(key=lambda c: (c.start_us, priority.get(c.kind, 9)))

    merged: List[CutCandidate] = []
    for cand in pool:
        if merged and cand.start_us < merged[-1].end_us:
            prev = merged[-1]
            # 겹치면 우선순위가 높은 쪽을 남기고 범위를 합칩니다.
            if priority.get(cand.kind, 9) < priority.get(prev.kind, 9):
                cand.start_us = min(prev.start_us, cand.start_us)
                cand.end_us = max(prev.end_us, cand.end_us)
                merged[-1] = cand
            else:
                prev.end_us = max(prev.end_us, cand.end_us)
            continue
        merged.append(cand)
    return merged


# ── 컷 맵 (원본 ↔ 편집본) ────────────────────────────────────────────────────
@dataclass
class KeptSpan:
    orig_start_us: int
    orig_end_us: int
    edit_start_us: int

    @property
    def duration_us(self) -> int:
        return self.orig_end_us - self.orig_start_us

    @property
    def edit_end_us(self) -> int:
        return self.edit_start_us + self.duration_us


class CutMap:
    """선택된 컷을 제외하고 남는 구간 목록. 양방향 타임코드 변환을 제공합니다."""

    def __init__(self, kept: List[KeptSpan], total_orig_us: int):
        self.kept = kept
        self.total_orig_us = total_orig_us

    @classmethod
    def build(
        cls,
        total_us: int,
        cuts: Sequence[CutCandidate],
        *,
        min_span_us: int = 40_000,
    ) -> "CutMap":
        selected = sorted(
            [(max(0, c.start_us), min(total_us, c.end_us)) for c in cuts if c.selected and c.end_us > c.start_us]
        )
        # 겹치는 컷 병합
        normalized: List[Tuple[int, int]] = []
        for start, end in selected:
            if normalized and start <= normalized[-1][1]:
                normalized[-1] = (normalized[-1][0], max(normalized[-1][1], end))
            else:
                normalized.append((start, end))

        kept: List[KeptSpan] = []
        cursor = 0
        edit_cursor = 0
        for start, end in normalized:
            if start - cursor >= min_span_us:
                span = KeptSpan(cursor, start, edit_cursor)
                kept.append(span)
                edit_cursor += span.duration_us
            cursor = max(cursor, end)
        if total_us - cursor >= min_span_us:
            span = KeptSpan(cursor, total_us, edit_cursor)
            kept.append(span)
        return cls(kept, total_us)

    @classmethod
    def identity(cls, total_us: int) -> "CutMap":
        return cls([KeptSpan(0, total_us, 0)], total_us)

    @property
    def kept_duration_us(self) -> int:
        return sum(span.duration_us for span in self.kept)

    @property
    def removed_ratio(self) -> float:
        if self.total_orig_us <= 0:
            return 0.0
        return 1.0 - (self.kept_duration_us / self.total_orig_us)

    def signature(self) -> Dict[str, int]:
        """컷 서명 — 드래프트와 자막이 같은 컷 설정인지 대조하는 값 (요청서 3.18)."""
        return {"kept_duration_us": self.kept_duration_us, "span_count": len(self.kept)}

    def orig_to_edit(self, us: int) -> Optional[int]:
        """원본 시각 → 편집본 시각. 잘려나간 구간이면 None."""
        for span in self.kept:
            if span.orig_start_us <= us < span.orig_end_us:
                return span.edit_start_us + (us - span.orig_start_us)
        return None

    def orig_to_edit_clamped(self, us: int) -> int:
        """잘려나간 구간이면 가장 가까운 남은 지점으로 당깁니다."""
        if not self.kept:
            return 0
        if us < self.kept[0].orig_start_us:
            return 0
        for span in self.kept:
            if span.orig_start_us <= us < span.orig_end_us:
                return span.edit_start_us + (us - span.orig_start_us)
            if us < span.orig_start_us:
                return span.edit_start_us
        return self.kept[-1].edit_end_us

    def edit_to_orig(self, us: int) -> int:
        if not self.kept:
            return us
        for span in self.kept:
            if span.edit_start_us <= us < span.edit_end_us:
                return span.orig_start_us + (us - span.edit_start_us)
        return self.kept[-1].orig_end_us

    def edit_range_to_orig_ranges(self, start_us: int, end_us: int) -> List[Tuple[int, int]]:
        """편집본 구간 → 원본에서의 여러 조각 (요청서 5차의 역변환).

        사용자가 고르는 구간은 컷 편집 **후** 시각인데 실제 소재는 **원본**입니다.
        그 사이 잘려나간 컷이 있으면 원본에서는 여러 조각이 됩니다.
        """
        out: List[Tuple[int, int]] = []
        for span in self.kept:
            overlap_start = max(start_us, span.edit_start_us)
            overlap_end = min(end_us, span.edit_end_us)
            if overlap_end <= overlap_start:
                continue
            out.append(
                (
                    span.orig_start_us + (overlap_start - span.edit_start_us),
                    span.orig_start_us + (overlap_end - span.edit_start_us),
                )
            )
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_orig_us": self.total_orig_us,
            "kept": [
                {
                    "orig_start_us": s.orig_start_us,
                    "orig_end_us": s.orig_end_us,
                    "edit_start_us": s.edit_start_us,
                }
                for s in self.kept
            ],
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "CutMap":
        return cls(
            [
                KeptSpan(int(s["orig_start_us"]), int(s["orig_end_us"]), int(s["edit_start_us"]))
                for s in raw.get("kept", [])
            ],
            int(raw.get("total_orig_us", 0)),
        )


def keep_long_silences(
    candidates: List[CutCandidate], *, threshold_sec: float = 3.0
) -> int:
    """긴 무음(기본 3초 이상)을 선택 해제합니다.

    화면만 보여주며 말을 안 하는 시연 구간이 통째로 날아가는 것을 막습니다.
    해제한 개수를 반환합니다.
    """
    threshold_us = int(threshold_sec * US)
    changed = 0
    for cand in candidates:
        if cand.kind == "silence" and cand.duration_us >= threshold_us and cand.selected:
            cand.selected = False
            changed += 1
    return changed
