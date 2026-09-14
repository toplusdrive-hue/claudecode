"""폴더 탐색, 경로 정리, 경로 오타 진단.

요청서 5절: 경로 입력은 앱 내장 찾아보기 / 윈도우 기본 창 / 직접 붙여넣기
세 가지를 모두 제공하고, 오타가 있으면 **어디까지가 맞는 경로인지** 알려 줍니다.
"""

from __future__ import annotations

import os
import string
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import load_settings, save_settings

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".wmv", ".webm", ".mpg", ".mpeg", ".ts", ".flv"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
SCRIPT_EXTS = {".txt", ".srt", ".md"}


def clean_path(raw: str) -> str:
    """붙여넣은 경로에서 따옴표·앞뒤 공백을 제거하고 환경변수를 펼칩니다."""
    text = (raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    text = text.strip()
    # PowerShell의 & 'path' 형태
    if text.startswith("& "):
        text = text[2:].strip().strip("'\"")
    text = os.path.expandvars(os.path.expanduser(text))
    return text


def diagnose_path(raw: str) -> Dict[str, Any]:
    """존재하지 않는 경로에 대해 '어디까지 맞는지'를 알려 줍니다."""
    path = Path(clean_path(raw))
    if path.exists():
        return {"ok": True, "path": str(path), "is_dir": path.is_dir()}

    parts = list(path.parts)
    good = Path(parts[0]) if parts else Path(".")
    matched = 0
    for idx in range(1, len(parts)):
        candidate = good / parts[idx]
        if candidate.exists():
            good = candidate
            matched = idx
        else:
            break

    if not good.exists():
        return {
            "ok": False,
            "path": str(path),
            "valid_prefix": "",
            "message": f"'{path}' 를 찾을 수 없습니다. 드라이브 문자부터 다시 확인해 주세요.",
            "suggestions": [],
        }

    wrong = parts[matched + 1] if matched + 1 < len(parts) else ""
    suggestions: List[str] = []
    if wrong and good.is_dir():
        lowered = wrong.lower()
        try:
            for child in good.iterdir():
                name = child.name
                if name.lower().startswith(lowered[:2]) or lowered in name.lower():
                    suggestions.append(str(child))
        except OSError:
            pass
    return {
        "ok": False,
        "path": str(path),
        "valid_prefix": str(good),
        "wrong_part": wrong,
        "message": (
            f"'{good}' 까지는 맞는 경로입니다. 그 다음의 '{wrong}' 를 찾을 수 없습니다."
            if wrong
            else f"'{good}' 까지는 맞는 경로입니다."
        ),
        "suggestions": suggestions[:8],
    }


def list_drives() -> List[Dict[str, str]]:
    if sys.platform != "win32":
        return [{"path": "/", "label": "/"}]
    drives: List[Dict[str, str]] = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if os.path.exists(root):
            drives.append({"path": root, "label": root})
    return drives


def shortcuts() -> List[Dict[str, str]]:
    """찾아보기 시작 지점. 빈 화면으로 시작하지 않게 합니다 (요청서 5절)."""
    home = Path.home()
    items: List[Dict[str, str]] = []
    for label, path in [
        ("바탕 화면", home / "Desktop"),
        ("문서", home / "Documents"),
        ("동영상", home / "Videos"),
        ("다운로드", home / "Downloads"),
        ("사용자 폴더", home),
    ]:
        if path.is_dir():
            items.append({"label": label, "path": str(path)})
    return items


def last_dir() -> str:
    saved = load_settings().get("last_browse_dir") or ""
    if saved and Path(saved).is_dir():
        return saved
    for item in shortcuts():
        return item["path"]
    return str(Path.home())


def remember_dir(path: str) -> None:
    target = Path(clean_path(path))
    if target.is_file():
        target = target.parent
    if target.is_dir():
        save_settings({"last_browse_dir": str(target)})


def browse(path: Optional[str] = None, kind: str = "video") -> Dict[str, Any]:
    """폴더 하나의 내용을 나열합니다.

    `kind`: video | image | audio | script | any
    """
    target_raw = clean_path(path) if path else last_dir()
    target = Path(target_raw) if target_raw else Path(last_dir())

    if target.is_file():
        target = target.parent
    if not target.is_dir():
        diag = diagnose_path(target_raw)
        fallback = Path(diag.get("valid_prefix") or last_dir())
        return {
            "path": str(fallback),
            "error": diag.get("message"),
            "suggestions": diag.get("suggestions", []),
            "parent": str(fallback.parent) if fallback.parent != fallback else "",
            "dirs": [],
            "files": [],
            "drives": list_drives(),
            "shortcuts": shortcuts(),
        }

    exts = {
        "video": VIDEO_EXTS,
        "image": IMAGE_EXTS,
        "audio": AUDIO_EXTS,
        "script": SCRIPT_EXTS,
    }.get(kind)

    dirs: List[Dict[str, str]] = []
    files: List[Dict[str, Any]] = []
    try:
        for child in sorted(target.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            if child.name.startswith("$") or child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    dirs.append({"name": child.name, "path": str(child)})
                elif exts is None or child.suffix.lower() in exts:
                    files.append(
                        {
                            "name": child.name,
                            "path": str(child),
                            "size": child.stat().st_size,
                        }
                    )
            except OSError:
                continue
    except PermissionError:
        return {
            "path": str(target),
            "error": f"'{target}' 폴더를 읽을 권한이 없습니다. 다른 폴더를 골라 주세요.",
            "parent": str(target.parent) if target.parent != target else "",
            "dirs": [],
            "files": [],
            "drives": list_drives(),
            "shortcuts": shortcuts(),
        }

    remember_dir(str(target))
    return {
        "path": str(target),
        "error": None,
        "parent": str(target.parent) if target.parent != target else "",
        "dirs": dirs,
        "files": files,
        "drives": list_drives(),
        "shortcuts": shortcuts(),
    }


def expand_inputs(raw_paths: List[str], kind: str = "video") -> Dict[str, Any]:
    """직접 붙여넣은 경로 목록을 파일 목록으로 펼칩니다.

    **폴더를 넣으면 그 안의 영상 목록으로 펼쳐 줍니다** (요청서 5절).
    """
    exts = {
        "video": VIDEO_EXTS,
        "image": IMAGE_EXTS,
        "audio": AUDIO_EXTS,
        "script": SCRIPT_EXTS,
    }.get(kind, VIDEO_EXTS)

    files: List[str] = []
    problems: List[Dict[str, Any]] = []
    for raw in raw_paths:
        cleaned = clean_path(raw)
        if not cleaned:
            continue
        path = Path(cleaned)
        if path.is_dir():
            found = sorted(
                (str(c) for c in path.iterdir() if c.is_file() and c.suffix.lower() in exts),
                key=str.lower,
            )
            if not found:
                problems.append(
                    {
                        "input": raw,
                        "message": f"'{path}' 폴더 안에 사용할 수 있는 파일이 없습니다.",
                    }
                )
            files.extend(found)
            remember_dir(str(path))
        elif path.is_file():
            if path.suffix.lower() not in exts:
                problems.append(
                    {
                        "input": raw,
                        "message": f"'{path.name}' 은(는) 지원하지 않는 확장자입니다.",
                    }
                )
                continue
            files.append(str(path))
            remember_dir(str(path.parent))
        else:
            diag = diagnose_path(cleaned)
            problems.append({"input": raw, "message": diag["message"], "suggestions": diag.get("suggestions", [])})

    deduped: List[str] = []
    for item in files:
        if item not in deduped:
            deduped.append(item)
    return {"files": deduped, "problems": problems}
