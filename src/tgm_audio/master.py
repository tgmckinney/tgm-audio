"""Loudness measurement and two-pass EBU R128 mastering (ffmpeg `loudnorm`)."""

import json
import re
from pathlib import Path

from pydantic import BaseModel

from tgm_audio import ffmpeg


class Target(BaseModel):
    lufs: float
    true_peak: float = -1.0
    lra: float = 11.0


TARGETS: dict[str, Target] = {
    "youtube": Target(lufs=-14),
    "podcast": Target(lufs=-16),
    "podcast-mono": Target(lufs=-19),
    "broadcast": Target(lufs=-23, lra=15),
}


class Loudness(BaseModel):
    """Integrated loudness (LUFS), true peak (dBTP), loudness range (LU)."""

    lufs: float
    true_peak: float
    lra: float
    threshold: float


def _loudnorm_json(stderr: str) -> dict:
    blocks = re.findall(r"\{[^{}]*\}", stderr)
    if not blocks:
        raise RuntimeError("loudnorm printed no measurement")
    return json.loads(blocks[-1])


def _num(v: str) -> float:
    return float("-inf") if v.strip() == "-inf" else float(v)


def measure(path: Path, target: Target = TARGETS["youtube"]) -> Loudness:
    af = f"loudnorm=I={target.lufs}:TP={target.true_peak}:LRA={target.lra}:print_format=json"
    proc = ffmpeg.run(["-i", str(path), "-map", "0:a:0", "-af", af, "-f", "null", "-"], "info")
    m = _loudnorm_json(proc.stderr)
    return Loudness(
        lufs=_num(m["input_i"]),
        true_peak=_num(m["input_tp"]),
        lra=_num(m["input_lra"]),
        threshold=_num(m["input_thresh"]),
    )


def master(src: Path, out: Path, target: str | Target = "youtube") -> Loudness:
    """Normalize `src` to the target with linear two-pass loudnorm; returns the result's loudness.

    A video input keeps its video stream (copied) and gets AAC audio.
    """
    t = TARGETS[target] if isinstance(target, str) else target
    m = measure(src, t)
    af = (
        f"loudnorm=I={t.lufs}:TP={t.true_peak}:LRA={t.lra}"
        f":measured_I={m.lufs}:measured_TP={m.true_peak}:measured_LRA={m.lra}"
        f":measured_thresh={m.threshold}:linear=true,aresample=48000"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    if ffmpeg.has_video(src):
        args = ["-i", str(src), "-map", "0:v", "-map", "0:a:0", "-c:v", "copy", "-af", af,
                "-c:a", "aac", "-b:a", "192k", str(out)]  # fmt: skip
    else:
        args = ["-i", str(src), "-map", "0:a:0", "-af", af, *ffmpeg.audio_codec_args(out), str(out)]
    ffmpeg.run(args)
    return measure(out, t)
