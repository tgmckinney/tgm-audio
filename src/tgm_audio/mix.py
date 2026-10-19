"""Music beds under voice: level-match, loop/trim to length, fade, sidechain-duck, mux."""

from pathlib import Path

from pydantic import BaseModel, Field

from tgm_audio import ffmpeg
from tgm_audio.master import measure

FMT = "aformat=sample_rates=48000:channel_layouts=stereo"


class Bed(BaseModel):
    """How a music cue sits in a mix. Levels are integrated LUFS before the final master."""

    music_start: float = Field(default=0.0, ge=0, description="Seconds into the music file")
    music_lufs: float = Field(default=-24.0, le=-6, description="Bed level with no voice")
    voice_lufs: float = Field(default=-16.0, le=-6)
    voice_offset: float = Field(default=0.0, ge=-30, le=60, description="Seconds; negative trims")
    duck_ratio: float = Field(default=8.0, ge=1, le=20, description="1 disables ducking")
    duck_threshold: float = Field(default=0.02, gt=0, le=1)
    attack_ms: float = 20
    release_ms: float = 400
    fade_in: float = Field(default=0.5, ge=0)
    fade_out: float = Field(default=2.0, ge=0)


def _gain(path: Path, lufs: float) -> float:
    """dB to bring `path` to `lufs` (static gain, keeps dynamics)."""
    return round(lufs - measure(path).lufs, 2)


def _shift(offset: float) -> str:
    if offset >= 0:
        return f"adelay={int(offset * 1000)}:all=1"
    return f"atrim=start={-offset},asetpts=PTS-STARTPTS"


def bed(
    music: Path, seconds: float, out: Path, voice: Path | None = None, b: Bed | None = None
) -> Path:
    """Mix `music` (looped if short) under optional `voice` into exactly `seconds` of audio."""
    b = b or Bed()
    fade_out = min(b.fade_out, seconds / 2)
    music_chain = (
        f"[{1 if voice else 0}:a]atrim=start={b.music_start}:duration={seconds},"
        f"asetpts=PTS-STARTPTS,volume={_gain(music, b.music_lufs)}dB,{FMT},"
        f"afade=t=in:d={b.fade_in},afade=t=out:st={seconds - fade_out}:d={fade_out}"
    )
    inputs: list[str] = []
    if voice:
        inputs += ["-i", str(voice)]
    inputs += ["-stream_loop", "-1", "-i", str(music)]
    if voice is None:
        graph = f"{music_chain}[out]"
    else:
        graph = (
            f"{music_chain}[m];"
            f"[0:a]{_shift(b.voice_offset)},volume={_gain(voice, b.voice_lufs)}dB,{FMT},"
            f"apad,atrim=duration={seconds},asplit=2[v][sc];"
            f"[m][sc]sidechaincompress=threshold={b.duck_threshold}:ratio={b.duck_ratio}"
            f":attack={b.attack_ms}:release={b.release_ms}[md];"
            f"[v][md]amix=inputs=2:duration=first:normalize=0[out]"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run([*inputs, "-filter_complex", graph, "-map", "[out]", "-t", f"{seconds}",
                *ffmpeg.audio_codec_args(out), str(out)])  # fmt: skip
    return out


def mux(video: Path, audio: Path, out: Path) -> Path:
    """Replace `video`'s audio with `audio` (video stream copied, cut to the video's length)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run(["-i", str(video), "-i", str(audio), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k", "-t", f"{ffmpeg.duration(video)}", str(out)])  # fmt: skip
    return out
