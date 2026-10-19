"""One-pass audio QA: loudness, true peak, sample peak, long silences, format, against a target."""

import re
from pathlib import Path

from pydantic import BaseModel

from tgm_audio import ffmpeg
from tgm_audio.master import TARGETS, Target, _loudnorm_json, _num


class Report(BaseModel):
    file: str
    seconds: float
    codec: str
    channels: int
    sample_rate: int
    bitrate_kbps: int | None
    lufs: float
    true_peak: float
    lra: float
    max_volume: float
    silences: list[tuple[float, float]]
    problems: list[str]


def report(path: Path, target: str | Target = "podcast", silence_db: float = -50,
           silence_min: float = 4.0) -> Report:  # fmt: skip
    """Measure `path` in one decode; `problems` lists deviations from the target."""
    t = TARGETS[target] if isinstance(target, str) else target
    af = (f"silencedetect=n={silence_db}dB:d={silence_min},volumedetect,"
          f"loudnorm=I={t.lufs}:TP={t.true_peak}:LRA={t.lra}:print_format=json")  # fmt: skip
    err = ffmpeg.run(
        ["-i", str(path), "-map", "0:a:0", "-af", af, "-f", "null", "-"], "info"
    ).stderr
    m = _loudnorm_json(err)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    seconds = ffmpeg.duration(path)
    silences = [(round(s, 1), round(e, 1)) for s, e in zip(starts, ends + [seconds], strict=False)]
    max_vol = float(re.search(r"max_volume: (-?[\d.]+) dB", err).group(1))
    stream = next(s for s in ffmpeg.probe(path)["streams"] if s["codec_type"] == "audio")
    br = stream.get("bit_rate")
    r = Report(
        file=str(path), seconds=round(seconds, 1), codec=stream["codec_name"],
        channels=stream["channels"], sample_rate=int(stream["sample_rate"]),
        bitrate_kbps=int(br) // 1000 if br else None, lufs=_num(m["input_i"]),
        true_peak=_num(m["input_tp"]), lra=_num(m["input_lra"]), max_volume=max_vol,
        silences=silences, problems=[],
    )  # fmt: skip
    if abs(r.lufs - t.lufs) > 1:
        r.problems.append(f"loudness {r.lufs:.1f} LUFS, target {t.lufs:g}")
    if r.true_peak > t.true_peak:
        r.problems.append(f"true peak {r.true_peak:.1f} dBTP above {t.true_peak:g}")
    if r.max_volume >= -0.1:
        r.problems.append("sample peaks at 0 dBFS (likely clipping)")
    if r.lra > 15:
        r.problems.append(f"loudness range {r.lra:.0f} LU (uneven levels)")
    for s, e in silences:
        r.problems.append(f"silence {e - s:.0f}s at {s // 60:.0f}:{s % 60:04.1f}")
    return r
