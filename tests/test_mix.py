from pathlib import Path

import pytest

from tgm_audio import ffmpeg, mix
from tgm_audio.master import master, measure


def _slice_lufs(path: Path, start: float, dur: float, tmp: Path) -> float:
    cut = tmp / f"cut{start}.wav"
    ffmpeg.run(["-ss", str(start), "-t", str(dur), "-i", str(path), str(cut)])
    return measure(cut).lufs


def test_master_hits_target(music: Path, tmp_path: Path):
    m = master(music, tmp_path / "m.wav", "podcast")
    assert m.lufs == pytest.approx(-16, abs=1.0)
    assert m.true_peak <= -0.5


def test_bed_loops_to_length_and_ducks(voice: Path, music: Path, tmp_path: Path):
    out = mix.bed(music, 9.0, tmp_path / "bed.wav", voice, mix.Bed(fade_in=0, fade_out=0))
    assert ffmpeg.duration(out) == pytest.approx(9.0, abs=0.05)  # 4 s music looped
    alone = mix.bed(music, 9.0, tmp_path / "alone.wav", None, mix.Bed(fade_in=0, fade_out=0))
    # 1.5-3.5 s has voice; 6-8 s is music only: music must be at bed level there in both mixes
    assert _slice_lufs(out, 6, 2, tmp_path) == pytest.approx(
        _slice_lufs(alone, 6, 2, tmp_path), abs=1.0
    )


def test_ducking_lowers_music_under_voice(voice: Path, music: Path, tmp_path: Path):
    b = mix.Bed(fade_in=0, fade_out=0)
    ducked = mix.bed(music, 6.0, tmp_path / "d.wav", voice, b)
    flat = mix.bed(music, 6.0, tmp_path / "f.wav", voice, b.model_copy(update={"duck_ratio": 1}))
    # Same voice in both, so a quieter voice section means the music was pulled down
    assert _slice_lufs(ducked, 2, 1.5, tmp_path) < _slice_lufs(flat, 2, 1.5, tmp_path) - 0.3


def test_mux_keeps_video_length(music: Path, tmp_path: Path):
    video = tmp_path / "v.mp4"
    ffmpeg.run(["-f", "lavfi", "-i", "color=c=black:s=64x64:d=2", "-c:v", "libx264", str(video)])
    out = mix.mux(video, music, tmp_path / "out.mp4")
    assert ffmpeg.has_video(out)
    assert ffmpeg.duration(out) == pytest.approx(2.0, abs=0.1)
