from pathlib import Path

import pytest

from tgm_audio import ffmpeg


def tone(path: Path, seconds: float, src: str) -> Path:
    ffmpeg.run(["-f", "lavfi", "-i", f"{src}:duration={seconds}", "-ac", "2", "-ar", "48000",
                str(path)])  # fmt: skip
    return path


@pytest.fixture
def voice(tmp_path: Path) -> Path:
    """Speech stand-in: a 3 s tone that starts 1 s in, inside 6 s."""
    t = tone(tmp_path / "t.wav", 3, "sine=frequency=300")
    out = tmp_path / "voice.wav"
    ffmpeg.run(["-i", str(t), "-af", "adelay=1000:all=1,apad=whole_dur=6", str(out)])
    return out


@pytest.fixture
def music(tmp_path: Path) -> Path:
    return tone(tmp_path / "music.wav", 4, "anoisesrc=color=pink:amplitude=0.5")
