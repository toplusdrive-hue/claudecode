"""캡컷 자동 편집기 실행 진입점.

    python run.py

서버를 띄우고 브라우저를 자동으로 엽니다.

⚠️ 요청서 3.16 — `pythonw.exe`로 띄우면 `sys.stdout`이 None이라 `print()`에서
   즉시 죽습니다. 이 파일은 처음부터 끝까지 `say()`만 씁니다.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_PORT = 8760
HOST = "127.0.0.1"


def say(*parts: object) -> None:
    """stdout이 없어도 죽지 않는 출력."""
    stream = sys.stdout
    if stream is None:
        return
    try:
        stream.write(" ".join(str(p) for p in parts) + "\n")
        stream.flush()
    except Exception:
        pass


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")


def find_port(start: int = DEFAULT_PORT, tries: int = 20) -> int:
    for offset in range(tries):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((HOST, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"{start}~{start + tries - 1} 범위에서 쓸 수 있는 포트를 찾지 못했습니다.")


def check_requirements() -> list[str]:
    missing: list[str] = []
    for module, package in [
        ("fastapi", "fastapi"),
        ("uvicorn", "uvicorn[standard]"),
        ("pycapcut", "pycapcut==0.0.3"),
    ]:
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    return missing


def main() -> int:
    _force_utf8()

    if sys.version_info[:2] != (3, 11):
        say(
            f"[경고] 검증된 파이썬은 3.11 입니다. 지금은 "
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} 입니다."
        )

    missing = check_requirements()
    if missing:
        say("[중단] 필요한 패키지가 없습니다:", ", ".join(missing))
        say("       아래 명령을 실행한 뒤 다시 시도해 주세요.")
        say("       pip install -r requirements.txt")
        return 1

    import uvicorn

    from app.main import app  # noqa: F401  (로깅 설정을 겸합니다)

    port = find_port()
    url = f"http://{HOST}:{port}/"

    say("캡컷 자동 편집기를 시작합니다.")
    say("  주소:", url)
    say("  종료: 이 창에서 Ctrl+C")
    say("")
    say("드래프트를 건드리는 작업은 캡컷을 완전히 종료한 상태에서만 할 수 있습니다.")

    def open_browser() -> None:
        time.sleep(1.2)
        try:
            webbrowser.open(url)
        except Exception as exc:
            say("[알림] 브라우저를 자동으로 열지 못했습니다:", exc)
            say("       위 주소를 브라우저에 직접 붙여넣어 주세요.")

    threading.Thread(target=open_browser, name="open-browser", daemon=True).start()

    try:
        uvicorn.run(app, host=HOST, port=port, log_level="warning", access_log=False)
    except KeyboardInterrupt:
        say("종료합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
