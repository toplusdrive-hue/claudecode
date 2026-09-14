"""ffprobe / 오디오 추출 / 무음 감지.

⚠️ 요청서 3.14 — ffmpeg stderr 파이프 데드락
    ffmpeg은 배너만으로 stderr에 4KB 넘게 씁니다. 윈도우 익명 파이프 기본 버퍼는
    4096 바이트라, stderr를 읽지 않으면 ffmpeg이 쓰기에서 블록되어 멈춥니다.
    ("진행률 2%에서 멈춤")
    → 모든 호출에 `-hide_banner`를 붙이고, **동시에** stderr를 별도 스레드로
      계속 비웁니다. 둘 중 하나만으로는 스트림이 많은 파일에서 다시 막힐 수 있습니다.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence

from ..config import ffmpeg_path, ffprobe_path
from .logging_util import get_logger

log = get_logger(__name__)

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class MediaToolMissing(RuntimeError):
    pass


def require_ffmpeg() -> str:
    path = ffmpeg_path()
    if not path:
        raise MediaToolMissing(
            "ffmpeg을 찾을 수 없습니다. `winget install Gyan.FFmpeg` 로 설치한 뒤 "
            "터미널을 새로 열거나, 설정에서 ffmpeg.exe 경로를 직접 지정해 주세요."
        )
    return path


def require_ffprobe() -> str:
    path = ffprobe_path()
    if not path:
        raise MediaToolMissing(
            "ffprobe를 찾을 수 없습니다. ffmpeg과 같은 폴더에 있습니다. "
            "`winget install Gyan.FFmpeg` 로 설치하거나 설정에서 경로를 지정해 주세요."
        )
    return path


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str


def run_quick(args: Sequence[str], timeout: float = 120.0) -> ProcResult:
    """짧게 끝나는 명령. subprocess.run이 두 파이프를 함께 비우므로 안전합니다."""
    proc = subprocess.run(
        list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        creationflags=_CREATE_NO_WINDOW,
    )
    return ProcResult(
        proc.returncode,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace"),
    )


class CancelledError(RuntimeError):
    pass


def run_ffmpeg(
    args: Sequence[str],
    *,
    total_us: Optional[int] = None,
    on_progress: Optional[Callable[[float, str], None]] = None,
    on_stderr_line: Optional[Callable[[str], None]] = None,
    cancel: Optional[threading.Event] = None,
    tail_lines: int = 80,
) -> ProcResult:
    """긴 ffmpeg 작업 실행.

    - `-hide_banner`, `-nostdin`, `-loglevel` 을 강제로 붙입니다.
    - stderr는 **전용 스레드가 끊임없이 읽어** 파이프가 차지 않게 합니다.
    - `total_us`를 주면 `-progress pipe:1` 로 진행률을 보고합니다.
    """
    argv = list(args)
    exe = argv[0]
    flags = ["-hide_banner", "-nostdin", "-y"]
    for flag in reversed(flags):
        if flag not in argv:
            argv.insert(1, flag)

    if total_us is not None and "-progress" not in argv:
        argv += ["-progress", "pipe:1", "-nostats"]

    log.info("ffmpeg 실행: %s", " ".join(Path(a).name if a == exe else a for a in argv))

    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        bufsize=1,
        universal_newlines=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATE_NO_WINDOW,
    )

    stderr_tail: Deque[str] = deque(maxlen=tail_lines)

    def _drain_stderr() -> None:
        # ⚠️ 이 스레드가 없으면 ffmpeg이 stderr 쓰기에서 영원히 멈춥니다 (3.14).
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.rstrip("\r\n")
            if not line:
                continue
            stderr_tail.append(line)
            if on_stderr_line is not None:
                try:
                    on_stderr_line(line)
                except Exception:  # 콜백 실패가 배수를 멈추면 안 됩니다
                    log.exception("stderr 콜백에서 예외")

    drainer = threading.Thread(target=_drain_stderr, name="ffmpeg-stderr", daemon=True)
    drainer.start()

    cancelled = False
    current_label = ""
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                cancelled = True
                break
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key == "out_time_us" and total_us and on_progress is not None:
                try:
                    done = max(0, int(value))
                except ValueError:
                    continue
                on_progress(min(1.0, done / total_us), current_label)
            elif key == "out_time":
                current_label = value
    finally:
        if cancelled:
            _kill_tree(proc)
        proc.wait()
        drainer.join(timeout=3)

    if cancelled:
        raise CancelledError("사용자가 작업을 취소했습니다.")

    stderr_text = "\n".join(stderr_tail)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg이 실패했습니다 (코드 {proc.returncode}).\n{stderr_text[-1500:]}")
    return ProcResult(proc.returncode, "", stderr_text)


def _kill_tree(proc: subprocess.Popen) -> None:
    """하위 프로세스까지 확실히 종료합니다 (요청서 7절 — 취소가 실제로 동작하게)."""
    try:
        import psutil

        parent = psutil.Process(proc.pid)
        for child in parent.children(recursive=True):
            try:
                child.kill()
            except Exception:
                pass
        parent.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ── ffprobe ──────────────────────────────────────────────────────────────────
@dataclass
class MediaInfo:
    path: str
    name: str
    duration_us: int
    width: int
    height: int
    fps: float
    has_audio: bool
    audio_tracks: int
    video_codec: str
    audio_codec: str
    size_bytes: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "duration_us": self.duration_us,
            "duration_sec": round(self.duration_us / 1_000_000, 3),
            "width": self.width,
            "height": self.height,
            "fps": round(self.fps, 3),
            "has_audio": self.has_audio,
            "audio_tracks": self.audio_tracks,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "size_bytes": self.size_bytes,
        }


def _parse_fps(raw: str) -> float:
    if not raw or raw in ("0/0", "N/A"):
        return 0.0
    if "/" in raw:
        num, _, den = raw.partition("/")
        try:
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        except ValueError:
            return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def probe(path: str) -> MediaInfo:
    exe = require_ffprobe()
    result = run_quick(
        [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]
    )
    if result.returncode != 0:
        raise RuntimeError(f"'{Path(path).name}' 의 정보를 읽지 못했습니다.\n{result.stderr[-800:]}")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audios = [s for s in streams if s.get("codec_type") == "audio"]

    duration_sec = 0.0
    for source in (fmt.get("duration"), video.get("duration") if video else None):
        if source:
            try:
                duration_sec = float(source)
                break
            except (TypeError, ValueError):
                continue

    return MediaInfo(
        path=str(path),
        name=Path(path).name,
        duration_us=int(round(duration_sec * 1_000_000)),
        width=int(video.get("width", 0)) if video else 0,
        height=int(video.get("height", 0)) if video else 0,
        fps=_parse_fps(video.get("avg_frame_rate") or video.get("r_frame_rate") or "") if video else 0.0,
        has_audio=bool(audios),
        audio_tracks=len(audios),
        video_codec=(video or {}).get("codec_name", ""),
        audio_codec=(audios[0].get("codec_name", "") if audios else ""),
        size_bytes=int(fmt.get("size", 0) or 0),
    )


# ── 오디오 추출 ──────────────────────────────────────────────────────────────
def extract_wav(
    source: str,
    dest: Path,
    *,
    total_us: Optional[int] = None,
    on_progress: Optional[Callable[[float, str], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> Path:
    """16kHz 모노 wav로 추출합니다."""
    exe = require_ffmpeg()
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [exe, "-i", source, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dest)],
        total_us=total_us,
        on_progress=on_progress,
        cancel=cancel,
    )
    if not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError(f"'{Path(source).name}' 에서 오디오를 뽑지 못했습니다. 오디오 트랙이 있는지 확인해 주세요.")
    return dest


def concat_wavs(parts: List[Path], dest: Path, *, cancel: Optional[threading.Event] = None) -> Path:
    """여러 wav를 순서대로 이어붙입니다 (소스 타임라인용)."""
    if len(parts) == 1:
        if parts[0] != dest:
            dest.write_bytes(parts[0].read_bytes())
        return dest
    exe = require_ffmpeg()
    listing = dest.with_suffix(".concat.txt")
    with listing.open("w", encoding="utf-8") as f:
        for part in parts:
            escaped = str(part).replace("\\", "/").replace("'", r"'\''")
            f.write(f"file '{escaped}'\n")
    run_ffmpeg(
        [exe, "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(dest)],
        cancel=cancel,
    )
    listing.unlink(missing_ok=True)
    return dest


def cut_audio_chunk(source: Path, dest: Path, start_sec: float, duration_sec: float) -> Path:
    exe = require_ffmpeg()
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            exe, "-ss", f"{start_sec:.3f}", "-t", f"{duration_sec:.3f}",
            "-i", str(source), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dest),
        ]
    )
    return dest


# ── 무음 감지 ────────────────────────────────────────────────────────────────
@dataclass
class SilenceSpan:
    start_us: int
    end_us: int

    @property
    def duration_us(self) -> int:
        return self.end_us - self.start_us


_RE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_RE_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_silence(
    wav_path: Path,
    *,
    threshold_db: float,
    min_duration: float,
    total_us: int,
    on_progress: Optional[Callable[[float, str], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> List[SilenceSpan]:
    """ffmpeg silencedetect 결과를 파싱합니다.

    silencedetect는 결과를 **stderr로** 냅니다. 배수 스레드가 그 줄을 파서에
    그대로 넘깁니다 (파이프를 비우면서 동시에 읽는 구조).
    """
    exe = require_ffmpeg()
    spans: List[SilenceSpan] = []
    pending_start: Optional[float] = None

    def _on_line(line: str) -> None:
        nonlocal pending_start
        if "silence_start" in line:
            m = _RE_START.search(line)
            if m:
                pending_start = max(0.0, float(m.group(1)))
        elif "silence_end" in line:
            m = _RE_END.search(line)
            if m and pending_start is not None:
                end = float(m.group(1))
                start_us = int(round(pending_start * 1_000_000))
                end_us = int(round(end * 1_000_000))
                if end_us > start_us:
                    spans.append(SilenceSpan(start_us, end_us))
                pending_start = None

    run_ffmpeg(
        [
            exe, "-i", str(wav_path),
            "-af", f"silencedetect=noise={threshold_db}dB:d={min_duration}",
            "-f", "null", "-",
        ],
        total_us=total_us,
        on_progress=on_progress,
        on_stderr_line=_on_line,
        cancel=cancel,
    )

    # 파일 끝까지 무음이면 silence_end가 나오지 않습니다.
    if pending_start is not None:
        start_us = int(round(pending_start * 1_000_000))
        if total_us > start_us:
            spans.append(SilenceSpan(start_us, total_us))

    spans.sort(key=lambda s: s.start_us)
    log.info("무음 구간 %d개 감지 (임계 %.1fdB, 최소 %.2fs)", len(spans), threshold_db, min_duration)
    return spans
