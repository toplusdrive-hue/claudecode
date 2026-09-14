"""캡컷 드래프트 읽기·쓰기·백업·캘리브레이션·후처리 교정.

여기 모인 교정 로직은 전부 "pyCapCut이 만든 결과와 캡컷이 만든 결과의 차이"를
메우는 것입니다. 각 함수 주석에 요청서 절 번호를 달아 두었습니다.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..config import (
    BACKUP_DIR,
    CAPCUT_PROCESS_NAMES,
    EXCLUDED_DRAFT_FOLDER_PREFIXES,
    resolve_draft_root,
)
from .logging_util import get_logger

log = get_logger(__name__)

DRAFT_CONTENT = "draft_content.json"
DRAFT_META = "draft_meta_info.json"
ROOT_META = "root_meta_info.json"

# 우리가 만든 트랙임을 표시하는 이름. 재실행 시 이 트랙만 지우고 다시 만듭니다.
AUTO_SUBTITLE_TRACK = "자동자막"
AUTO_SFX_TRACK = "자동효과음"

# 세션과 드래프트를 잇는 표식.
#
# ⚠️ 예전에는 이 값을 draft_content.json 안에 직접 넣었습니다. 하지만 그 파일은
#    캡컷이 파싱하는 파일이고, 캡컷에 없는 최상위 키를 넣으면 열 때 문제가 될 수
#    있습니다. 그래서 드래프트 폴더 안의 **별도 파일**로 뺐습니다.
#    캡컷은 자기가 모르는 파일은 건드리지 않습니다.
MARKER_KEY = "capcut_auto_editor"
MARKER_FILE = "capcut_auto_editor.json"


def read_marker(draft_path: Path) -> Dict[str, Any]:
    """드래프트에 남겨 둔 우리 표식을 읽습니다.

    예전 버전이 draft_content.json 안에 넣어 둔 것도 함께 읽어 줍니다.
    """
    path = Path(draft_path) / MARKER_FILE
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("'%s' 를 읽지 못했습니다.", path)
    try:  # 예전 형식 폴백
        return read_content(draft_path).get(MARKER_KEY) or {}
    except Exception:
        return {}


def write_marker(draft_path: Path, marker: Dict[str, Any]) -> None:
    path = Path(draft_path) / MARKER_FILE
    merged = read_marker(draft_path)
    merged.update(marker)
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")


def strip_legacy_marker(content: Dict[str, Any]) -> bool:
    """예전 버전이 draft_content.json 에 넣어 둔 키를 제거합니다."""
    return content.pop(MARKER_KEY, None) is not None


# ── 캡컷 실행 감지 (요청서 3.13) ─────────────────────────────────────────────
class CapCutRunning(RuntimeError):
    def __init__(self, processes: List[str]):
        self.processes = processes
        super().__init__(
            "캡컷이 실행 중이라 드래프트를 수정할 수 없습니다. "
            "캡컷은 프로젝트를 메모리에 들고 있다가 저장할 때 파일을 통째로 덮어쓰기 때문에, "
            "켜진 상태로 고치면 작업이 사라집니다. "
            f"캡컷을 완전히 종료한 뒤 다시 눌러 주세요. (감지된 프로세스 {len(processes)}개)"
        )


def running_capcut_processes() -> List[str]:
    """캡컷 프로세스 이름 목록. 멀티프로세스라 7~8개가 뜨는 게 정상입니다."""
    try:
        import psutil
    except ImportError:
        log.warning("psutil이 없어 캡컷 실행 여부를 확인하지 못했습니다.")
        return []

    found: List[str] = []
    for proc in psutil.process_iter(["name"]):
        try:
            name = (proc.info.get("name") or "").lower()
        except Exception:
            continue
        if not name:
            continue
        stem = name[:-4] if name.endswith(".exe") else name
        if stem in {n[:-4] if n.endswith(".exe") else n for n in CAPCUT_PROCESS_NAMES}:
            found.append(proc.info.get("name") or name)
    return found


def ensure_capcut_closed() -> None:
    processes = running_capcut_processes()
    if processes:
        raise CapCutRunning(processes)


def verify_capcut_still_closed() -> Optional[str]:
    """작업 **직후에도** 다시 검사합니다. 그 사이 캡컷이 켜졌으면 경고 문구를 돌려줍니다."""
    processes = running_capcut_processes()
    if not processes:
        return None
    return (
        "작업하는 동안 캡컷이 실행되었습니다. 캡컷이 지금 열고 있는 프로젝트를 저장하면 "
        "방금 반영한 내용이 덮어써질 수 있습니다. 캡컷을 저장하지 말고 완전히 종료한 뒤 다시 열어 확인해 주세요. "
        f"(감지된 프로세스 {len(processes)}개)"
    )


# ── 드래프트 폴더 / 목록 ─────────────────────────────────────────────────────
class DraftRootMissing(RuntimeError):
    pass


class RegistryUnreadable(RuntimeError):
    """root_meta_info.json 을 해석하지 못했을 때.

    이 파일은 캡컷의 **프로젝트 목록 정본**입니다. 읽지 못했다고 새로 만들어 덮어쓰면
    사용자의 프로젝트 목록이 통째로 날아갑니다. 그래서 덮어쓰지 않고 중단합니다.
    """


def require_root() -> Path:
    root = resolve_draft_root()
    if root is None:
        raise DraftRootMissing(
            "캡컷 드래프트 폴더를 찾지 못했습니다. 캡컷을 한 번 실행해 프로젝트를 하나 만든 뒤 다시 시도하거나, "
            r"0차 화면에서 '%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft' 형태의 경로를 직접 지정해 주세요."
        )
    return root


def is_target_folder(path: Path) -> bool:
    """클라우드/텍스트템플릿 폴더는 대상이 아닙니다 (요청서 3.1)."""
    name = path.name
    return not any(name.startswith(prefix) for prefix in EXCLUDED_DRAFT_FOLDER_PREFIXES)


def list_drafts(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """드래프트 목록. `root_meta_info.json`(정본)과 폴더 스캔을 합칩니다."""
    root = root or require_root()
    registry: Dict[str, Dict[str, Any]] = {}
    registry_path = root / ROOT_META
    if registry_path.exists():
        try:
            data = json.loads(registry_path.read_text(encoding="utf-8"))
            for entry in data.get("all_draft_store", []) or []:
                folder = Path(str(entry.get("draft_fold_path", ""))).name
                if folder:
                    registry[folder.lower()] = entry
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("root_meta_info.json 을 읽지 못했습니다: %s", exc)

    drafts: List[Dict[str, Any]] = []
    for child in sorted(root.iterdir(), key=lambda c: c.name.lower()):
        if not child.is_dir() or not (child / DRAFT_CONTENT).exists():
            continue
        entry = registry.get(child.name.lower())
        try:
            mtime = (child / DRAFT_CONTENT).stat().st_mtime
        except OSError:
            mtime = 0.0
        drafts.append(
            {
                "name": child.name,
                "path": str(child),
                "registered": entry is not None,
                "modified": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else "",
                "modified_ts": mtime,
            }
        )
    drafts.sort(key=lambda d: d["modified_ts"], reverse=True)
    return drafts


def read_content(draft_path: Path) -> Dict[str, Any]:
    path = Path(draft_path) / DRAFT_CONTENT
    if not path.exists():
        raise FileNotFoundError(f"'{path}' 를 찾을 수 없습니다. 캡컷 드래프트 폴더가 맞는지 확인해 주세요.")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_content(draft_path: Path, content: Dict[str, Any]) -> None:
    path = Path(draft_path) / DRAFT_CONTENT
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False, indent=4)
    tmp.replace(path)


# ── 백업 / 되돌리기 ──────────────────────────────────────────────────────────
def backup_draft(draft_path: Path, tag: str = "") -> Dict[str, Any]:
    """드래프트 폴더 전체를 backups/{프로젝트}_{타임스탬프}/ 로 복사합니다."""
    draft_path = Path(draft_path)
    if not draft_path.is_dir():
        raise FileNotFoundError(f"'{draft_path}' 드래프트 폴더가 없습니다.")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{tag}" if tag else ""
    dest = BACKUP_DIR / f"{draft_path.name}_{stamp}{suffix}"
    counter = 1
    while dest.exists():
        dest = BACKUP_DIR / f"{draft_path.name}_{stamp}{suffix}_{counter}"
        counter += 1
    shutil.copytree(draft_path, dest)
    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    log.info("백업 완료: %s (%.1fMB)", dest, size / 1024 / 1024)
    return {
        "path": str(dest),
        "name": dest.name,
        "draft": draft_path.name,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "size_bytes": size,
        "tag": tag,
    }


def list_backups(draft_name: Optional[str] = None) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for child in BACKUP_DIR.iterdir() if BACKUP_DIR.exists() else []:
        if not child.is_dir() or not (child / DRAFT_CONTENT).exists():
            continue
        if draft_name and not child.name.startswith(f"{draft_name}_"):
            continue
        stat = child.stat()
        items.append(
            {
                "path": str(child),
                "name": child.name,
                "created": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "created_ts": stat.st_mtime,
            }
        )
    items.sort(key=lambda i: i["created_ts"], reverse=True)
    return items


def backup_registry(root: Path) -> Optional[str]:
    """root_meta_info.json 을 건드리기 전에 따로 보관합니다.

    드래프트 폴더 백업(`backup_draft`)은 드래프트 폴더만 복사하는데,
    이 파일은 그 **부모 폴더**에 있어서 백업 대상에서 빠져 있었습니다.
    """
    registry_path = Path(root) / ROOT_META
    if not registry_path.is_file():
        return None
    folder = BACKUP_DIR / "registry"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"root_meta_info_{datetime.now():%Y%m%d_%H%M%S}.json"
    counter = 1
    while dest.exists():
        dest = folder / f"root_meta_info_{datetime.now():%Y%m%d_%H%M%S}_{counter}.json"
        counter += 1
    shutil.copy2(registry_path, dest)
    log.info("레지스트리 백업: %s (%d바이트)", dest, dest.stat().st_size)
    return str(dest)


def list_registry_backups() -> List[Dict[str, Any]]:
    folder = BACKUP_DIR / "registry"
    if not folder.is_dir():
        return []
    items = []
    for child in sorted(folder.glob("root_meta_info_*.json"), reverse=True):
        stat = child.stat()
        items.append({
            "path": str(child),
            "name": child.name,
            "size_bytes": stat.st_size,
            "created": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return items


def restore_registry(backup_path: Path, root: Path) -> Dict[str, Any]:
    """레지스트리를 백업 시점으로 되돌립니다."""
    ensure_capcut_closed()
    backup_path = Path(backup_path)
    registry_path = Path(root) / ROOT_META
    if not backup_path.is_file():
        raise FileNotFoundError(f"'{backup_path}' 백업 파일이 없습니다.")
    json.loads(backup_path.read_text(encoding="utf-8"))  # 온전한지 먼저 확인
    safety = backup_registry(root)
    shutil.copy2(backup_path, registry_path)
    return {"restored_from": str(backup_path), "safety_backup": safety}


def restore_backup(backup_path: Path, draft_path: Path) -> Dict[str, Any]:
    """되돌리기. 되돌리기 전 현재 상태도 한 번 더 백업해 둡니다."""
    ensure_capcut_closed()
    backup_path = Path(backup_path)
    draft_path = Path(draft_path)
    if not (backup_path / DRAFT_CONTENT).exists():
        raise FileNotFoundError(f"'{backup_path}' 는 온전한 백업이 아닙니다.")
    safety = backup_draft(draft_path, tag="되돌리기전") if draft_path.is_dir() else None
    if draft_path.exists():
        shutil.rmtree(draft_path)
    shutil.copytree(backup_path, draft_path)
    fix_meta_paths(draft_path)
    register_in_registry(draft_path)
    return {"restored_from": str(backup_path), "safety_backup": safety}


# ── 후처리 교정 ──────────────────────────────────────────────────────────────
def fix_track_render_index(content: Dict[str, Any]) -> int:
    """⚠️ 요청서 3.3 — 자막이 안 보이는 가장 흔한 원인.

    캡컷이 만든 드래프트는 `track_render_index`가 트랙 순서대로 0,1,2,3…인데
    pyCapCut은 **전부 0**으로 둡니다. 비디오와 텍스트가 같은 레이어가 되어
    자막이 영상에 가려집니다.

    저장 후 모든 트랙의 세그먼트에 트랙 인덱스를 다시 매깁니다.
    (조사 결과: `track_render_index`는 트랙이 아니라 **세그먼트**의 필드이고,
     `dumps()`가 트랙을 render_index로 정렬하므로 **파일을 다시 읽은 뒤** 교정해야 합니다.)
    """
    changed = 0
    for track_index, track in enumerate(content.get("tracks", []) or []):
        track["track_render_index"] = track_index
        for segment in track.get("segments", []) or []:
            if segment.get("track_render_index") != track_index:
                segment["track_render_index"] = track_index
                changed += 1
    return changed


def fix_canvas_ratio(content: Dict[str, Any]) -> bool:
    """요청서 3.10 — dumps()가 ratio를 항상 "original"로 씁니다.

    세로(9:16) 드래프트는 캡컷 실물이 "9:16"을 쓰므로 저장 후 교정합니다.
    """
    canvas = content.get("canvas_config") or {}
    width = int(canvas.get("width") or 0)
    height = int(canvas.get("height") or 0)
    if width <= 0 or height <= 0:
        return False
    ratio = _ratio_name(width, height)
    if canvas.get("ratio") == ratio:
        return False
    canvas["ratio"] = ratio
    content["canvas_config"] = canvas
    return True


def _ratio_name(width: int, height: int) -> str:
    known = {
        (9, 16): "9:16", (16, 9): "original", (1, 1): "1:1",
        (4, 3): "4:3", (3, 4): "3:4", (21, 9): "21:9",
    }
    from math import gcd

    divisor = gcd(width, height) or 1
    key = (width // divisor, height // divisor)
    return known.get(key, "original")


# draft_content.json 최상위에서 "이 드래프트를 만든 캡컷이 누구인지" 말해 주는 필드들.
# pyCapCut 번들 템플릿은 app_version "6.7.0" 을 주장합니다. 검증 대상은 9.3.0.3969 라
# 세 단계나 차이가 납니다. 추측해서 채우지 않고, 캘리브레이션 때 읽어 둔
# **사용자의 실제 드래프트 값**을 그대로 씁니다.
VERSION_FIELDS = ("version", "new_version", "platform", "last_modified_platform", "app_version")


def extract_version_meta(content: Dict[str, Any]) -> Dict[str, Any]:
    """실제 캡컷이 만든 드래프트에서 버전 관련 최상위 필드를 뽑습니다."""
    return {key: copy.deepcopy(content[key]) for key in VERSION_FIELDS if key in content}


def apply_reference_version(content: Dict[str, Any]) -> Dict[str, Any]:
    """캘리브레이션으로 저장해 둔 버전 메타를 새 드래프트에 입힙니다.

    저장된 값이 없으면 아무것도 하지 않습니다(번들 템플릿 값을 그대로 둡니다).
    """
    from ..config import load_style_profile

    profile = load_style_profile() or {}
    reference = profile.get("version_meta") or {}
    applied: Dict[str, Any] = {}
    for key, value in reference.items():
        if key in VERSION_FIELDS and content.get(key) != value:
            content[key] = copy.deepcopy(value)
            applied[key] = value
    return applied


def fix_meta_paths(draft_path: Path) -> Dict[str, Any]:
    """요청서 3.11 — draft_meta_info.json의 경로는 신뢰할 수 없습니다.

    캡컷에서 이름을 바꿔도 갱신되지 않고, duplicate_as_template도 고쳐주지 않습니다.
    **저장할 때마다 실제 폴더 경로로 덮어씁니다.**

    추가 발견: pyCapCut 번들 템플릿의 `draft_id`가 고정 상수라
    모든 드래프트가 같은 id를 갖습니다. 드래프트마다 새 UUID를 넣습니다.
    """
    draft_path = Path(draft_path)
    meta_path = draft_path / DRAFT_META
    meta: Dict[str, Any] = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}

    windows_path = str(draft_path)
    draft_id = str(meta.get("draft_id") or "")
    # 번들 템플릿의 고정 id면 새로 발급합니다.
    if not draft_id or draft_id.upper() == "792BD5DA-E961-4821-B10E-F51E4683DEC0":
        draft_id = str(uuid.uuid4()).upper()

    meta["draft_fold_path"] = windows_path
    meta["draft_name"] = draft_path.name
    meta["draft_id"] = draft_id
    meta["draft_root_path"] = str(draft_path.parent)
    meta["tm_draft_modified"] = int(time.time() * 1_000_000)
    meta.setdefault("tm_draft_create", meta["tm_draft_modified"])
    meta.setdefault("draft_timeline_materials_size_", 0)

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=4), encoding="utf-8")
    return {"draft_id": draft_id, "draft_fold_path": windows_path, "draft_name": draft_path.name}


def register_in_registry(draft_path: Path) -> Dict[str, Any]:
    """요청서 3.12 — create_draft는 레지스트리에 등록하지 않습니다.

    `root_meta_info.json`의 `all_draft_store`에 직접 등록해야 캡컷 목록에 뜹니다.
    """
    draft_path = Path(draft_path)
    root = draft_path.parent
    registry_path = root / ROOT_META

    # ⚠️ 이 파일은 캡컷 프로젝트 목록의 정본입니다. 읽지 못했다고 새로 만들어
    #    덮어쓰면 사용자의 프로젝트가 목록에서 통째로 사라집니다.
    #    따라서 해석에 실패하면 **쓰지 않고 중단**합니다.
    registry: Dict[str, Any] = {}
    existing_count = 0
    if registry_path.exists():
        raw = registry_path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")  # 캡컷이 BOM을 붙이는 경우 대비
        except UnicodeDecodeError:
            try:
                text = raw.decode("cp949")
            except UnicodeDecodeError as exc:
                raise RegistryUnreadable(
                    f"'{registry_path}' 의 인코딩을 알 수 없어 건드리지 않았습니다. "
                    "이 파일은 캡컷 프로젝트 목록의 정본이라, 잘못 덮어쓰면 목록이 사라집니다. "
                    f"({exc})"
                ) from exc
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RegistryUnreadable(
                f"'{registry_path}' 를 해석하지 못해 건드리지 않았습니다. "
                "이 파일은 캡컷 프로젝트 목록의 정본입니다. 캡컷이 실행 중이면 완전히 종료한 뒤 "
                f"다시 시도해 주세요. (JSON {exc.lineno}번째 줄: {exc.msg})"
            ) from exc
        if not isinstance(loaded, dict):
            raise RegistryUnreadable(
                f"'{registry_path}' 의 형식이 예상과 다릅니다(최상위가 객체가 아님). 건드리지 않았습니다."
            )
        registry = loaded
        existing_count = len(registry.get("all_draft_store") or [])

    # 고치기 전에 따로 보관해 둡니다.
    registry_backup = backup_registry(root)

    meta = fix_meta_paths(draft_path)
    store: List[Dict[str, Any]] = list(registry.get("all_draft_store") or [])
    now_us = int(time.time() * 1_000_000)

    cover = ""
    for candidate in ("draft_cover.jpg", "draft_cover.png"):
        if (draft_path / candidate).exists():
            cover = str(draft_path / candidate)
            break

    duration = 0
    try:
        duration = int(read_content(draft_path).get("duration", 0) or 0)
    except Exception:
        duration = 0

    entry = {
        "draft_cloud_last_action_download": False,
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": cover,
        "draft_fold_path": str(draft_path),
        "draft_id": meta["draft_id"],
        "draft_is_ai_shorts": False,
        "draft_is_invisible": False,
        "draft_json_file": str(draft_path / DRAFT_CONTENT),
        "draft_name": draft_path.name,
        "draft_new_version": "",
        "draft_root_path": str(root),
        "draft_timeline_materials_size": 0,
        "draft_type": "",
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_modified": 0,
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_draft_removed": 0,
        "tm_duration": duration,
    }

    replaced = False
    for index, existing in enumerate(store):
        if str(existing.get("draft_fold_path", "")).lower() == str(draft_path).lower():
            entry["tm_draft_create"] = existing.get("tm_draft_create", now_us)
            store[index] = entry
            replaced = True
            break
    if not replaced:
        store.insert(0, entry)

    registry["all_draft_store"] = store
    registry.setdefault("root_path", str(root))
    registry.setdefault("draft_ids", [])

    tmp = registry_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(registry, ensure_ascii=False, indent=4), encoding="utf-8")
    tmp.replace(registry_path)

    # 쓴 뒤 다시 읽어 확인합니다. 기존 항목이 사라졌으면 즉시 되돌립니다.
    try:
        written = json.loads(registry_path.read_text(encoding="utf-8-sig"))
        written_count = len(written.get("all_draft_store") or [])
        found = any(
            str(e.get("draft_fold_path", "")).lower() == str(draft_path).lower()
            for e in written.get("all_draft_store") or []
        )
    except (OSError, json.JSONDecodeError) as exc:
        written_count, found = -1, False
        log.error("레지스트리를 다시 읽지 못했습니다: %s", exc)

    expected = existing_count if replaced else existing_count + 1
    if not found or written_count != expected:
        if registry_backup:
            shutil.copy2(registry_backup, registry_path)
            raise RuntimeError(
                f"캡컷 목록에 등록한 결과가 이상해서 되돌렸습니다 "
                f"(기존 {existing_count}개 → 기대 {expected}개, 실제 {written_count}개). "
                f"백업: {registry_backup}"
            )
        raise RuntimeError(
            f"캡컷 목록 등록 결과가 이상합니다 (기존 {existing_count}개 → 실제 {written_count}개)."
        )

    log.info(
        "드래프트 '%s' 를 캡컷 목록에 %s했습니다. (%d개 → %d개)",
        draft_path.name, "갱신" if replaced else "등록", existing_count, written_count,
    )
    return {
        "registered": True,
        "updated": replaced,
        "draft_id": meta["draft_id"],
        "entries_before": existing_count,
        "entries_after": written_count,
        "registry_backup": registry_backup,
    }


# ── 폰트 파일 찾기 (요청서 3.6) ──────────────────────────────────────────────
_WEIGHT_TOKENS = {
    "thin": 100, "extralight": 200, "ultralight": 200, "light": 300,
    "regular": 400, "normal": 400, "book": 400, "medium": 500,
    "semibold": 600, "demibold": 600, "bold": 700, "extrabold": 800,
    "ultrabold": 800, "black": 900, "heavy": 900,
}
_FONT_EXTS = {".otf", ".ttf", ".ttc", ".otc"}


def font_dirs() -> List[Path]:
    dirs: List[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    win = os.environ.get("SystemRoot", r"C:\Windows")
    dirs.append(Path(win) / "Fonts")
    if sys.platform != "win32":
        dirs.extend([Path("/usr/share/fonts"), Path.home() / ".fonts"])
    return [d for d in dirs if d.is_dir()]


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def parse_font_name(name: str) -> Tuple[str, str]:
    """'Pretendard-Bold' → ('Pretendard', 'Bold')"""
    stem = Path(name).stem if Path(name).suffix.lower() in _FONT_EXTS else name
    tokens = re.split(r"[-_\s]+", stem)
    if len(tokens) > 1 and _normalize(tokens[-1]) in _WEIGHT_TOKENS:
        return "-".join(tokens[:-1]), tokens[-1]
    return stem, ""


def find_font_file(family: str, style: str = "") -> Optional[str]:
    """글꼴 파일의 절대 경로를 찾습니다.

    ⚠️ 요청서 3.6 — "Bold"만 맞아도 통과시키면 `Arial Bold` → `NotoSansKR-Bold`
    같은 오매칭이 납니다. **글꼴 계열 이름이 반드시 맞아야** 합니다.
    """
    if not family:
        return None
    want_family = _normalize(family)
    want_style = _normalize(style) or "regular"
    if not want_family:
        return None

    best: Optional[Tuple[int, str]] = None
    for directory in font_dirs():
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for item in entries:
            if not item.is_file() or item.suffix.lower() not in _FONT_EXTS:
                continue
            file_family, file_style = parse_font_name(item.name)
            if _normalize(file_family) != want_family:
                continue  # ← 계열 이름 불일치는 무조건 탈락
            file_style_norm = _normalize(file_style) or "regular"
            if file_style_norm == want_style:
                score = 0
            elif _WEIGHT_TOKENS.get(file_style_norm) == _WEIGHT_TOKENS.get(want_style):
                score = 1
            elif want_style == "regular":
                score = 3
            else:
                score = 5
            if best is None or score < best[0]:
                best = (score, str(item))
                if score == 0:
                    return str(item)
    return best[1] if best else None


def to_capcut_path(path: str) -> str:
    """캡컷이 쓰는 슬래시 경로 형태로 바꿉니다."""
    return str(path).replace("\\", "/")


def list_installed_fonts(limit: int = 400) -> List[Dict[str, str]]:
    seen: Dict[str, Dict[str, str]] = {}
    for directory in font_dirs():
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for item in entries:
            if not item.is_file() or item.suffix.lower() not in _FONT_EXTS:
                continue
            family, style = parse_font_name(item.name)
            key = f"{_normalize(family)}|{_normalize(style)}"
            if key in seen:
                continue
            seen[key] = {"family": family, "style": style, "path": str(item)}
            if len(seen) >= limit:
                return list(seen.values())
    return list(seen.values())


# ── 캘리브레이션 ────────────────────────────────────────────────────────────
@dataclass
class CalibrationResult:
    profile: Dict[str, Any]
    warnings: List[str]


def _text_materials(content: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(item.get("id")): item
        for item in (content.get("materials", {}) or {}).get("texts", []) or []
        if item.get("id")
    }


def calibrate_text_style(draft_path: Path) -> CalibrationResult:
    """참조 드래프트에서 자막 스타일을 그대로 추출합니다.

    요청서 3.5 — pyCapCut이 만드는 텍스트는 필드가 대거 빠져 있습니다
    (세그먼트 27개, 소재 91개). 그래서 **캡컷 원본 소재를 통째로 저장해 두고,
    그것을 바탕으로 텍스트만 갈아 끼웁니다.** 빈 껍데기에 스타일만 얹으면 안 됩니다.
    """
    content = read_content(draft_path)
    materials = _text_materials(content)
    warnings: List[str] = []

    canvas = content.get("canvas_config") or {}
    canvas_w = int(canvas.get("width") or 1920)
    canvas_h = int(canvas.get("height") or 1080)

    samples: List[Tuple[Dict[str, Any], Dict[str, Any], int]] = []
    for track_index, track in enumerate(content.get("tracks", []) or []):
        if track.get("type") != "text":
            continue
        for segment in track.get("segments", []) or []:
            material = materials.get(str(segment.get("material_id")))
            if material:
                samples.append((segment, material, track_index))

    if not samples:
        raise ValueError(
            f"'{Path(draft_path).name}' 드래프트에 텍스트 트랙이 없습니다. "
            "캡컷에서 자막을 하나 이상 만든 드래프트를 골라 주세요."
        )

    # 가장 흔한 모양을 대표값으로 씁니다(설명 자막 하나만 다른 경우를 피하려고).
    def _fingerprint(pair: Tuple[Dict[str, Any], Dict[str, Any], int]) -> str:
        segment, material, _ = pair
        clip = segment.get("clip") or {}
        transform = clip.get("transform") or {}
        return json.dumps(
            {
                "check_flag": material.get("check_flag"),
                "bg": material.get("background_color"),
                "bg_style": material.get("background_style"),
                "y": round(float(transform.get("y", 0.0)), 3),
                "content": _content_style_key(material),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    groups: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any], int]]] = {}
    for pair in samples:
        groups.setdefault(_fingerprint(pair), []).append(pair)
    representative = max(groups.values(), key=len)
    segment, material, track_index = representative[0]

    parsed = _parse_content(material)
    style0 = (parsed.get("styles") or [{}])[0]
    clip = segment.get("clip") or {}
    transform = clip.get("transform") or {}
    scale = clip.get("scale") or {}

    font_path = str(style0.get("font", {}).get("path") or material.get("font_path") or "")
    font_family, font_style = ("", "")
    if font_path:
        font_family, font_style = parse_font_name(Path(font_path.replace("/", os.sep)).name)
    if not font_family:
        font_family = str(material.get("font_name") or "")

    check_flag = int(material.get("check_flag") or 7)
    background_style = int(material.get("background_style") or 0)

    if background_style == 0 and (check_flag & 16):
        warnings.append(
            "참조 드래프트의 background_style이 0입니다. 배경 비트는 켜져 있지만 "
            "스타일이 0이면 배경이 표시되지 않습니다. 1로 올려 저장합니다."
        )
        background_style = 1
    if background_style >= 1 and not (check_flag & 16):
        warnings.append("참조 드래프트에 배경 비트(+16)가 없어 배경이 표시되지 않습니다. check_flag에 16을 더합니다.")
        check_flag |= 16
    if not font_path:
        warnings.append("참조 드래프트에서 폰트 파일 경로를 읽지 못했습니다. 자막 화면에서 글꼴을 직접 골라 주세요.")

    profile = {
        "source_draft": str(draft_path),
        "source_draft_name": Path(draft_path).name,
        "calibrated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "manual": False,
        "canvas": {"width": canvas_w, "height": canvas_h},
        "sample_count": len(samples),
        "group_count": len(groups),
        "font": {
            "family": font_family,
            "style": font_style,
            "path": to_capcut_path(font_path),
            "resource_id": str(style0.get("font", {}).get("id") or material.get("font_resource_id") or ""),
        },
        "text": {
            "size": float(style0.get("size") or material.get("font_size") or 5.0),
            "color_rgb": list(
                ((style0.get("fill") or {}).get("content") or {}).get("solid", {}).get("color", [0.0, 0.0, 0.0])
            ),
            "color_hex": str(material.get("text_color") or "#000000"),
            "bold": bool(style0.get("bold", False)),
            "italic": bool(style0.get("italic", False)),
            "alignment": int(material.get("alignment") or 1),
            "letter_spacing": material.get("letter_spacing", 0),
            "line_spacing": material.get("line_spacing", 0.02),
            "line_max_width": material.get("line_max_width", 0.82),
        },
        "background": {
            "color": str(material.get("background_color") or "#ffffff"),
            "style": background_style,
            "alpha": float(material.get("background_alpha") or 1.0),
            "width": float(material.get("background_width") or 0.28),
            "height": float(material.get("background_height") or 0.28),
            "round_radius": float(material.get("background_round_radius") or 0.4),
            "horizontal_offset": float(material.get("background_horizontal_offset") or 0.0),
            "vertical_offset": float(material.get("background_vertical_offset") or 0.0),
        },
        "border": _extract_border(style0, material),
        "check_flag": check_flag,
        "position": {
            "x": float(transform.get("x") or 0.0),
            "y": float(transform.get("y") or 0.0),
            "scale_x": float(scale.get("x") or 1.0),
            "scale_y": float(scale.get("y") or 1.0),
            "y_px": round(float(transform.get("y") or 0.0) * canvas_h / 2, 1),
        },
        "track_index": track_index,
        # ⚠️ 요청서 3.5 — 원본 소재/세그먼트를 통째로 저장해 둡니다.
        "raw_material": material,
        "raw_segment": segment,
        "raw_material_field_count": len(material),
        "raw_segment_field_count": len(segment),
        # 이 드래프트를 만든 캡컷이 주장하는 버전 정보. 새 드래프트에 그대로 씁니다.
        "version_meta": extract_version_meta(content),
    }

    log.info(
        "자막 스타일 캘리브레이션 완료: 소재 %d필드 / 세그먼트 %d필드 / 표본 %d개",
        len(material), len(segment), len(samples),
    )
    return CalibrationResult(profile, warnings)


def _content_style_key(material: Dict[str, Any]) -> Dict[str, Any]:
    parsed = _parse_content(material)
    style0 = (parsed.get("styles") or [{}])[0]
    return {
        "size": style0.get("size"),
        "font": (style0.get("font") or {}).get("path"),
        "bold": style0.get("bold"),
    }


def _parse_content(material: Dict[str, Any]) -> Dict[str, Any]:
    raw = material.get("content")
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def _extract_border(style0: Dict[str, Any], material: Dict[str, Any]) -> Dict[str, Any]:
    strokes = style0.get("strokes") or []
    if strokes:
        stroke = strokes[0]
        solid = ((stroke.get("content") or {}).get("solid") or {})
        return {
            "enabled": True,
            "width": float(stroke.get("width") or 0.08),
            "color_rgb": list(solid.get("color") or [1.0, 1.0, 1.0]),
            "alpha": float(solid.get("alpha") or 1.0),
            "color_hex": str(material.get("border_color") or "#ffffff"),
        }
    return {
        "enabled": bool(material.get("border_color")),
        "width": float(material.get("border_width") or 0.08),
        "color_rgb": [1.0, 1.0, 1.0],
        "alpha": 1.0,
        "color_hex": str(material.get("border_color") or "#ffffff"),
    }


def manual_style_profile(options: Dict[str, Any]) -> CalibrationResult:
    """수동 입력 폴백.

    요청서: **추정값임을 명확히 표시**하고, `background_style=1`,
    check_flag 배경 비트, 폰트 파일 경로를 반드시 채웁니다.
    """
    family = str(options.get("font_family") or "Pretendard")
    style = str(options.get("font_style") or "Bold")
    canvas_w = int(options.get("canvas_width") or 1920)
    canvas_h = int(options.get("canvas_height") or 1080)

    warnings = [
        "수동 입력으로 만든 **추정값**입니다. 캡컷에서 자막이 실제로 어떻게 보이는지 "
        "반드시 눈으로 확인해 주세요. 참조 드래프트로 캘리브레이션하는 쪽이 훨씬 정확합니다."
    ]

    font_path = str(options.get("font_path") or "") or (find_font_file(family, style) or "")
    if not font_path:
        warnings.append(
            f"'{family} {style}' 글꼴 파일을 찾지 못했습니다. 캡컷에서 글꼴이 기본값으로 바뀝니다. "
            r"%LOCALAPPDATA%\Microsoft\Windows\Fonts 또는 C:\Windows\Fonts 에 설치했는지 확인해 주세요."
        )

    raw_bg_style = options.get("background_style")
    background_style = 1 if raw_bg_style is None else int(raw_bg_style)
    if background_style < 1:
        background_style = 1
        warnings.append("background_style이 0이면 배경이 표시되지 않아 1로 올렸습니다 (요청서 3.4).")

    check_flag = 7 | 8 | 16  # 기본 + 테두리 + 배경

    y_norm = options.get("position_y")
    if y_norm is None:
        y_px = float(options.get("position_y_px", -394.0))
        y_norm = y_px / (canvas_h / 2)
        warnings.append(
            f"자막 Y 위치를 픽셀({y_px:g}px) 기준으로 받아 정규화 좌표 {y_norm:.4f}로 환산했습니다. "
            "4K 기준 값을 1080 캔버스에 그대로 넣으면 화면 밖으로 나갑니다 (요청서 3.2)."
        )

    profile = {
        "source_draft": "",
        "source_draft_name": "(수동 입력)",
        "calibrated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "manual": True,
        "canvas": {"width": canvas_w, "height": canvas_h},
        "sample_count": 0,
        "group_count": 0,
        "font": {
            "family": family,
            "style": style,
            "path": to_capcut_path(font_path),
            "resource_id": "",
        },
        "text": {
            "size": float(options.get("font_size") or 5.0),
            "color_rgb": [0.0, 0.0, 0.0],
            "color_hex": str(options.get("text_color") or "#000000"),
            "bold": bool(options.get("bold", True)),
            "italic": False,
            "alignment": 1,
            "letter_spacing": 0,
            "line_spacing": 0.02,
            "line_max_width": 0.82,
        },
        "background": {
            "color": str(options.get("background_color") or "#ffffff"),
            "style": background_style,
            "alpha": float(options.get("background_alpha") or 1.0),
            "width": float(options.get("background_width") or 0.28),
            "height": float(options.get("background_height") or 0.28),
            "round_radius": float(options.get("background_round_radius") or 0.4),
            "horizontal_offset": 0.0,
            "vertical_offset": 0.0,
        },
        "border": {
            "enabled": True,
            "width": float(options.get("border_width") or 0.08),
            "color_rgb": [1.0, 1.0, 1.0],
            "alpha": 1.0,
            "color_hex": str(options.get("border_color") or "#ffffff"),
        },
        "check_flag": check_flag,
        "position": {
            "x": float(options.get("position_x") or 0.0),
            "y": float(y_norm),
            "scale_x": 1.0,
            "scale_y": 1.0,
            "y_px": round(float(y_norm) * canvas_h / 2, 1),
        },
        "track_index": 1,
        "raw_material": None,
        "raw_segment": None,
        "raw_material_field_count": 0,
        "raw_segment_field_count": 0,
    }
    return CalibrationResult(profile, warnings)


def calibrate_transitions(draft_path: Path) -> List[Dict[str, Any]]:
    """참조 드래프트에서 트랜지션을 읽어 effect_id로 enum을 역조회합니다.

    요청서 3.7 — TransitionType enum은 1137개이고 이름이 전부 중국어라
    한국어 UI 이름과 매칭되지 않습니다. **하지만 effect_id 역조회는 정확히 동작합니다.**
    """
    content = read_content(draft_path)
    transitions = (content.get("materials", {}) or {}).get("transitions", []) or []
    lookup = transition_lookup()

    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in transitions:
        effect_id = str(item.get("effect_id") or "")
        if not effect_id or effect_id in seen:
            continue
        seen.add(effect_id)
        enum_name = lookup.get(effect_id)
        out.append(
            {
                "effect_id": effect_id,
                "resource_id": str(item.get("resource_id") or ""),
                "draft_name": str(item.get("name") or ""),
                "enum_name": enum_name or "",
                "matched": enum_name is not None,
                "duration_us": int(item.get("duration") or 500_000),
                "is_overlap": bool(item.get("is_overlap", False)),
            }
        )
    return out


_TRANSITION_LOOKUP: Optional[Dict[str, str]] = None


def transition_lookup() -> Dict[str, str]:
    """effect_id → TransitionType 멤버 이름."""
    global _TRANSITION_LOOKUP
    if _TRANSITION_LOOKUP is not None:
        return _TRANSITION_LOOKUP
    mapping: Dict[str, str] = {}
    try:
        from pycapcut import TransitionType

        for member in TransitionType:
            mapping[str(member.value.effect_id)] = member.name
    except Exception as exc:
        log.warning("TransitionType을 불러오지 못했습니다: %s", exc)
    _TRANSITION_LOOKUP = mapping
    return mapping


def all_transitions() -> List[Dict[str, Any]]:
    """전체 트랜지션 목록 (드롭다운용). 이름은 중국어입니다."""
    out: List[Dict[str, Any]] = []
    try:
        from pycapcut import TransitionType

        for member in TransitionType:
            meta = member.value
            out.append(
                {
                    "enum_name": member.name,
                    "name": getattr(meta, "name", member.name),
                    "effect_id": str(getattr(meta, "effect_id", "")),
                    "default_duration": int(getattr(meta, "default_duration", 500_000) or 500_000),
                }
            )
    except Exception as exc:
        log.warning("TransitionType 목록을 불러오지 못했습니다: %s", exc)
    return out


# ── 검증 ────────────────────────────────────────────────────────────────────
def inspect_draft(draft_path: Path) -> Dict[str, Any]:
    """파일을 **다시 읽어** 실제 상태를 확인합니다 (요청서 7절 — 적용 후 검증)."""
    content = read_content(draft_path)
    canvas = content.get("canvas_config") or {}
    tracks_info: List[Dict[str, Any]] = []
    text_count = 0
    transition_ids: set[str] = set()

    for index, track in enumerate(content.get("tracks", []) or []):
        segments = track.get("segments", []) or []
        render_indexes = sorted({int(s.get("track_render_index", 0) or 0) for s in segments})
        if track.get("type") == "text":
            text_count += len(segments)
        tracks_info.append(
            {
                "index": index,
                "type": track.get("type"),
                "name": track.get("name") or "",
                "segment_count": len(segments),
                "track_render_index": render_indexes,
                "layered_correctly": render_indexes in ([index], []),
            }
        )

    materials = content.get("materials", {}) or {}
    for item in materials.get("transitions", []) or []:
        if item.get("effect_id"):
            transition_ids.add(str(item["effect_id"]))

    photo_clips = [v for v in (materials.get("videos") or []) if v.get("type") == "photo"]

    problems: List[str] = []
    for info in tracks_info:
        if not info["layered_correctly"]:
            problems.append(
                f"{info['index']}번 트랙({info['type']})의 track_render_index가 {info['track_render_index']} 입니다. "
                f"{info['index']} 이어야 자막이 영상 위에 보입니다 (요청서 3.3)."
            )
    expected_ratio = _ratio_name(int(canvas.get("width") or 0) or 1920, int(canvas.get("height") or 0) or 1080)
    if canvas.get("ratio") != expected_ratio:
        problems.append(
            f"canvas_config.ratio 가 '{canvas.get('ratio')}' 입니다. "
            f"{canvas.get('width')}x{canvas.get('height')} 라면 '{expected_ratio}' 여야 합니다 (요청서 3.10)."
        )

    return {
        "draft": Path(draft_path).name,
        "path": str(draft_path),
        "canvas": {"width": canvas.get("width"), "height": canvas.get("height"), "ratio": canvas.get("ratio")},
        "duration_us": int(content.get("duration") or 0),
        "fps": content.get("fps"),
        "tracks": tracks_info,
        "text_segment_count": text_count,
        "transition_count": len(transition_ids),
        "photo_material_count": len(photo_clips),
        "marker": read_marker(draft_path),
        "problems": problems,
    }


def finalize_draft(draft_path: Path, *, marker: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """저장 직후 반드시 거쳐야 하는 교정 묶음.

    3.3 레이어 / 3.10 캔버스 비율 / 3.11 메타 경로 / 3.12 레지스트리 등록
    + draft_id 중복(추가 발견) 을 한 번에 처리하고 결과를 다시 읽어 검증합니다.
    """
    draft_path = Path(draft_path)
    content = read_content(draft_path)

    layer_fixes = fix_track_render_index(content)
    ratio_fixed = fix_canvas_ratio(content)
    legacy_removed = strip_legacy_marker(content)
    version_applied = apply_reference_version(content)
    write_content(draft_path, content)

    if marker is not None:
        write_marker(draft_path, marker)

    meta = fix_meta_paths(draft_path)
    registry = register_in_registry(draft_path)

    verified = inspect_draft(draft_path)  # ← 파일을 다시 읽어 확인
    return {
        "layer_fixes": layer_fixes,
        "ratio_fixed": ratio_fixed,
        "legacy_marker_removed": legacy_removed,
        "version_applied": version_applied,
        "meta": meta,
        "registry": registry,
        "verified": verified,
    }
