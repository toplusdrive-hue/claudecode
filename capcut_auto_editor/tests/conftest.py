import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MEDIA_DIR = Path(__file__).resolve().parent / "media"


def _ffmpeg() -> str:
    from shutil import which

    found = which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pytest.skip("ffmpeg이 없어 영상이 필요한 테스트를 건너뜁니다.")


@pytest.fixture(scope="session")
def clips() -> list[Path]:
    """테스트용 영상 2개 (1920x1080 / 30fps). 없으면 만들어 둡니다."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    specs = [("clip_a.mp4", "testsrc", 6, 440), ("clip_b.mp4", "testsrc2", 4, 660)]
    paths: list[Path] = []
    exe = None
    for name, source, duration, freq in specs:
        path = MEDIA_DIR / name
        if not path.exists():
            exe = exe or _ffmpeg()
            subprocess.run(
                [
                    exe, "-hide_banner", "-nostdin", "-y",
                    "-f", "lavfi", "-i", f"{source}=size=1920x1080:rate=30:duration={duration}",
                    "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
                ],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        paths.append(path)
    return paths
