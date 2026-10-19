"""Podcast episodes: clean and level voice tracks, intro/outro music ducked under the voice,
master (-16 LUFS stereo / -19 mono), MP3 with ID3 tags, chapters and cover art, then QA.

An episode is a TOML file (paths relative to it); see `examples/episode.toml`. Times in
`chapters` are on the voice timeline (as in a transcript of the raw recording); the intro's
lead-in (`intro.seconds - intro.overlap`) is added on output.
"""

import json
import re
import tempfile
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

from tgm_audio import ffmpeg, master, qa
from tgm_audio.ledger import Library


class Track(BaseModel):
    file: Path
    name: str = "mix"
    offset: float = Field(default=0.0, ge=0, description="Seconds of delay to align multitrack")
    gain_db: float = Field(default=0.0, ge=-20, le=20, description="Trim after leveling")


class Clean(BaseModel):
    highpass_hz: float = Field(default=80, ge=0, le=300, description="0 disables")
    denoise: float = Field(default=0, ge=0, le=40, description="afftdn reduction in dB; 0 = off")
    level: bool = Field(default=True, description="Static gain each track to speech_lufs")
    speech_lufs: float = -20
    compress: bool = True


class Music(BaseModel):
    source: str = Field(description="Library cue id (64 hex) or a file path")
    seconds: float = Field(gt=0, le=300, description="How long the cue plays in total")
    overlap: float = Field(default=4.0, ge=0, description="Seconds it plays under the voice")
    start: float = Field(default=0.0, ge=0, description="Seconds into the music file")
    lufs: float = Field(default=-22.0, le=-10, description="Level before the master")
    fade: float = Field(default=3.0, ge=0)


class Chapter(BaseModel):
    start: float = Field(ge=0, description="Seconds on the voice timeline")
    title: str = Field(min_length=1)


class Episode(BaseModel):
    title: str
    show: str
    artist: str | None = None
    number: int | None = None
    date: str | None = None
    description: str | None = None
    tracks: list[Track] = Field(min_length=1)
    clean: Clean = Clean()
    intro: Music | None = None
    outro: Music | None = None
    chapters: list[Chapter] = []
    chapters_file: Path | None = Field(
        default=None,
        description='JSON: [{start, title}] or {"chapters": [...]}; used if no chapters',
    )
    cover: Path | None = None
    mono: bool = False
    bitrate: str = "128k"

    @property
    def target(self) -> str:
        return "podcast-mono" if self.mono else "podcast"

    @property
    def lead(self) -> float:
        """Where the voice starts in the output."""
        return max(0.0, self.intro.seconds - self.intro.overlap) if self.intro else 0.0


def load(path: Path) -> Episode:
    """Read an episode TOML; relative file paths resolve against its directory."""
    ep = Episode.model_validate(tomllib.loads(path.read_text()))
    base = path.parent
    for t in ep.tracks:
        t.file = (base / t.file).resolve()
    if ep.cover:
        ep.cover = (base / ep.cover).resolve()
    if ep.chapters_file and not ep.chapters:
        data = json.loads((base / ep.chapters_file).read_text())
        rows = data["chapters"] if isinstance(data, dict) else data
        ep.chapters = [Chapter(start=r["start"], title=r["title"]) for r in rows]
    for m in (ep.intro, ep.outro):
        if m and not re.fullmatch(r"[0-9a-f]{64}", m.source):
            m.source = str((base / m.source).resolve())
    return ep


def _music_path(m: Music) -> Path:
    if re.fullmatch(r"[0-9a-f]{64}", m.source):
        return Library().path(m.source)
    return Path(m.source)


def _fmt(ep: Episode) -> str:
    return f"aformat=sample_rates=48000:channel_layouts={'mono' if ep.mono else 'stereo'}"


def voice(ep: Episode, out: Path) -> Path:
    """Clean, level and mix the voice tracks into one file."""
    chains, inputs = [], []
    for i, t in enumerate(ep.tracks):
        inputs += ["-i", str(t.file)]
        gain = t.gain_db
        if ep.clean.level:
            gain += ep.clean.speech_lufs - master.measure(t.file).lufs
        f = [_fmt(ep)]
        if t.offset:
            f.append(f"adelay={int(t.offset * 1000)}:all=1")
        if ep.clean.highpass_hz:
            f.append(f"highpass=f={ep.clean.highpass_hz}")
        if ep.clean.denoise:
            f.append(f"afftdn=nr={ep.clean.denoise}:nf=-40")
        f.append(f"volume={gain:.2f}dB")
        if ep.clean.compress:
            f.append("acompressor=threshold=-28dB:ratio=2.5:attack=15:release=250:makeup=2")
        chains.append(f"[{i}:a]{','.join(f)}[t{i}]")
    n = len(ep.tracks)
    mixdown = f"{''.join(f'[t{i}]' for i in range(n))}amix=inputs={n}:duration=longest:normalize=0"
    graph = ";".join(chains) + (f";{mixdown}[out]" if n > 1 else "")
    out_label = "[out]" if n > 1 else "[t0]"
    ffmpeg.run(
        [*inputs, "-filter_complex", graph, "-map", out_label, "-c:a", "pcm_s24le", str(out)]
    )
    return out


def with_music(ep: Episode, voice_wav: Path, out: Path) -> Path:
    """Intro before and under the voice start, outro under the voice end; music ducked."""
    if not (ep.intro or ep.outro):
        return voice_wav
    v = ffmpeg.duration(voice_wav)
    lead = ep.lead
    tail = ep.outro.seconds - ep.outro.overlap if ep.outro else 0.0
    total = lead + v + max(0.0, tail)
    inputs, chains, beds = ["-i", str(voice_wav)], [], []
    for m, kind in ((ep.intro, "intro"), (ep.outro, "outro")):
        if not m:
            continue
        idx = len(inputs) // 2
        inputs += ["-i", str(_music_path(m))]
        gain = m.lufs - master.measure(_music_path(m)).lufs
        fade = min(m.fade, m.seconds / 2)
        f = [f"atrim=start={m.start}:duration={m.seconds}", "asetpts=PTS-STARTPTS",
             f"volume={gain:.2f}dB", _fmt(ep)]  # fmt: skip
        if kind == "outro":
            f += [f"afade=t=in:d={fade}", f"adelay={int((lead + v - m.overlap) * 1000)}:all=1"]
            f.append(f"afade=t=out:st={total - fade}:d={fade}")
        else:
            f.append(f"afade=t=out:st={m.seconds - fade}:d={fade}")
        f.append(f"apad=whole_dur={total}")
        chains.append(f"[{idx}:a]{','.join(f)}[{kind}]")
        beds.append(f"[{kind}]")
    bed = "".join(beds) + (f"amix=inputs={len(beds)}:normalize=0" if len(beds) > 1 else "anull")
    graph = ";".join([
        f"[0:a]{_fmt(ep)},adelay={int(lead * 1000)}:all=1,apad=whole_dur={total},asplit=2[v][sc]",
        *chains,
        f"{bed}[m]",
        "[m][sc]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=600[md]",
        f"[v][md]amix=inputs=2:duration=longest:normalize=0,atrim=duration={total}[out]",
    ])  # fmt: skip
    ffmpeg.run([*inputs, "-filter_complex", graph, "-map", "[out]", "-c:a", "pcm_s24le", str(out)])
    return out


def _esc(s: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", s)


def ffmetadata(ep: Episode, total: float) -> str:
    """FFMETADATA1 text: ID3 tags plus chapters shifted by the intro lead-in."""
    tags = {"title": ep.title, "album": ep.show, "artist": ep.artist or ep.show,
            "album_artist": ep.artist or ep.show, "track": ep.number, "date": ep.date,
            "comment": ep.description, "genre": "Podcast"}  # fmt: skip
    lines = [";FFMETADATA1", *(f"{k}={_esc(str(v))}" for k, v in tags.items() if v is not None)]
    chapters = sorted(ep.chapters, key=lambda c: c.start)
    starts = [(c.start + ep.lead if c.start or not ep.lead else 0.0, c.title) for c in chapters]
    if starts and starts[0][0] > 1:
        starts.insert(0, (0.0, "Intro"))
    for i, (start, title) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else total
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(start * 1000)}",
                  f"END={int(end * 1000)}", f"title={_esc(title)}"]  # fmt: skip
    return "\n".join(lines) + "\n"


def encode(ep: Episode, wav: Path, out: Path, tmp: Path) -> Path:
    meta = tmp / "meta.txt"
    meta.write_text(ffmetadata(ep, ffmpeg.duration(wav)))
    args = ["-i", str(wav), "-i", str(meta)]
    maps = ["-map", "0:a", "-map_metadata", "1", "-map_chapters", "1"]
    if ep.cover:
        args += ["-i", str(ep.cover)]
        maps += ["-map", "2:v", "-c:v", "mjpeg", "-q:v", "3", "-vf", "scale='min(1400,iw)':-2",
                 "-disposition:v", "attached_pic", "-metadata:s:v", "title=Cover"]  # fmt: skip
    ac = ["-ac", "1"] if ep.mono else []
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run([*args, *maps, *ac, "-c:a", "libmp3lame", "-b:a", ep.bitrate,
                "-id3v2_version", "3", str(out)])  # fmt: skip
    return out


def build(ep: Episode, out: Path) -> qa.Report:
    """Voice → music → master → MP3; returns the QA report of the result."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        mixed = with_music(ep, voice(ep, tmp / "voice.wav"), tmp / "mixed.wav")
        mastered = tmp / "mastered.wav"
        # Lossy encoding overshoots true peak by ~0.2-0.5 dB: master with headroom, QA at -1.
        t = master.TARGETS[ep.target]
        master.master(mixed, mastered, t.model_copy(update={"true_peak": t.true_peak - 0.5}))
        encode(ep, mastered, out, tmp)
    return qa.report(out, ep.target)


def ab_clips(before: Path, after: Path, out_dir: Path, stem: str, lead: float = 0.0,
             at: tuple[float, ...] = (0.12, 0.6), seconds: float = 40) -> list[dict]:  # fmt: skip
    """Matching excerpts of an original and its remaster (stream copy, no re-encode)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    length = ffmpeg.duration(before)
    clips = []
    for i, frac in enumerate(at):
        start = round(max(0.0, min(frac * length, length - seconds)), 1)
        names = {}
        for side, src, offset in (("before", before, 0.0), ("after", after, lead)):
            name = f"{stem}-{i}-{side}{src.suffix}"
            ffmpeg.run(["-ss", f"{start + offset}", "-t", f"{seconds}", "-i", str(src),
                        "-map", "0:a", "-c", "copy", str(out_dir / name)])  # fmt: skip
            names[side] = name
        clips.append({"start": start, "seconds": seconds, **names})
    return clips


def batch(plans: Path, out: Path, jobs: int = 2, clips: bool = True) -> list[dict]:
    """Build every plans/*.toml into out/<stem>.mp3 with before/after QA (and A/B clips).

    Writes out/review.json; skips episodes whose output already exists.
    """
    from concurrent.futures import ThreadPoolExecutor

    out.mkdir(parents=True, exist_ok=True)

    def one(toml: Path) -> dict:
        ep = load(toml)
        dst = out / f"{toml.stem}.mp3"
        row = {"id": toml.stem, "title": ep.title, "number": ep.number, "date": ep.date,
               "chapters": len(ep.chapters)}  # fmt: skip
        try:
            after = qa.report(dst, ep.target) if dst.exists() else build(ep, dst)
            row |= {"before": qa.report(ep.tracks[0].file, ep.target).model_dump(),
                    "after": after.model_dump()}  # fmt: skip
            if clips and len(ep.tracks) == 1:
                row["clips"] = ab_clips(ep.tracks[0].file, dst, out / "clips", toml.stem, ep.lead)
        except Exception as e:  # noqa: BLE001 - one bad episode must not stop the batch
            row["error"] = str(e)[-1000:]
        return row

    with ThreadPoolExecutor(jobs) as ex:
        rows = list(ex.map(one, sorted(plans.glob("*.toml"))))
    (out / "review.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    return rows
