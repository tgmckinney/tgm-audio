import json
import subprocess
from pathlib import Path

import pytest

from tgm_audio import ffmpeg, podcast, qa


def tone(path: Path, seconds: float, src: str, extra: list[str] | None = None) -> Path:
    ffmpeg.run(["-f", "lavfi", "-i", f"{src}:duration={seconds}", *(extra or []), "-ar", "48000",
                str(path)])  # fmt: skip
    return path


@pytest.fixture
def episode(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    tone(raw / "host.wav", 20, "sine=frequency=220", ["-af", "volume=-20dB"])
    tone(raw / "guest.wav", 20, "sine=frequency=330", ["-af", "volume=-6dB"])
    tone(tmp_path / "theme.wav", 30, "anoisesrc=color=pink:amplitude=0.4", ["-ac", "2"])
    ffmpeg.run(["-f", "lavfi", "-i", "color=c=red:s=1600x1600:d=1", "-frames:v", "1",
                str(tmp_path / "cover.png")])  # fmt: skip
    toml = tmp_path / "ep.toml"
    toml.write_text(
        'title = "Ep = One; test"\nshow = "Test Show"\nnumber = 3\ncover = "cover.png"\n'
        '[[tracks]]\nfile = "raw/host.wav"\n[[tracks]]\nfile = "raw/guest.wav"\noffset = 0.5\n'
        '[intro]\nsource = "theme.wav"\nseconds = 8\noverlap = 3\n'
        '[outro]\nsource = "theme.wav"\nseconds = 10\noverlap = 4\n'
        '[[chapters]]\nstart = 0\ntitle = "Welcome"\n[[chapters]]\nstart = 10\ntitle = "Part 2"\n'
    )
    return toml


def test_build_episode(episode: Path, tmp_path: Path):
    ep = podcast.load(episode)
    assert ep.lead == 5 and ep.tracks[0].file.is_absolute()
    out = tmp_path / "out.mp3"
    r = podcast.build(ep, out)
    # lead 5 + voice 20.5 + outro tail 6
    assert r.seconds == pytest.approx(31.5, abs=0.2)
    assert r.lufs == pytest.approx(-16, abs=1) and r.true_peak <= -0.5
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_chapters", "-show_format", "-show_streams", "-of", "json",
         str(out)], env=ffmpeg.env(), capture_output=True, text=True, check=True).stdout)  # fmt: skip
    chapters = [(round(float(c["start_time"]), 1), c["tags"]["title"]) for c in probe["chapters"]]
    assert chapters == [(0.0, "Welcome"), (15.0, "Part 2")]  # 10 s + 5 s lead
    tags = probe["format"]["tags"]
    assert tags["title"] == "Ep = One; test" and tags["album"] == "Test Show"
    assert any(s.get("disposition", {}).get("attached_pic") for s in probe["streams"])


def test_voice_levels_mismatched_tracks(episode: Path, tmp_path: Path):
    ep = podcast.load(episode)
    ep.clean.compress = False
    v = podcast.voice(ep.model_copy(update={"tracks": ep.tracks[:1]}), tmp_path / "a.wav")
    g = podcast.voice(ep.model_copy(update={"tracks": ep.tracks[1:]}), tmp_path / "b.wav")
    # 14 dB apart raw; leveled to the same speech loudness
    assert qa.report(v).lufs == pytest.approx(qa.report(g).lufs, abs=1)


def test_report_flags_silence_and_level(tmp_path: Path):
    f = tmp_path / "s.wav"
    ffmpeg.run(["-f", "lavfi", "-i", "sine=frequency=300:duration=3", "-af",
                "volume=-30dB,apad=pad_dur=6", str(f)])  # fmt: skip
    r = qa.report(f)
    assert any(p.startswith("silence 6s") for p in r.problems)
    assert any(p.startswith("loudness") for p in r.problems)


def test_chapters_file(tmp_path: Path):
    (tmp_path / "draft.json").write_text(
        json.dumps(
            {"slug": "x", "chapters": [{"start": 0, "title": "A"}, {"start": 60, "title": "B"}]}
        )
    )
    (tmp_path / "ep.toml").write_text(
        'title = "t"\nshow = "s"\nchapters_file = "draft.json"\n[[tracks]]\nfile = "a.wav"\n'
    )
    ep = podcast.load(tmp_path / "ep.toml")
    assert [c.title for c in ep.chapters] == ["A", "B"]
    meta = podcast.ffmetadata(ep, 120)
    assert "START=60000" in meta and "END=120000" in meta


def test_batch_reports_and_clips(episode: Path, tmp_path: Path):
    plans = tmp_path / "plans"
    plans.mkdir()
    raw = tmp_path / "raw"
    ffmpeg.run(["-i", str(raw / "host.wav"), "-c:a", "libmp3lame", str(raw / "mix.mp3")])
    (plans / "ep1.toml").write_text(
        'title = "One"\nshow = "S"\n[[tracks]]\nfile = "../raw/mix.mp3"\n'
    )
    (plans / "bad.toml").write_text('title = "Bad"\nshow = "S"\n[[tracks]]\nfile = "nope.mp3"\n')
    rows = {r["id"]: r for r in podcast.batch(plans, tmp_path / "out", jobs=2)}
    assert "error" in rows["bad"]
    one = rows["ep1"]
    assert one["after"]["lufs"] == pytest.approx(-16, abs=1) and one["before"]["lufs"] < -20
    assert [c["start"] for c in one["clips"]] == [0.0, 0.0]  # 20 s file: 40 s clips clamp to 0
    assert (tmp_path / "out/clips" / one["clips"][0]["after"]).exists()
    assert json.loads((tmp_path / "out/review.json").read_text())[0]["id"] in rows
