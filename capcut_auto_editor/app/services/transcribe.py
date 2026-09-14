"""faster-whisper 전사 — CPU 전제, 청크 분할 + 재시작 가능.

요청서 3.19:
  - 기본 모델은 large-v3가 아니라 **medium**
  - 첫 실행 시 모델을 내려받습니다 (medium ≈ 1.5GB). 아무 안내 없이
    "모델 로딩 중"에서 몇 분 멈춘 것처럼 보이므로 **미리 알리고 진행량을 표시**합니다.
  - huggingface_hub가 hf_xet 백엔드를 쓰면 **staging 폴더에 먼저 받고 마지막에 옮깁니다.**
    모델 폴더 크기만 재면 끝날 때까지 0으로 보이므로 `~/.cache/huggingface/xet`도 함께 셉니다.
  - 긴 영상은 청크로 나눠 처리하고 청크별 결과를 디스크에 남겨 재시작 가능하게 합니다.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..config import WORK_DIR, load_settings
from .audio_analysis import CancelledError, SilenceSpan, cut_audio_chunk
from .logging_util import get_logger

log = get_logger(__name__)

US = 1_000_000

# 모델별 대략적인 다운로드 크기 (진행률 표시용)
MODEL_SIZES_MB = {
    "tiny": 75, "base": 145, "small": 490, "medium": 1530,
    "large-v2": 3090, "large-v3": 3090, "distil-large-v3": 1520,
}

DEFAULT_CHUNK_SEC = 600.0   # 10분
MAX_CHUNK_SEC = 900.0


def hf_cache_dirs() -> List[Path]:
    base = Path.home() / ".cache" / "huggingface"
    return [base / "hub", base / "xet"]


def _dir_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total / (1024 * 1024)


def model_is_cached(model_name: str) -> bool:
    hub = Path.home() / ".cache" / "huggingface" / "hub"
    if not hub.exists():
        return False
    needle = model_name.replace("/", "--").lower()
    try:
        for child in hub.iterdir():
            name = child.name.lower()
            if child.is_dir() and needle in name and any(child.rglob("*.bin")):
                return True
            if child.is_dir() and needle in name and any(child.rglob("model.bin")):
                return True
    except OSError:
        return False
    return False


def download_estimate_mb(model_name: str) -> int:
    return MODEL_SIZES_MB.get(model_name, 1500)


class ModelDownloadWatcher:
    """모델 다운로드 진행량을 hub + xet staging 폴더 크기로 추정해 보고합니다."""

    def __init__(self, model_name: str, on_progress: Callable[[float, str], None]):
        self.model_name = model_name
        self.on_progress = on_progress
        self.target_mb = float(download_estimate_mb(model_name))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._baseline = 0.0

    def __enter__(self) -> "ModelDownloadWatcher":
        self._baseline = sum(_dir_size_mb(p) for p in hf_cache_dirs())
        self._thread = threading.Thread(target=self._run, name="hf-download-watch", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.wait(1.5):
            grown = max(0.0, sum(_dir_size_mb(p) for p in hf_cache_dirs()) - self._baseline)
            ratio = min(0.99, grown / self.target_mb) if self.target_mb else 0.0
            self.on_progress(
                ratio,
                f"모델 내려받는 중 {grown:.0f}MB / 약 {self.target_mb:.0f}MB",
            )


@dataclass
class Chunk:
    index: int
    start_us: int
    end_us: int

    @property
    def duration_sec(self) -> float:
        return (self.end_us - self.start_us) / US


def plan_chunks(
    total_us: int,
    silences: Sequence[SilenceSpan] = (),
    *,
    chunk_sec: float = DEFAULT_CHUNK_SEC,
) -> List[Chunk]:
    """가능하면 **무음 한가운데**에서 끊어 말이 잘리지 않게 청크를 나눕니다."""
    if total_us <= chunk_sec * US:
        return [Chunk(0, 0, total_us)]

    boundaries: List[int] = [0]
    target = int(chunk_sec * US)
    max_len = int(MAX_CHUNK_SEC * US)
    cursor = 0

    while total_us - cursor > max_len:
        ideal = cursor + target
        best: Optional[int] = None
        best_dist = None
        for span in silences:
            mid = (span.start_us + span.end_us) // 2
            if mid <= cursor + target // 4:
                continue
            if mid > cursor + max_len:
                break
            dist = abs(mid - ideal)
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best = mid
        cut_at = best if best is not None else min(ideal, cursor + max_len)
        boundaries.append(cut_at)
        cursor = cut_at

    boundaries.append(total_us)
    return [
        Chunk(i, boundaries[i], boundaries[i + 1])
        for i in range(len(boundaries) - 1)
        if boundaries[i + 1] > boundaries[i]
    ]


def _chunk_cache_path(session_id: str, chunk: Chunk) -> Path:
    folder = WORK_DIR / session_id / "stt"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"chunk_{chunk.index:03d}_{chunk.start_us}_{chunk.end_us}.json"


def transcribe(
    wav_path: Path,
    *,
    session_id: str,
    total_us: int,
    silences: Sequence[SilenceSpan] = (),
    on_progress: Optional[Callable[[float, str], None]] = None,
    cancel: Optional[threading.Event] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """전사 실행. 청크별 결과를 디스크에 남기므로 중단해도 이어서 할 수 있습니다.

    반환: {"segments": [...], "words": [...], "language": ..., "model": ...}
    시각은 전부 **소스 타임라인 기준 마이크로초**입니다.
    """
    settings = load_settings()
    model_name = settings.get("whisper_model", "medium")
    compute_type = settings.get("whisper_compute_type", "int8")
    language = settings.get("whisper_language", "ko") or None

    def report(ratio: float, label: str) -> None:
        if on_progress is not None:
            on_progress(max(0.0, min(1.0, ratio)), label)

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - 설치 안내
        raise RuntimeError(
            "faster-whisper가 설치되어 있지 않습니다. "
            "`pip install faster-whisper==1.2.1` 을 실행한 뒤 다시 시도해 주세요."
        ) from exc

    cached = model_is_cached(model_name)
    if not cached:
        report(
            0.0,
            f"'{model_name}' 모델을 처음 내려받습니다 (약 {download_estimate_mb(model_name)}MB). "
            "네트워크 속도에 따라 수 분 걸립니다.",
        )

    load_start = time.time()
    if cached:
        report(0.0, f"'{model_name}' 모델 불러오는 중")
        model = WhisperModel(model_name, device="cpu", compute_type=compute_type)
    else:
        with ModelDownloadWatcher(model_name, lambda r, label: report(r * 0.25, label)):
            model = WhisperModel(model_name, device="cpu", compute_type=compute_type)
    log.info("모델 '%s' 준비 완료 (%.1f초)", model_name, time.time() - load_start)

    base_ratio = 0.0 if cached else 0.25
    chunks = plan_chunks(total_us, silences)
    log.info("전사 청크 %d개 (총 %.1f분)", len(chunks), total_us / US / 60)

    segments: List[Dict[str, Any]] = []
    words: List[Dict[str, Any]] = []
    chunk_dir = WORK_DIR / session_id / "stt"
    chunk_dir.mkdir(parents=True, exist_ok=True)

    for chunk in chunks:
        if cancel is not None and cancel.is_set():
            raise CancelledError("사용자가 전사를 취소했습니다.")

        cache_path = _chunk_cache_path(session_id, chunk)
        if cache_path.exists() and not force:
            try:
                cached_payload = json.loads(cache_path.read_text(encoding="utf-8"))
                segments.extend(cached_payload["segments"])
                words.extend(cached_payload["words"])
                log.info("청크 %d/%d 은 이전 결과를 재사용합니다.", chunk.index + 1, len(chunks))
                report(
                    base_ratio + (1 - base_ratio) * (chunk.index + 1) / len(chunks),
                    f"{chunk.index + 1}/{len(chunks)} 구간 (이전 결과 재사용)",
                )
                continue
            except (OSError, json.JSONDecodeError, KeyError):
                log.warning("청크 %d 캐시가 손상되어 다시 전사합니다.", chunk.index)

        report(
            base_ratio + (1 - base_ratio) * chunk.index / len(chunks),
            f"{chunk.index + 1}/{len(chunks)} 구간 전사 중 "
            f"({chunk.start_us / US / 60:.1f}분~{chunk.end_us / US / 60:.1f}분)",
        )

        chunk_wav = chunk_dir / f"chunk_{chunk.index:03d}.wav"
        cut_audio_chunk(wav_path, chunk_wav, chunk.start_us / US, chunk.duration_sec)

        chunk_segments: List[Dict[str, Any]] = []
        chunk_words: List[Dict[str, Any]] = []
        try:
            result, _info = model.transcribe(
                str(chunk_wav),
                language=language,
                word_timestamps=True,
                vad_filter=True,  # Silero VAD (onnxruntime 내장, torch 불필요)
                vad_parameters={"min_silence_duration_ms": 400},
                beam_size=5,
            )
            for segment in result:
                if cancel is not None and cancel.is_set():
                    raise CancelledError("사용자가 전사를 취소했습니다.")
                seg_words: List[Dict[str, Any]] = []
                for word in getattr(segment, "words", None) or []:
                    seg_words.append(
                        {
                            "text": (word.word or "").strip(),
                            "start_us": chunk.start_us + int(round(word.start * US)),
                            "end_us": chunk.start_us + int(round(word.end * US)),
                            "probability": float(getattr(word, "probability", 1.0) or 1.0),
                        }
                    )
                text = (segment.text or "").strip()
                if not text:
                    continue
                chunk_segments.append(
                    {
                        "text": text,
                        "start_us": chunk.start_us + int(round(segment.start * US)),
                        "end_us": chunk.start_us + int(round(segment.end * US)),
                        "confidence": _confidence(segment),
                        "words": seg_words,
                    }
                )
                chunk_words.extend(seg_words)
        finally:
            chunk_wav.unlink(missing_ok=True)

        cache_path.write_text(
            json.dumps({"segments": chunk_segments, "words": chunk_words}, ensure_ascii=False),
            encoding="utf-8",
        )
        segments.extend(chunk_segments)
        words.extend(chunk_words)
        report(
            base_ratio + (1 - base_ratio) * (chunk.index + 1) / len(chunks),
            f"{chunk.index + 1}/{len(chunks)} 구간 완료 (누적 {len(segments)}문장)",
        )

    segments.sort(key=lambda s: s["start_us"])
    words.sort(key=lambda w: w["start_us"])
    return {
        "segments": segments,
        "words": words,
        "language": language or "auto",
        "model": model_name,
        "chunk_count": len(chunks),
    }


def _confidence(segment: Any) -> float:
    prob = getattr(segment, "avg_logprob", None)
    if prob is None:
        return 1.0
    try:
        import math

        return max(0.0, min(1.0, math.exp(float(prob))))
    except Exception:
        return 1.0


def clear_cache(session_id: str) -> int:
    folder = WORK_DIR / session_id / "stt"
    if not folder.exists():
        return 0
    count = 0
    for item in folder.glob("chunk_*.json"):
        item.unlink(missing_ok=True)
        count += 1
    return count
