"""설치 파일이 한국어 윈도우에서 읽히는지 검증.

⚠️ 실제로 겪은 문제 — requirements.txt에 한글 주석을 넣었더니 설치가 실패했습니다.

    UnicodeDecodeError: 'cp949' codec can't decode byte 0x80 in position 4

pip는 requirements.txt를 **시스템 로케일 코덱**으로 읽습니다. 한국어 윈도우는
cp949라, UTF-8로 저장된 한글이 들어 있으면 그대로 깨집니다.
요청서 3.15와 같은 유형(인코딩을 환경에 맡기면 안 된다)이 설치 파일에서 재발한 것입니다.

→ requirements.txt는 **ASCII만** 씁니다. 한국어 설명은 README.md에 둡니다.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements.txt"
LAUNCHER = ROOT / "캡컷 자동 편집기 실행.bat"


def test_requirements_is_ascii_only():
    raw = REQUIREMENTS.read_bytes()
    offenders = [(index, byte) for index, byte in enumerate(raw) if byte > 127]
    assert not offenders, (
        f"requirements.txt에 비ASCII 바이트가 {len(offenders)}개 있습니다 "
        f"(첫 위치 {offenders[0][0]}, 0x{offenders[0][1]:02x}). "
        "한국어 윈도우의 pip가 cp949로 읽어 설치가 실패합니다. 한국어 설명은 README.md에 두십시오."
    )


@pytest.mark.parametrize("codec", ["cp949", "euc-kr", "utf-8", "latin-1"])
def test_requirements_decodes_under_any_locale(codec):
    """어떤 로케일 코덱으로 읽어도 깨지지 않아야 합니다."""
    REQUIREMENTS.read_bytes().decode(codec)


def test_requirements_lists_every_runtime_dependency():
    text = REQUIREMENTS.read_text(encoding="ascii")
    for package in [
        "fastapi", "uvicorn[standard]", "python-multipart",
        "pycapcut==0.0.3", "faster-whisper==1.2.1",
        "rapidfuzz", "psutil", "pymediainfo", "imageio", "uiautomation",
    ]:
        assert package in text, f"requirements.txt에 '{package}' 가 빠졌습니다."


def test_requirements_does_not_pull_torch():
    """torch는 약 2.5GB입니다. faster-whisper는 onnxruntime으로 VAD를 돌립니다."""
    text = REQUIREMENTS.read_text(encoding="ascii").lower()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        assert not stripped.startswith("torch"), "torch를 직접 요구하면 안 됩니다."


def test_launcher_bat_is_cp949_without_codepage_switch():
    """요청서 3.16 — bat은 CP949로 저장하고 코드페이지 변경 명령을 넣지 않습니다."""
    raw = LAUNCHER.read_bytes()
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")  # UTF-8로 읽히면 CP949 저장이 아닙니다
    raw.decode("cp949")      # CP949로는 읽혀야 합니다
    assert b"chcp" not in raw.lower(), "bat 안에서 코드페이지를 바꾸면 창이 즉시 닫힙니다."
    assert raw.count(b"\r\n") > 10, "bat은 CRLF 줄바꿈이어야 합니다."
