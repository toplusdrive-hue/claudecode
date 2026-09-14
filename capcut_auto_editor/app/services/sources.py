"""소스 타임라인 — 여러 영상을 순서대로 이어붙인 하나의 가상 타임라인.

무음 감지·전사·컷 편집·자막은 전부 이 타임라인 위에서 이뤄집니다.
드래프트를 만들 때만 (몇 번째 영상, 그 안에서 몇 초)로 되돌립니다.

**구간이 영상 경계를 가로지르면 반드시 쪼갭니다** (요청서 4절).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple


@dataclass
class SourceClip:
    index: int
    path: str
    name: str
    duration_us: int
    width: int
    height: int
    fps: float
    offset_us: int  # 소스 타임라인상 시작 위치

    @property
    def end_us(self) -> int:
        return self.offset_us + self.duration_us

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "path": self.path,
            "name": self.name,
            "duration_us": self.duration_us,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "offset_us": self.offset_us,
        }


@dataclass
class LocalSpan:
    """특정 원본 영상 안의 구간."""
    source_index: int
    path: str
    start_us: int
    end_us: int

    @property
    def duration_us(self) -> int:
        return self.end_us - self.start_us

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_index": self.source_index,
            "path": self.path,
            "start_us": self.start_us,
            "end_us": self.end_us,
            "duration_us": self.duration_us,
        }


class SourceTimeline:
    def __init__(self, clips: List[SourceClip]):
        self.clips = clips

    @classmethod
    def from_media(cls, media: List[Dict[str, Any]]) -> "SourceTimeline":
        clips: List[SourceClip] = []
        offset = 0
        for idx, item in enumerate(media):
            duration = int(item["duration_us"])
            clips.append(
                SourceClip(
                    index=idx,
                    path=item["path"],
                    name=item.get("name") or item["path"],
                    duration_us=duration,
                    width=int(item.get("width", 0)),
                    height=int(item.get("height", 0)),
                    fps=float(item.get("fps", 0.0)),
                    offset_us=offset,
                )
            )
            offset += duration
        return cls(clips)

    @property
    def total_us(self) -> int:
        return self.clips[-1].end_us if self.clips else 0

    @property
    def canvas(self) -> Tuple[int, int, int]:
        """캔버스는 가장 큰 해상도 기준, fps는 최빈 반올림값 (요청서 0차)."""
        if not self.clips:
            return 1920, 1080, 30
        width = max(c.width for c in self.clips) or 1920
        height = max(c.height for c in self.clips) or 1080
        rates = [round(c.fps) for c in self.clips if c.fps > 0]
        fps = max(set(rates), key=rates.count) if rates else 30
        return width, height, int(fps)

    def mixed_warnings(self) -> List[str]:
        warnings: List[str] = []
        resolutions = {(c.width, c.height) for c in self.clips}
        if len(resolutions) > 1:
            listed = ", ".join(f"{w}x{h}" for w, h in sorted(resolutions))
            warnings.append(
                f"해상도가 섞여 있습니다 ({listed}). 캔버스는 가장 큰 해상도로 잡히고, "
                "작은 영상은 여백이 생기거나 늘어나 보일 수 있습니다."
            )
        rates = {round(c.fps, 2) for c in self.clips if c.fps > 0}
        if len(rates) > 1:
            listed = ", ".join(f"{r:g}fps" for r in sorted(rates))
            warnings.append(f"프레임레이트가 섞여 있습니다 ({listed}). 캡컷에서 재생이 매끄럽지 않을 수 있습니다.")
        for clip in self.clips:
            if clip.duration_us <= 0:
                warnings.append(f"'{clip.name}' 의 길이를 읽지 못했습니다. 파일이 손상되었는지 확인해 주세요.")
        return warnings

    def locate(self, global_us: int) -> Tuple[int, int]:
        """소스 타임라인 시각 → (영상 인덱스, 그 영상 안에서의 시각)."""
        if not self.clips:
            return 0, 0
        for clip in self.clips:
            if global_us < clip.end_us:
                return clip.index, max(0, global_us - clip.offset_us)
        last = self.clips[-1]
        return last.index, last.duration_us

    def split(self, start_us: int, end_us: int) -> List[LocalSpan]:
        """소스 타임라인 구간을 영상 경계에서 쪼갭니다."""
        spans: List[LocalSpan] = []
        start_us = max(0, start_us)
        end_us = min(self.total_us, end_us)
        if end_us <= start_us:
            return spans
        for clip in self.clips:
            overlap_start = max(start_us, clip.offset_us)
            overlap_end = min(end_us, clip.end_us)
            if overlap_end <= overlap_start:
                continue
            spans.append(
                LocalSpan(
                    source_index=clip.index,
                    path=clip.path,
                    start_us=overlap_start - clip.offset_us,
                    end_us=overlap_end - clip.offset_us,
                )
            )
        return spans

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clips": [c.to_dict() for c in self.clips],
            "total_us": self.total_us,
        }
