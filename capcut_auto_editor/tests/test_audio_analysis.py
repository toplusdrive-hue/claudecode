"""ffmpeg 호출 방어와 무음 감지 검증 (요청서 3.14).

⚠️ 3.14 — ffmpeg은 배너만으로 stderr에 4,133바이트를 씁니다.
    윈도우 익명 파이프 기본 버퍼는 4,096바이트라, stderr를 읽지 않으면
    37바이트 차이로 파이프가 막히고 ffmpeg이 멈춥니다("진행률 2%에서 멈춤").
    이 테스트는 `-hide_banner` 강제 주입과 stderr 배수 스레드가
    실제로 동작하는지 확인합니다.
"""

import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import audio_analysis as aa

US = 1_000_000
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg이 없습니다")


@pytest.fixture
def speech_wav(tmp_path):
    """말–무음–말–무음–말 구조의 오디오를 만듭니다."""
    exe = aa.require_ffmpeg()
    path = tmp_path / "speech.wav"
    graph = (
        "sine=frequency=300:duration=2[a];"
        "anullsrc=r=16000:cl=mono:d=1.5[s1];"
        "sine=frequency=400:duration=2[b];"
        "anullsrc=r=16000:cl=mono:d=1.5[s2];"
        "sine=frequency=500:duration=2[c];"
        "[a][s1][b][s2][c]concat=n=5:v=0:a=1[out]"
    )
    subprocess.run(
        [exe, "-hide_banner", "-nostdin", "-y", "-filter_complex", graph,
         "-map", "[out]", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(path)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return path


def test_hide_banner_is_forced(speech_wav, tmp_path, monkeypatch):
    """모든 호출에 -hide_banner 가 붙는지 확인합니다."""
    captured = {}
    real_popen = subprocess.Popen

    def spy(args, *a, **kw):
        captured["args"] = list(args)
        return real_popen(args, *a, **kw)

    monkeypatch.setattr(subprocess, "Popen", spy)
    aa.extract_wav(str(speech_wav), tmp_path / "out.wav")
    assert "-hide_banner" in captured["args"]
    assert "-nostdin" in captured["args"]


def test_long_run_with_progress_does_not_deadlock(speech_wav, tmp_path):
    """stderr 배수 스레드가 없으면 여기서 멈춥니다."""
    seen = []
    done = threading.Event()

    def run():
        aa.extract_wav(
            str(speech_wav), tmp_path / "progress.wav",
            total_us=9 * US, on_progress=lambda ratio, label: seen.append(ratio),
        )
        done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=60)
    assert done.is_set(), "ffmpeg이 60초 안에 끝나지 않았습니다 (파이프 데드락 의심)"
    assert (tmp_path / "progress.wav").exists()


def test_stderr_lines_reach_callback(speech_wav):
    """silencedetect 결과는 stderr로 나옵니다. 배수 스레드가 파서로 넘겨야 합니다."""
    lines = []
    aa.run_ffmpeg(
        [aa.require_ffmpeg(), "-i", str(speech_wav),
         "-af", "silencedetect=noise=-35dB:d=0.6", "-f", "null", "-"],
        on_stderr_line=lines.append,
    )
    assert any("silence_start" in line for line in lines)


def test_detect_silence_finds_gaps(speech_wav):
    spans = aa.detect_silence(
        speech_wav, threshold_db=-35, min_duration=0.6, total_us=9 * US
    )
    assert len(spans) == 2
    # 2.0~3.5초 / 5.5~7.0초 부근
    assert 1.8 * US < spans[0].start_us < 2.2 * US
    assert 3.3 * US < spans[0].end_us < 3.7 * US
    assert 5.3 * US < spans[1].start_us < 5.7 * US


def test_cancel_kills_process(speech_wav, tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(aa.CancelledError):
        aa.extract_wav(
            str(speech_wav), tmp_path / "cancelled.wav",
            total_us=9 * US, on_progress=lambda *_: None, cancel=cancel,
        )


def test_concat_wavs_joins_in_order(speech_wav, tmp_path):
    part = tmp_path / "part.wav"
    aa.cut_audio_chunk(speech_wav, part, 0.0, 1.0)
    merged = tmp_path / "merged.wav"
    aa.concat_wavs([part, part], merged)
    assert merged.exists()
    assert merged.stat().st_size > part.stat().st_size * 1.8


def test_missing_ffprobe_gives_install_guidance(monkeypatch):
    monkeypatch.setattr(aa, "ffprobe_path", lambda: None)
    with pytest.raises(aa.MediaToolMissing, match="winget install Gyan.FFmpeg"):
        aa.require_ffprobe()
