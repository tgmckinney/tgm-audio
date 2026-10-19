"""Bundled static ffmpeg/ffprobe (no Homebrew on the work laptop) and thin wrappers."""

import json
import os
import subprocess
from functools import cache
from pathlib import Path

import static_ffmpeg.run


@cache
def env() -> dict[str, str]:
    """Environment with the bundled static ffmpeg/ffprobe first on PATH."""
    ffmpeg, _ = static_ffmpeg.run.get_or_fetch_platform_executables_else_raise()
    return {**os.environ, "PATH": f"{Path(ffmpeg).parent}{os.pathsep}{os.environ['PATH']}"}


def run(args: list[str], loglevel: str = "error") -> subprocess.CompletedProcess[str]:
    """Run ffmpeg with `args`; raises with stderr on failure."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-nostdin", "-loglevel", loglevel, *args]
    proc = subprocess.run(cmd, env=env(), capture_output=True, text=True, check=False)
    if proc.returncode:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {proc.stderr[-2000:]}")
    return proc


def probe(path: Path) -> dict:
    cmd = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]
    out = subprocess.run(cmd, env=env(), capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def has_video(path: Path) -> bool:
    return any(s["codec_type"] == "video" for s in probe(path)["streams"])


def audio_codec_args(out: Path) -> list[str]:
    """Encoder args chosen from the output extension."""
    match out.suffix.lower():
        case ".wav":
            return ["-c:a", "pcm_s24le"]
        case ".mp3":
            return ["-c:a", "libmp3lame", "-b:a", "192k"]
        case ".flac":
            return ["-c:a", "flac"]
        case _:
            return ["-c:a", "aac", "-b:a", "192k"]
