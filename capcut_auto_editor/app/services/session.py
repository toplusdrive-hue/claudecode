"""세션 상태와 단계 잠금.

영상 한 편 작업 = 한 세션. 상태를 JSON으로 저장해 브라우저를 껐다 켜도
이어서 작업할 수 있습니다.
"""

from __future__ import annotations

import json
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import SESSION_DIR, WORK_DIR, load_settings
from .logging_util import get_logger

log = get_logger(__name__)

STEPS = [
    {"id": "stage0", "icon": "📁", "title": "0차 프로젝트 준비", "requires": []},
    {"id": "calibration", "icon": "🎨", "title": "자막 스타일 캘리브레이션", "requires": []},
    {"id": "stage1", "icon": "✂️", "title": "1차 컷 편집", "requires": ["stage0"]},
    {"id": "stage2", "icon": "💬", "title": "2차 자막 생성", "requires": ["stage1", "calibration"]},
    {"id": "stage3", "icon": "🔀", "title": "3차 트랜지션·효과음", "requires": ["stage1"]},
    {"id": "stage4", "icon": "📤", "title": "4차 내보내기·문구", "requires": ["stage2"]},
    {"id": "stage5", "icon": "📱", "title": "5차 세로용 영상", "requires": ["stage2"]},
]

STEP_TITLES = {s["id"]: s["title"] for s in STEPS}

_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_state(title: str = "") -> Dict[str, Any]:
    settings = load_settings()
    session_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    return {
        "id": session_id,
        "title": title or f"새 세션 {datetime.now():%m월 %d일 %H:%M}",
        "created": _now(),
        "updated": _now(),
        "completed": {},          # step_id -> 완료 시각
        "sources": [],            # 0차: 선택한 영상 목록 (ffprobe 결과 포함)
        "warnings": [],
        "draft_name": "",
        "draft_path": "",
        "backups": [],
        "audio": {},              # {"wav": .., "total_us": ..}
        "silence": [],            # [{"start_us":..,"end_us":..}, ...]
        "transcript": {},         # {"segments": [...], "words": [...]}
        "cut_candidates": [],
        "cut_settings": {
            "threshold_db": settings["silence_threshold_db"],
            "min_duration": settings["silence_min_duration"],
            "tail_pad": settings["tail_pad"],
            "head_pad": settings["head_pad"],
        },
        "cut_map": None,
        "cut_signature": None,
        "script_path": "",
        "subtitles": [],
        "subtitle_settings": {"max_chars": settings["subtitle_max_chars"]},
        "transitions": [],
        "sfx": [],
        "publishing": {},
        "vertical": {},
    }


def session_path(session_id: str) -> Path:
    return SESSION_DIR / f"{session_id}.json"


def save(state: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        state["updated"] = _now()
        path = session_path(state["id"])
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        tmp.replace(path)
    return state


def load(session_id: str) -> Dict[str, Any]:
    path = session_path(session_id)
    if not path.exists():
        raise FileNotFoundError(f"세션 '{session_id}' 을(를) 찾을 수 없습니다.")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def create(title: str = "") -> Dict[str, Any]:
    state = new_state(title)
    save(state)
    log.info("세션 생성: %s (%s)", state["id"], state["title"])
    return state


def delete(session_id: str) -> None:
    session_path(session_id).unlink(missing_ok=True)
    work = WORK_DIR / session_id
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    log.info("세션 삭제: %s", session_id)


def list_sessions() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in SESSION_DIR.glob("*.json"):
        try:
            with path.open("r", encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        completed = state.get("completed", {}) or {}
        done = [s["id"] for s in STEPS if s["id"] in completed]
        out.append(
            {
                "id": state.get("id", path.stem),
                "title": state.get("title", path.stem),
                "created": state.get("created", ""),
                "updated": state.get("updated", ""),
                "source_count": len(state.get("sources", []) or []),
                "draft_name": state.get("draft_name", ""),
                "completed_steps": done,
                "progress_label": STEP_TITLES.get(done[-1], "시작 전") if done else "시작 전",
            }
        )
    out.sort(key=lambda s: s.get("updated", ""), reverse=True)
    return out


def mark_done(state: Dict[str, Any], step_id: str) -> Dict[str, Any]:
    state.setdefault("completed", {})[step_id] = _now()
    return save(state)


def clear_done(state: Dict[str, Any], *step_ids: str) -> Dict[str, Any]:
    completed = state.setdefault("completed", {})
    for step_id in step_ids:
        completed.pop(step_id, None)
    return save(state)


def step_status(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """각 단계의 완료/잠김 상태와 **잠긴 이유**를 계산합니다."""
    completed = dict(state.get("completed", {}) or {})
    # 자막 스타일 캘리브레이션은 세션이 아니라 앱 전체에 하나만 저장됩니다.
    from ..config import STYLE_PROFILE_PATH

    if STYLE_PROFILE_PATH.exists():
        completed.setdefault("calibration", "저장됨")
    else:
        completed.pop("calibration", None)
    rows: List[Dict[str, Any]] = []
    for step in STEPS:
        missing = [STEP_TITLES[r] for r in step["requires"] if r not in completed]
        rows.append(
            {
                "id": step["id"],
                "icon": step["icon"],
                "title": step["title"],
                "done": step["id"] in completed,
                "done_at": completed.get(step["id"], ""),
                "locked": bool(missing),
                "lock_reason": (
                    f"{' · '.join(missing)} 을(를) 먼저 끝내야 합니다." if missing else ""
                ),
            }
        )
    return rows


def work_dir(session_id: str) -> Path:
    path = WORK_DIR / session_id
    path.mkdir(parents=True, exist_ok=True)
    return path
