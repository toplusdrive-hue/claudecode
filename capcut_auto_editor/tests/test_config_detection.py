"""ffmpeg / 드래프트 폴더 탐지 검증.

⚠️ 실제로 겪은 문제 — gyan.dev의 zip을 풀면 한 겹이 더 생깁니다.

    C:\\ffmpeg\\ffmpeg-9.0.1-essentials_build\\bin\\ffmpeg.exe

처음에는 `C:\\ffmpeg\\bin` 만 봤기 때문에 찾지 못했습니다.
사용자가 어느 층을 지정하든 찾아야 합니다.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config


def _make_build(root: Path, *, nested: bool = True, with_ffprobe: bool = True) -> Path:
    """gyan.dev zip을 푼 모양을 흉내 냅니다."""
    bin_dir = (root / "ffmpeg-9.0.1-essentials_build" / "bin") if nested else (root / "bin")
    bin_dir.mkdir(parents=True)
    names = ["ffmpeg.exe", "ffplay.exe"] + (["ffprobe.exe"] if with_ffprobe else [])
    for name in names:
        (bin_dir / name).write_bytes(b"MZ")
    return bin_dir


@pytest.fixture
def no_path_lookup(monkeypatch):
    """PATH와 시스템 위치를 보지 않게 해서 힌트 탐색만 검사합니다."""
    monkeypatch.setattr(config.shutil, "which", lambda name: None)
    monkeypatch.setattr(config, "_local_appdata", lambda: None)
    monkeypatch.setattr(config, "_FFMPEG_ROOT_HINTS", [])
    monkeypatch.setattr(config.Path, "home", staticmethod(lambda: Path("/nonexistent-home")))


def test_finds_binary_inside_unzipped_subfolder(tmp_path, no_path_lookup):
    """사용자가 C:\\ffmpeg 를 지정해도 한 겹 아래의 bin을 찾아야 합니다."""
    _make_build(tmp_path)
    assert config._find_binary("ffmpeg", str(tmp_path)).endswith("ffmpeg.exe")
    assert config._find_binary("ffprobe", str(tmp_path)).endswith("ffprobe.exe")


def test_finds_binary_when_build_folder_given(tmp_path, no_path_lookup):
    _make_build(tmp_path)
    build = tmp_path / "ffmpeg-9.0.1-essentials_build"
    assert config._find_binary("ffmpeg", str(build)).endswith("ffmpeg.exe")


def test_finds_binary_when_bin_folder_given(tmp_path, no_path_lookup):
    bin_dir = _make_build(tmp_path)
    assert config._find_binary("ffmpeg", str(bin_dir)).endswith("ffmpeg.exe")


def test_finds_binary_when_exe_given(tmp_path, no_path_lookup):
    bin_dir = _make_build(tmp_path)
    exe = bin_dir / "ffmpeg.exe"
    assert config._find_binary("ffmpeg", str(exe)) == str(exe)


def test_finds_binary_in_flat_layout(tmp_path, no_path_lookup):
    _make_build(tmp_path, nested=False)
    assert config._find_binary("ffmpeg", str(tmp_path)).endswith("ffmpeg.exe")


def test_root_hint_is_searched_one_level_deep(tmp_path, monkeypatch):
    """설정에 아무것도 없어도 흔한 위치를 훑습니다."""
    _make_build(tmp_path)
    monkeypatch.setattr(config.shutil, "which", lambda name: None)
    monkeypatch.setattr(config, "_local_appdata", lambda: None)
    monkeypatch.setattr(config, "_FFMPEG_ROOT_HINTS", [str(tmp_path)])
    monkeypatch.setattr(config.Path, "home", staticmethod(lambda: Path("/nonexistent-home")))
    assert config._find_binary("ffmpeg", "").endswith("ffmpeg.exe")


def test_missing_ffprobe_is_reported_as_missing(tmp_path, no_path_lookup):
    _make_build(tmp_path, with_ffprobe=False)
    assert config._find_binary("ffmpeg", str(tmp_path)) is not None
    assert config._find_binary("ffprobe", str(tmp_path)) is None


def test_unrelated_folder_finds_nothing(tmp_path, no_path_lookup):
    (tmp_path / "문서").mkdir()
    assert config._find_binary("ffmpeg", str(tmp_path)) is None


def test_expand_tool_dirs_does_not_recurse_forever(tmp_path):
    deep = tmp_path
    for level in range(6):
        deep = deep / f"level{level}"
    deep.mkdir(parents=True)
    dirs = config.expand_tool_dirs(tmp_path)
    # 기본 깊이는 2단계입니다. 드라이브 전체를 훑으면 앱이 멈춥니다.
    assert all(len(d.relative_to(tmp_path).parts) <= 3 for d in dirs), [str(d) for d in dirs]


def test_expand_tool_dirs_on_missing_path():
    assert config.expand_tool_dirs(Path("/이런-경로는-없습니다")) == []


def test_configured_file_must_match_requested_name(tmp_path, no_path_lookup):
    """ffprobe 설정에 ffmpeg.exe 경로가 들어가도 엉뚱한 실행 파일을 돌려주면 안 됩니다.

    조용히 잘못된 바이너리를 쓰면 그 뒤의 오류는 원인을 알 수 없게 됩니다.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ffmpeg.exe").write_bytes(b"MZ")

    wrong = bin_dir / "ffmpeg.exe"
    assert config._find_binary("ffprobe", str(wrong)) is None
    # 이름이 맞으면 그대로 돌려줍니다
    assert config._find_binary("ffmpeg", str(wrong)) == str(wrong)


def test_configured_file_falls_back_to_its_folder(tmp_path, no_path_lookup):
    """ffmpeg.exe 를 지정했더라도 같은 폴더의 ffprobe.exe 는 찾아야 합니다."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ffmpeg.exe").write_bytes(b"MZ")
    (bin_dir / "ffprobe.exe").write_bytes(b"MZ")

    found = config._find_binary("ffprobe", str(bin_dir / "ffmpeg.exe"))
    assert found is not None and found.endswith("ffprobe.exe")
