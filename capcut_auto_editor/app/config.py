"""경로 탐지, 설정 로드, 사전 관리.

캡컷 드래프트 폴더 경로는 **소스에 하드코딩하지 않습니다** (요청서 2절).
런타임에 탐지하고, 탐지에 실패하면 사용자가 지정한 값을 씁니다.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── 프로젝트 경로 ────────────────────────────────────────────────────────────
APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
DATA_DIR = ROOT_DIR / "data"
BACKUP_DIR = ROOT_DIR / "backups"
LOG_DIR = ROOT_DIR / "logs"
ASSET_DIR = ROOT_DIR / "assets"
SFX_DIR = ASSET_DIR / "sfx"
BG_DIR = ASSET_DIR / "bg"
SESSION_DIR = DATA_DIR / "sessions"
WORK_DIR = DATA_DIR / "work"
STATIC_DIR = APP_DIR / "static"

for _d in (DATA_DIR, BACKUP_DIR, LOG_DIR, SFX_DIR, BG_DIR, SESSION_DIR, WORK_DIR):
    _d.mkdir(parents=True, exist_ok=True)

SETTINGS_PATH = DATA_DIR / "settings.json"
STYLE_PROFILE_PATH = DATA_DIR / "style_profile.json"
FILLER_PATH = DATA_DIR / "filler_words.json"
GLOSSARY_PATH = DATA_DIR / "glossary_en.json"

# ── 검증된 조합 (요청서 1절) ──────────────────────────────────────────────────
VERIFIED_CAPCUT_VERSION = "9.3.0.3969"
VERIFIED_PYCAPCUT_VERSION = "0.0.3"
VERIFIED_PYTHON = (3, 11)

# 캡컷 드래프트 폴더 이름. 클라우드/텍스트템플릿 폴더는 대상이 아닙니다 (요청서 3.1).
DRAFT_FOLDER_NAME = "com.lveditor.draft"
EXCLUDED_DRAFT_FOLDER_PREFIXES = ("com.lveditor.cloud.draft", "com.lveditor.textTemplate.draft")

# 캡컷 프로세스 이름 (요청서 3.13)
CAPCUT_PROCESS_NAMES = ("capcut", "jianyingpro", "capcut.exe", "jianyingpro.exe")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "draft_root": "",           # 비워 두면 자동 탐지
    "ffmpeg": "",
    "ffprobe": "",
    "last_browse_dir": "",
    "whisper_model": "medium",  # 요청서 3.19 — large-v3 아님
    "whisper_compute_type": "int8",
    "whisper_language": "ko",
    "silence_threshold_db": -35.0,
    "silence_min_duration": 0.6,
    "tail_pad": 0.35,           # 말 끝난 뒤 여유 (요청서 3.17)
    "head_pad": 0.15,           # 다음 말 시작 전 여유
    "subtitle_max_chars": 36,   # 요청서 2차 — 한 줄 상한
    "subtitle_min_duration": 0.35,
    "transition_duration": 0.5,
    "vertical_overlay_scale": 1.8,
    "vertical_overlay_y": -0.078125,
    "enable_auto_export": False,  # 요청서 4차 — 기본 OFF
}

DEFAULT_FILLERS: List[str] = [
    "어", "음", "그", "그니까", "저기", "뭐지", "이제", "아 그", "그러니까 이제",
    "뭐냐", "인제", "아니 그", "자 그럼", "에", "으", "그래서 이제",
]

DEFAULT_GLOSSARY: Dict[str, List[Dict[str, Any]]] = {
    "물류": [
        {"term": t, "enabled": True}
        for t in [
            "Forwarding", "B/L", "HBL", "MBL", "Incoterms", "FCL", "LCL", "CBM",
            "ETD", "ETA", "Demurrage", "Detention", "Consignee", "Shipper",
            "Booking", "Manifest", "CargoWise",
        ]
    ],
    "AI": [
        {"term": t, "enabled": True}
        for t in ["Prompt", "Token", "Context", "Agent", "API", "LLM", "MCP", "Workflow", "Automation"]
    ],
}


# ── 설정 입출력 ──────────────────────────────────────────────────────────────
def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return json.loads(json.dumps(fallback))
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return json.loads(json.dumps(fallback))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def load_settings() -> Dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    settings.update(_read_json(SETTINGS_PATH, {}))
    return settings


def save_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    merged = load_settings()
    merged.update(settings)
    _write_json(SETTINGS_PATH, merged)
    return merged


def load_fillers() -> List[str]:
    data = _read_json(FILLER_PATH, DEFAULT_FILLERS)
    if not FILLER_PATH.exists():
        _write_json(FILLER_PATH, DEFAULT_FILLERS)
    return [str(x) for x in data]


def save_fillers(words: List[str]) -> List[str]:
    cleaned: List[str] = []
    for word in words:
        word = str(word).strip()
        if word and word not in cleaned:
            cleaned.append(word)
    _write_json(FILLER_PATH, cleaned)
    return cleaned


def load_glossary() -> Dict[str, List[Dict[str, Any]]]:
    data = _read_json(GLOSSARY_PATH, DEFAULT_GLOSSARY)
    if not GLOSSARY_PATH.exists():
        _write_json(GLOSSARY_PATH, DEFAULT_GLOSSARY)
    return data


def save_glossary(glossary: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    _write_json(GLOSSARY_PATH, glossary)
    return glossary


def load_style_profile() -> Optional[Dict[str, Any]]:
    if not STYLE_PROFILE_PATH.exists():
        return None
    return _read_json(STYLE_PROFILE_PATH, None)


def save_style_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    _write_json(STYLE_PROFILE_PATH, profile)
    return profile


# ── 캡컷 드래프트 폴더 자동 탐지 ─────────────────────────────────────────────
def _local_appdata() -> Optional[Path]:
    for key in ("LOCALAPPDATA", "APPDATA"):
        raw = os.environ.get(key)
        if raw:
            path = Path(raw)
            if key == "APPDATA":
                path = path.parent / "Local"
            if path.exists():
                return path
    if sys.platform == "win32":
        home = Path.home() / "AppData" / "Local"
        return home if home.exists() else None
    return None


def detect_draft_roots() -> List[Dict[str, Any]]:
    """캡컷 드래프트 폴더 후보를 찾아 반환합니다.

    하드코딩된 단일 경로를 쓰지 않고, `%LOCALAPPDATA%` 아래에서
    `*/User Data/Projects/com.lveditor.draft` 패턴을 실제로 탐색합니다.
    """
    found: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def _add(path: Path, origin: str) -> None:
        key = str(path).lower()
        if key in seen or not path.is_dir():
            return
        seen.add(key)
        found.append(
            {
                "path": str(path),
                "origin": origin,
                "draft_count": _count_drafts(path),
                "has_registry": (path / "root_meta_info.json").exists(),
            }
        )

    configured = load_settings().get("draft_root") or ""
    if configured:
        _add(Path(configured), "설정에 지정된 경로")

    base = _local_appdata()
    if base is not None:
        for vendor in ("CapCut", "JianyingPro", "CapCut Drafts", "lveditor"):
            _add(base / vendor / "User Data" / "Projects" / DRAFT_FOLDER_NAME, f"{vendor} 기본 경로")
        # 벤더 폴더명이 다를 수 있으므로 1단계만 더 넓게 훑습니다.
        try:
            for child in base.iterdir():
                if not child.is_dir():
                    continue
                candidate = child / "User Data" / "Projects" / DRAFT_FOLDER_NAME
                _add(candidate, f"{child.name} 아래에서 탐지")
        except OSError:
            pass
    return found


def _count_drafts(root: Path) -> int:
    try:
        return sum(
            1
            for child in root.iterdir()
            if child.is_dir() and (child / "draft_content.json").exists()
        )
    except OSError:
        return 0


def resolve_draft_root() -> Optional[Path]:
    """실제로 사용할 드래프트 폴더 하나를 결정합니다. 없으면 None."""
    configured = load_settings().get("draft_root") or ""
    if configured and Path(configured).is_dir():
        return Path(configured)
    candidates = detect_draft_roots()
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c["has_registry"], c["draft_count"]), reverse=True)
    return Path(candidates[0]["path"])


# ── ffmpeg / ffprobe 탐지 ────────────────────────────────────────────────────
# ffmpeg을 흔히 두는 위치. 압축을 풀면 보통 한 겹이 더 생기므로
# (예: C:\ffmpeg\ffmpeg-9.0.1-essentials_build\bin) 아래에서 한 단계 더 훑습니다.
_FFMPEG_ROOT_HINTS = [
    r"C:\ffmpeg",
    r"C:\Program Files\ffmpeg",
    r"C:\Program Files (x86)\ffmpeg",
    r"C:\Tools\ffmpeg",
    r"D:\ffmpeg",
]


def expand_tool_dirs(base: Path, depth: int = 2) -> List[Path]:
    """실행 파일이 있을 만한 폴더 후보를 만듭니다.

    사용자가 `C:\ffmpeg` 를 지정하든, 압축을 푼 그대로
    `C:\ffmpeg\ffmpeg-9.0.1-essentials_build` 를 지정하든,
    `...\bin` 을 지정하든 모두 찾아야 합니다.
    """
    if not base.is_dir():
        return []
    seen: Dict[str, Path] = {}

    def _add(path: Path) -> None:
        if path.is_dir():
            seen.setdefault(str(path).lower(), path)

    _add(base)
    _add(base / "bin")
    if depth > 0:
        try:
            children = sorted(base.iterdir(), key=lambda c: c.name.lower())
        except OSError:
            children = []
        for child in children:
            if not child.is_dir() or child.name.startswith("."):
                continue
            for nested in expand_tool_dirs(child, depth - 1):
                _add(nested)
    return list(seen.values())


def _lookup_in(folder: Path, name: str) -> Optional[str]:
    for candidate in (folder / name, folder / f"{name}.exe"):
        if candidate.is_file():
            return str(candidate)
    return None


def _find_binary(name: str, configured: str) -> Optional[str]:
    if configured:
        target = Path(configured)
        if target.is_file():
            return str(target)
        for folder in expand_tool_dirs(target):
            found = _lookup_in(folder, name)
            if found:
                return found

    found = shutil.which(name)
    if found:
        return found

    roots: List[Path] = [Path(hint) for hint in _FFMPEG_ROOT_HINTS]

    local = _local_appdata()
    if local is not None:
        # winget(Gyan.FFmpeg) 설치 위치
        roots.append(local / "Microsoft" / "WinGet" / "Links")
        packages = local / "Microsoft" / "WinGet" / "Packages"
        if packages.is_dir():
            try:
                roots.extend(
                    child for child in packages.iterdir()
                    if child.is_dir() and "ffmpeg" in child.name.lower()
                )
            except OSError:
                pass

    # 사용자가 받은 그대로 두는 경우가 많은 위치
    home = Path.home()
    for folder in ("Downloads", "Desktop"):
        candidate = home / folder
        if candidate.is_dir():
            try:
                roots.extend(
                    child for child in candidate.iterdir()
                    if child.is_dir() and child.name.lower().startswith("ffmpeg")
                )
            except OSError:
                pass

    for root in roots:
        for folder in expand_tool_dirs(root):
            found = _lookup_in(folder, name)
            if found:
                return found
    return None


def ffmpeg_path() -> Optional[str]:
    return _find_binary("ffmpeg", load_settings().get("ffmpeg", ""))


def ffprobe_path() -> Optional[str]:
    return _find_binary("ffprobe", load_settings().get("ffprobe", ""))


def pycapcut_version() -> Optional[str]:
    try:
        from importlib.metadata import version

        return version("pycapcut")
    except Exception:
        return None
