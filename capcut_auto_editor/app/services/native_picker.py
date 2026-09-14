"""윈도우 기본 선택 창 — **별도 프로세스**로 띄웁니다.

같은 프로세스에서 tkinter를 띄우면 서버가 멈추고 타임아웃도 걸 수 없습니다.

⚠️ 요청서 3.15 — 하위 프로세스와 주고받는 한글은 UTF-8로 못을 박아야 합니다.
    자식 파이썬의 stdout 인코딩은 로케일(한국어 윈도우는 cp949)로 정해지는데
    부모가 UTF-8로 읽으면 한글 경로가 깨집니다. 환경에 따라 나타났다 안 나타났다 하고,
    개발 터미널에 PYTHONIOENCODING이 설정돼 있으면 정상 동작하다가 탐색기에서
    bat으로 띄우면 깨집니다.

    세 겹으로 막습니다.
      1) 자식이 시작하자마자 sys.stdout.reconfigure(encoding="utf-8")
      2) 부모가 자식 환경에 PYTHONIOENCODING=utf-8
      3) 그래도 U+FFFD가 섞이면 "경로의 한글이 깨졌습니다" 라고 명확히 알림
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
REPLACEMENT = "�"


# ── 자식 프로세스 본체 ──────────────────────────────────────────────────────
def _child_main(argv: List[str]) -> int:
    # (1) 자식이 시작하자마자 stdout을 UTF-8로 고정합니다.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    mode = argv[1] if len(argv) > 1 else "files"
    initial = argv[2] if len(argv) > 2 else ""
    kind = argv[3] if len(argv) > 3 else "video"

    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # pragma: no cover
        print(json.dumps({"ok": False, "error": f"tkinter를 불러오지 못했습니다: {exc}"}, ensure_ascii=False))
        return 1

    filetypes = {
        "video": [("영상 파일", "*.mp4 *.mov *.mkv *.avi *.m4v *.wmv *.webm"), ("모든 파일", "*.*")],
        "image": [("이미지 파일", "*.png *.jpg *.jpeg *.webp"), ("모든 파일", "*.*")],
        "audio": [("오디오 파일", "*.wav *.mp3 *.m4a *.aac *.flac"), ("모든 파일", "*.*")],
        "script": [("대본 파일", "*.txt *.srt *.md"), ("모든 파일", "*.*")],
    }.get(kind, [("모든 파일", "*.*")])

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    try:
        if mode == "folder":
            picked = filedialog.askdirectory(title="폴더 선택", initialdir=initial or None)
            paths = [picked] if picked else []
        else:
            picked = filedialog.askopenfilenames(
                title="파일 선택 (여러 개 고를 수 있습니다)",
                initialdir=initial or None,
                filetypes=filetypes,
            )
            paths = list(picked or [])
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"선택 창에서 오류가 났습니다: {exc}"}, ensure_ascii=False))
        return 1
    finally:
        try:
            root.destroy()
        except Exception:
            pass

    print(json.dumps({"ok": True, "paths": [str(p) for p in paths]}, ensure_ascii=False))
    return 0


# ── 부모 쪽 호출부 ──────────────────────────────────────────────────────────
def pick(mode: str = "files", initial: str = "", kind: str = "video", timeout: float = 180.0) -> Dict[str, Any]:
    """윈도우 기본 선택 창을 띄웁니다. 타임아웃이 있어 서버가 잠기지 않습니다."""
    env = dict(os.environ)
    # (2) 부모가 자식 환경에 인코딩을 못 박습니다.
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    try:
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), mode, initial, kind],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=env,
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"{int(timeout)}초 동안 선택이 없어 창을 닫았습니다. 다시 시도하거나 앱 안의 '찾아보기'를 써 주세요.",
        }
    except Exception as exc:
        return {"ok": False, "error": f"선택 창을 띄우지 못했습니다: {exc}"}

    stdout = proc.stdout.decode("utf-8", "replace").strip()
    stderr = proc.stderr.decode("utf-8", "replace").strip()

    if not stdout:
        return {
            "ok": False,
            "error": "선택 창이 아무것도 돌려주지 않았습니다. "
            + (f"({stderr[-300:]})" if stderr else "tkinter가 설치되어 있는지 확인해 주세요."),
        }

    try:
        payload = json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError:
        return {"ok": False, "error": f"선택 창의 응답을 해석하지 못했습니다: {stdout[-300:]}"}

    if not payload.get("ok"):
        return payload

    # (3) 그래도 깨진 문자가 섞이면 명확히 알립니다.
    broken = [p for p in payload.get("paths", []) if REPLACEMENT in p]
    if broken:
        return {
            "ok": False,
            "error": (
                "경로의 한글이 깨졌습니다. 선택한 폴더 이름에 한글이 들어 있고 "
                "파이썬이 cp949로 값을 넘긴 경우입니다. "
                "앱 안의 '찾아보기'로 고르거나, 경로를 직접 붙여넣어 주세요.\n"
                f"깨진 경로: {broken[0]}"
            ),
            "paths": [],
        }

    existing = [p for p in payload.get("paths", []) if Path(p).exists()]
    missing = [p for p in payload.get("paths", []) if not Path(p).exists()]
    result: Dict[str, Any] = {"ok": True, "paths": existing}
    if missing:
        result["warning"] = f"{len(missing)}개 경로를 찾지 못했습니다: {missing[0]}"
    return result


if __name__ == "__main__":
    raise SystemExit(_child_main(sys.argv))
