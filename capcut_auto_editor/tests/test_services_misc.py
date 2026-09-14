"""세션 잠금 · 작업 중복 방지 · UTF-8 방어 · 프롬프트 조립 검증."""

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import native_picker, prompt_builder
from app.services.jobs import JobManager
from app.services.script_align import Subtitle

US = 1_000_000


# ── 요청서 7절: 중복 작업 방지 ───────────────────────────────────────────
def test_duplicate_submit_reuses_running_job():
    """버튼이 멈춘 것처럼 보이면 사용자는 두 번 누릅니다."""
    manager = JobManager()
    gate = threading.Event()

    def slow(ctx):
        gate.wait(5)
        return {"label": "끝"}

    first = manager.submit(session_id="s1", kind="analyze", title="분석", target=slow)
    second = manager.submit(session_id="s1", kind="analyze", title="분석", target=slow)
    assert first.id == second.id  # 새 작업을 만들지 않고 다시 연결합니다

    gate.set()
    for _ in range(100):
        if first.status != "running":
            break
        time.sleep(0.05)
    assert first.status == "done"

    # 끝난 뒤에는 새 작업이 만들어집니다
    third = manager.submit(session_id="s1", kind="analyze", title="분석", target=lambda ctx: {"label": "x"})
    assert third.id != first.id


def test_different_kinds_run_concurrently():
    manager = JobManager()
    a = manager.submit(session_id="s1", kind="analyze", title="A", target=lambda c: {"label": "a"})
    b = manager.submit(session_id="s1", kind="build_draft", title="B", target=lambda c: {"label": "b"})
    assert a.id != b.id


def test_cancel_marks_job_cancelled():
    manager = JobManager()
    started = threading.Event()

    def waiter(ctx):
        started.set()
        for _ in range(200):
            ctx.raise_if_cancelled()
            time.sleep(0.02)
        return {"label": "끝"}

    job = manager.submit(session_id="s1", kind="analyze", title="분석", target=waiter)
    started.wait(3)
    assert manager.cancel(job.id) is True
    for _ in range(150):
        if job.status != "running":
            break
        time.sleep(0.02)
    assert job.status == "cancelled"
    assert job.error is None


def test_failed_job_keeps_korean_message():
    manager = JobManager()

    def boom(ctx):
        raise RuntimeError("캡컷이 실행 중이라 진행할 수 없습니다.")

    job = manager.submit(session_id="s1", kind="x", title="x", target=boom)
    for _ in range(100):
        if job.status != "running":
            break
        time.sleep(0.02)
    assert job.status == "failed"
    assert "캡컷이 실행 중" in job.error


def test_job_warnings_are_deduplicated():
    manager = JobManager()
    captured = {}

    def target(ctx):
        ctx.warn("같은 경고")
        ctx.warn("같은 경고")
        captured["warnings"] = list(ctx.job.warnings)
        return {"label": "ok"}

    job = manager.submit(session_id="s1", kind="x", title="x", target=target)
    for _ in range(100):
        if job.status != "running":
            break
        time.sleep(0.02)
    assert captured["warnings"] == ["같은 경고"]


def test_stage_progress_maps_into_subrange():
    manager = JobManager()
    seen = []

    def target(ctx):
        report = ctx.stage(0.4, 0.2)
        report(0.0, "시작")
        report(0.5, "절반")
        report(1.0, "끝")
        seen.append(ctx.job.progress)
        return {"label": "ok"}

    job = manager.submit(session_id="s1", kind="x", title="x", target=target)
    for _ in range(100):
        if job.status != "running":
            break
        time.sleep(0.02)
    assert seen[0] == pytest.approx(0.6)


# ── 요청서 3.15: UTF-8 방어 ──────────────────────────────────────────────
def test_broken_korean_path_is_reported_clearly(monkeypatch):
    """cp949로 넘어온 한글 경로에 U+FFFD가 섞이면 명확히 알려야 합니다."""
    import json
    import subprocess

    class FakeProc:
        returncode = 0
        stdout = json.dumps(
            {"ok": True, "paths": ["C:\\Users\\u\\\ufffd\ufffd\ufffd\ufffd\\video.mp4"]}
        ).encode("utf-8")
        stderr = b""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeProc())
    result = native_picker.pick()
    assert result["ok"] is False
    assert "한글이 깨졌습니다" in result["error"]
    assert result["paths"] == []


def test_picker_sets_utf8_env(monkeypatch, tmp_path):
    import json
    import subprocess

    target = tmp_path / "a.mp4"
    target.write_bytes(b"x")
    captured = {}

    class FakeProc:
        returncode = 0
        stdout = json.dumps({"ok": True, "paths": [str(target)]}).encode("utf-8")
        stderr = b""

    def spy(args, **kw):
        captured["env"] = kw.get("env", {})
        return FakeProc()

    monkeypatch.setattr(subprocess, "run", spy)
    result = native_picker.pick()
    assert result["ok"] is True
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    assert captured["env"]["PYTHONUTF8"] == "1"


def test_picker_timeout_is_friendly(monkeypatch):
    import subprocess

    def boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="x", timeout=180)

    monkeypatch.setattr(subprocess, "run", boom)
    result = native_picker.pick(timeout=180)
    assert result["ok"] is False
    assert "찾아보기" in result["error"]


# ── 프롬프트 조립 ────────────────────────────────────────────────────────
def _subs():
    return [
        Subtitle(1, 0, 3 * US, "오늘은 B/L 발행 절차를 정리합니다"),
        Subtitle(2, 65 * US, 68 * US, "Demurrage가 붙는 시점을 확인하세요"),
    ]


def test_marketing_prompt_includes_everything_required():
    text = prompt_builder.build_marketing_prompt(
        answers={"takeaway": "핵심", "situation": "상황", "tool_name": "CargoWise"},
        subtitles=_subs(),
        video_duration_us=600 * US,
    )
    assert "유튜브 제목 2안" in text
    assert "20자 내외" in text
    assert "타임스탬프 목차" in text
    assert "해시태그 5개" in text
    # 사람이 잘 쓰지 않는 표기 금지 지시
    assert "em dash" in text
    assert "별표(*)로 강조하지 마십시오" in text
    # 채널/타겟/톤
    assert "타겟 시청자" in text
    # 자막 전문이 타임스탬프와 함께 들어갑니다
    assert "[1:05] Demurrage가 붙는 시점을 확인하세요" in text


def test_reels_prompt_requires_profile_link_cta():
    reels = prompt_builder.build_vertical_text_prompt(
        platform="reels", subtitles=_subs(), answers={"takeaway": "핵심"}, clip_duration_us=25 * US
    )
    assert "프로필 링크" in reels
    assert "릴스" in reels

    shorts = prompt_builder.build_vertical_text_prompt(
        platform="shorts", subtitles=_subs(), answers={"takeaway": "핵심"}, clip_duration_us=25 * US
    )
    assert "프로필 링크" not in shorts
    assert "18자 이내" in shorts and "24자 이내" in shorts


def test_prompt_saved_to_txt(tmp_path, monkeypatch):
    monkeypatch.setattr(prompt_builder, "WORK_DIR", tmp_path, raising=False)
    from app import config

    monkeypatch.setattr(config, "WORK_DIR", tmp_path)
    path = prompt_builder.save_prompt("sess1", "마케팅문구", "내용")
    assert Path(path).read_text(encoding="utf-8") == "내용"
