"""마케팅 문구 프롬프트 조립.

외부 LLM API는 쓰지 않습니다. **완성된 프롬프트 텍스트**를 만들어
클립보드 복사와 txt 저장으로 제공합니다 (요청서 1절).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..config import DATA_DIR
from .script_align import Subtitle

US = 1_000_000

CHANNEL_PROFILE_PATH = DATA_DIR / "channel_profile.json"

DEFAULT_CHANNEL = {
    "name": "",
    "topic": "수출입 물류(포워딩) 실무와 업무 자동화",
    "audience": "포워딩·수출입 업무를 하는 실무자. 대부분 30~50대 직장인이고, 엑셀과 메일로 대부분의 업무를 처리합니다.",
    "tone": "현장 용어를 그대로 쓰되 과장하지 않습니다. 단정적인 홍보 문구보다 '이렇게 하면 몇 분이 줄어든다'처럼 구체적인 이득을 말합니다.",
}

# 사람이 잘 쓰지 않는 표기를 막는 지시 (요청서 4차)
STYLE_RULES = """[표기 규칙]
- em dash(—)나 en dash(–)를 쓰지 마십시오. 필요하면 쉼표나 마침표로 끊으십시오.
- 별표(*)로 강조하지 마십시오. 굵게 표시가 필요하면 그냥 문장으로 풀어 쓰십시오.
- 물결(~), 화살표(→), 이모지로 문장을 장식하지 마십시오.
- "~할 수 있습니다"의 반복을 피하고, 문장 길이를 고르게 섞으십시오.
- 영어 약어는 처음 나올 때만 괄호로 풀어 쓰고 그 뒤로는 약어만 쓰십시오."""


def load_channel_profile() -> Dict[str, Any]:
    import json

    if CHANNEL_PROFILE_PATH.exists():
        try:
            data = json.loads(CHANNEL_PROFILE_PATH.read_text(encoding="utf-8"))
            merged = dict(DEFAULT_CHANNEL)
            merged.update(data)
            return merged
        except (OSError, ValueError):
            pass
    return dict(DEFAULT_CHANNEL)


def save_channel_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    import json

    merged = load_channel_profile()
    merged.update(profile)
    CHANNEL_PROFILE_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def _tc(us: int) -> str:
    seconds = max(0, us) // US
    if seconds >= 3600:
        return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def subtitle_transcript(subs: Sequence[Subtitle], *, with_time: bool = True) -> str:
    lines: List[str] = []
    for sub in subs:
        lines.append(f"[{_tc(sub.start_us)}] {sub.text}" if with_time else sub.text)
    return "\n".join(lines)


def build_marketing_prompt(
    *,
    answers: Dict[str, str],
    subtitles: Sequence[Subtitle],
    channel: Optional[Dict[str, Any]] = None,
    video_duration_us: int = 0,
) -> str:
    """유튜브 제목 2안 / 설명글 / 해시태그를 뽑는 프롬프트를 조립합니다."""
    channel = channel or load_channel_profile()
    takeaway = (answers.get("takeaway") or "").strip()
    situation = (answers.get("situation") or "").strip()
    tool_name = (answers.get("tool_name") or "").strip()

    duration_label = _tc(video_duration_us) if video_duration_us else "(길이 미상)"

    parts: List[str] = []
    parts.append("아래 영상의 유튜브 업로드용 문구를 작성해 주십시오.")
    parts.append("")
    parts.append("[채널]")
    if channel.get("name"):
        parts.append(f"- 채널명: {channel['name']}")
    parts.append(f"- 주제: {channel.get('topic', '')}")
    parts.append(f"- 타겟 시청자: {channel.get('audience', '')}")
    parts.append(f"- 톤: {channel.get('tone', '')}")
    parts.append("")
    parts.append("[이 영상]")
    parts.append(f"- 길이: {duration_label}")
    parts.append(f"- 시청자가 가져가는 가장 큰 하나: {takeaway or '(입력하지 않음)'}")
    parts.append(f"- 필요한 사람의 상황: {situation or '(입력하지 않음)'}")
    parts.append(f"- 다루는 도구·기능의 정확한 이름: {tool_name or '(입력하지 않음)'}")
    parts.append("")
    parts.append("[요청]")
    parts.append("1. 유튜브 제목 2안. 각 20자 내외. 낚시성 표현 없이, 무엇을 알려주는 영상인지 드러나게.")
    parts.append("2. 설명글. 첫 두 줄에 영상 요지를 적고, 그 아래에 타임스탬프 목차를 넣어 주십시오.")
    parts.append("   타임스탬프는 아래 자막 전문의 시각을 근거로 실제 주제가 바뀌는 지점에만 찍으십시오.")
    parts.append("   목차는 5~8개 사이로 맞추고, 각 줄은 `0:00 항목명` 형식으로 적으십시오.")
    parts.append("3. 해시태그 5개. 한국어 위주로 하되 검색될 만한 영어 약어는 그대로 쓰십시오.")
    parts.append("")
    parts.append(STYLE_RULES)
    parts.append("")
    parts.append("[자막 전문 — 타임스탬프 포함]")
    parts.append(subtitle_transcript(subtitles))
    return "\n".join(parts)


def build_vertical_text_prompt(
    *,
    platform: str,                   # shorts | reels
    subtitles: Sequence[Subtitle],
    answers: Dict[str, str],
    channel: Optional[Dict[str, Any]] = None,
    clip_duration_us: int = 0,
) -> str:
    """세로 영상의 상단/하단 문구 프롬프트를 조립합니다."""
    channel = channel or load_channel_profile()
    is_reels = platform == "reels"
    label = "인스타그램 릴스" if is_reels else "유튜브 쇼츠"

    parts: List[str] = []
    parts.append(f"아래 세로 영상({label})에 얹을 상단 문구와 하단 문구를 작성해 주십시오.")
    parts.append("")
    parts.append("[채널]")
    parts.append(f"- 주제: {channel.get('topic', '')}")
    parts.append(f"- 타겟 시청자: {channel.get('audience', '')}")
    parts.append(f"- 톤: {channel.get('tone', '')}")
    parts.append("")
    parts.append("[이 클립]")
    parts.append(f"- 길이: 약 {max(1, clip_duration_us // US)}초")
    parts.append(f"- 핵심 메시지: {(answers.get('takeaway') or '(입력하지 않음)').strip()}")
    parts.append("")
    parts.append("[요청]")
    parts.append("1. 상단 문구 3안. **각 18자 이내.** 화면 위쪽에 크게 얹을 한 줄입니다.")
    parts.append("   첫 1초 안에 '내 얘기다'라고 느끼게 하는 상황 제시로 쓰십시오.")
    parts.append("2. 하단 문구 3안. **각 24자 이내.** 상단 문구를 받아 결론이나 이득을 말합니다.")
    if is_reels:
        parts.append("3. 하단 문구 3안 중 **최소 2안에는 프로필 링크로 유도하는 문장을 넣으십시오.**")
        parts.append("   예: '전체 영상은 프로필 링크에' 같은 형태. 릴스는 댓글 링크가 잘 눌리지 않습니다.")
    else:
        parts.append("3. 마지막에 구독 유도 한 줄을 덧붙이되, 문구 안에 녹여 쓰십시오.")
    parts.append("")
    parts.append(STYLE_RULES)
    parts.append("- 상단/하단 문구는 글자 수 제한이 좁으므로, 조사까지 세어 반드시 맞추십시오.")
    parts.append("")
    parts.append("[이 클립의 자막]")
    parts.append(subtitle_transcript(subtitles, with_time=False))
    return "\n".join(parts)


def save_prompt(session_id: str, name: str, text: str) -> str:
    from ..config import WORK_DIR

    folder = WORK_DIR / session_id / "prompts"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}_{datetime.now():%Y%m%d_%H%M%S}.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)
