"""API 스모크 테스트 — 서버가 뜨고 주요 엔드포인트가 응답하는지."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app

client = TestClient(app)


def test_health():
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_index_serves_html():
    res = client.get("/")
    assert res.status_code == 200
    assert "캡컷 자동 편집기" in res.text


def test_setup_status_reports_environment():
    res = client.get("/api/setup/status")
    assert res.status_code == 200
    data = res.json()
    assert data["pycapcut"]["verified"] == "0.0.3"
    assert data["capcut"]["verified_version"] == "9.3.0.3969"
    assert "whisper" in data
    assert isinstance(data["blocks"], list)


def test_session_lifecycle():
    created = client.post("/api/sessions", json={"title": "테스트 세션"}).json()
    sid = created["id"]
    try:
        read = client.get(f"/api/sessions/{sid}").json()
        assert read["session"]["title"] == "테스트 세션"
        step_ids = [s["id"] for s in read["steps"]]
        assert step_ids == ["stage0", "calibration", "stage1", "stage2", "stage3", "stage4", "stage5"]

        # 0차를 끝내기 전에는 1차가 잠겨 있고 이유가 표시됩니다
        stage1 = next(s for s in read["steps"] if s["id"] == "stage1")
        assert stage1["locked"] is True
        assert "0차 프로젝트 준비" in stage1["lock_reason"]

        renamed = client.patch(f"/api/sessions/{sid}", json={"title": "이름 변경"}).json()
        assert renamed["title"] == "이름 변경"

        listed = client.get("/api/sessions").json()
        assert any(s["id"] == sid for s in listed["sessions"])
    finally:
        assert client.delete(f"/api/sessions/{sid}").json()["ok"] is True


def test_missing_session_returns_korean_message():
    res = client.get("/api/sessions/없는세션")
    assert res.status_code == 404
    assert "찾을 수 없습니다" in str(res.json()["detail"])


def test_sources_reject_missing_files():
    created = client.post("/api/sessions", json={}).json()
    sid = created["id"]
    try:
        res = client.post(f"/api/sessions/{sid}/sources", json={"paths": ["C:/없는파일.mp4"]})
        assert res.status_code == 200
        body = res.json()
        assert body["problems"]
        assert "파일이 없습니다" in body["problems"][0]["message"]
        # 소스가 없으므로 0차가 완료되지 않습니다
        assert not any(s["id"] == "stage0" and s["done"] for s in body["steps"])
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_editing_requires_sources():
    created = client.post("/api/sessions", json={}).json()
    sid = created["id"]
    try:
        res = client.post(f"/api/editing/{sid}/analyze", json={})
        assert res.status_code == 400
        assert "0차" in str(res.json()["detail"])
    finally:
        client.delete(f"/api/sessions/{sid}")


def test_transitions_all_returns_full_enum():
    data = client.get("/api/calibration/transitions/all").json()
    assert data["count"] == 1137  # 실측: pycapcut 0.0.3의 TransitionType 멤버 수
    assert "중국어" in data["notice"]


def test_file_browse_and_diagnose(tmp_path):
    (tmp_path / "sub").mkdir()
    res = client.get("/api/files/browse", params={"path": str(tmp_path), "kind": "video"})
    assert res.status_code == 200
    assert any(d["name"] == "sub" for d in res.json()["dirs"])

    diag = client.post("/api/files/diagnose", json={"path": str(tmp_path / "sub" / "없는폴더" / "x.mp4")}).json()
    assert diag["ok"] is False
    assert diag["valid_prefix"] == str(tmp_path / "sub")
    assert "까지는 맞는 경로입니다" in diag["message"]


def test_quoted_path_is_cleaned(tmp_path):
    quoted = f'"{tmp_path}"'
    res = client.post("/api/files/diagnose", json={"path": quoted}).json()
    assert res["ok"] is True


def test_fillers_and_glossary_roundtrip():
    original = client.get("/api/setup/fillers").json()["words"]
    try:
        updated = client.post("/api/setup/fillers", json={"words": ["어", "음", "어"]}).json()
        assert updated["words"] == ["어", "음"]  # 중복 제거
    finally:
        client.post("/api/setup/fillers", json={"words": original})

    glossary = client.get("/api/setup/glossary").json()
    assert any(e["term"] == "CargoWise" for e in glossary["물류"])
    assert any(e["term"] == "MCP" for e in glossary["AI"])


def test_capcut_running_endpoint():
    data = client.get("/api/setup/capcut-running").json()
    assert "running" in data and "message" in data


# ── ffmpeg 경로 직접 지정 ────────────────────────────────────────────────
# 0차 배너가 "아래에서 경로를 직접 지정해 주세요" 라고 안내하므로,
# 그 수단이 실제로 있어야 합니다.
def _restore_settings(saved):
    from app import config

    config.save_settings({"ffmpeg": saved.get("ffmpeg", ""), "ffprobe": saved.get("ffprobe", "")})


def test_media_tools_accepts_folder_with_both_binaries(tmp_path):
    from app import config

    saved = config.load_settings()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("ffmpeg", "ffprobe"):
        target = bin_dir / name
        target.write_text("#!/bin/sh\n")
        target.chmod(0o755)
    try:
        res = client.post("/api/setup/media-tools", json={"path": str(bin_dir)})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["ok"] is True
        assert body["ffmpeg"].endswith("ffmpeg")
        assert body["ffprobe"].endswith("ffprobe")
    finally:
        _restore_settings(saved)


def test_media_tools_reports_which_binary_is_missing(tmp_path):
    from app import config

    saved = config.load_settings()
    bin_dir = tmp_path / "half"
    bin_dir.mkdir()
    target = bin_dir / "ffmpeg"
    target.write_text("#!/bin/sh\n")
    target.chmod(0o755)
    try:
        res = client.post("/api/setup/media-tools", json={"path": str(bin_dir)})
        assert res.status_code == 400
        message = res.json()["detail"]["message"]
        assert "ffprobe" in message
        assert "같은 폴더" in message
        assert "**" not in message  # 배너는 마크다운을 해석하지 않습니다
        # 실패했으면 설정을 더럽히지 않아야 합니다
        assert config.load_settings()["ffmpeg"] == ""
    finally:
        _restore_settings(saved)


def test_media_tools_rejects_missing_path():
    res = client.post("/api/setup/media-tools", json={"path": "C:/없는폴더/bin"})
    assert res.status_code == 400
    assert "경로가 없습니다" in res.json()["detail"]["message"]


def test_media_tools_rejects_empty_path():
    res = client.post("/api/setup/media-tools", json={"path": "  "})
    assert res.status_code == 400
    assert "경로를 입력해" in res.json()["detail"]["message"]
