from pathlib import Path
from typing import Annotated

import typer

from tgm_audio import epidemic, mix, tts
from tgm_audio import master as mastering
from tgm_audio.ledger import Library

app = typer.Typer(
    no_args_is_help=True, help="Audio stage for the factory: music, voice, mix, master."
)
lib_app = typer.Typer(no_args_is_help=True, help="The library and licence ledger.")
es_app = typer.Typer(no_args_is_help=True, help="Epidemic Sound.")
voice_app = typer.Typer(no_args_is_help=True, help="ElevenLabs voices.")
app.add_typer(lib_app, name="lib")
app.add_typer(es_app, name="epidemic")
app.add_typer(voice_app, name="voice")

Opt = typer.Option


@app.command()
def measure(path: Path):
    """Integrated loudness, true peak and range."""
    m = mastering.measure(path)
    typer.echo(f"{m.lufs:.1f} LUFS  {m.true_peak:.1f} dBTP  LRA {m.lra:.1f}")


@app.command()
def master(
    src: Path,
    out: Path,
    target: Annotated[str, Opt(help=f"One of {', '.join(mastering.TARGETS)}")] = "youtube",
):
    """Two-pass loudness normalization (video stream is copied)."""
    m = mastering.master(src, out, target)
    typer.echo(f"{out}: {m.lufs:.1f} LUFS  {m.true_peak:.1f} dBTP")


@app.command()
def bed(
    music: Path,
    out: Path,
    seconds: Annotated[float, Opt(help="Length; default the video's or music's")] = 0,
    voice: Path | None = None,
    video: Annotated[Path | None, Opt(help="Mux the mastered mix into this video")] = None,
    music_start: float = 0.0,
    music_lufs: float = -24.0,
    voice_offset: float = 0.0,
    duck: Annotated[float, Opt(help="Sidechain ratio; 1 disables")] = 8.0,
    target: str = "youtube",
):
    """Music under optional voice, ducked, mastered; optionally muxed into a video."""
    seconds = seconds or mix.ffmpeg.duration(video or music)
    b = mix.Bed(music_start=music_start, music_lufs=music_lufs, voice_offset=voice_offset,
                duck_ratio=duck)  # fmt: skip
    raw = out.with_suffix(".raw.wav")
    mix.bed(music, seconds, raw, voice, b)
    mixed = out if video is None else out.with_suffix(".mix.wav")
    m = mastering.master(raw, mixed, target)
    raw.unlink()
    if video:
        mix.mux(video, mixed, out)
        mixed.unlink()
    typer.echo(f"{out}: {seconds:.2f}s  {m.lufs:.1f} LUFS  {m.true_peak:.1f} dBTP")


@lib_app.command("ls")
def lib_ls(kind: str | None = None):
    """List cues."""
    for c in Library().cues().values():
        if kind is None or c.kind == kind:
            typer.echo(f"{c.id[:12]}  {c.kind:<5} {c.license:<18} {c.title or ''}  {c.stem or ''}")


@lib_app.command("add")
def lib_add(
    path: Path,
    kind: str = "music",
    source: str = "own",
    license: str = "own",
    title: str | None = None,
    credit: str | None = None,
    source_id: str | None = None,
):
    """Freeze a local file (own recording, Freesound, an Epidemic download from the web/MCP)."""
    c = Library().add(path, kind=kind, source=source, license=license, title=title,
                      credit=credit, source_id=source_id)  # fmt: skip
    typer.echo(c.id)


@lib_app.command("use")
def lib_use(cue: str, item: str, channel: str):
    """Record that a cue (id or unique prefix) is placed in a factory item for a channel."""
    lib = Library()
    ids = [i for i in lib.cues() if i.startswith(cue)]
    if len(ids) != 1:
        raise typer.BadParameter(f"{cue!r} matches {len(ids)} cues")
    lib.use(ids[0], item, channel)


@lib_app.command("check")
def lib_check(item: str):
    """Licence gate: exit 1 with the problems if the item is not cleared to publish."""
    lib = Library()
    problems = lib.check(item)
    for p in problems:
        typer.echo(f"✗ {p}", err=True)
    if problems:
        raise typer.Exit(1)
    typer.echo("cleared")
    for line in lib.credits(item):
        typer.echo(line)


@es_app.command("search")
def es_search(
    term: str | None = None,
    bpm_min: int | None = None,
    bpm_max: int | None = None,
    vocals: bool | None = None,
    limit: int = 10,
):
    es = epidemic.Epidemic()
    for t in es.search(term, bpm_min=bpm_min, bpm_max=bpm_max, vocals=vocals, limit=limit):
        moods = ",".join(m["name"] for m in t.get("moods", [])[:3])
        typer.echo(
            f"{t['id']}  {t['bpm']:>3} bpm  {t['length'] / 1000:>5.0f}s  {t['title']}  [{moods}]"
        )


@es_app.command("fetch")
def es_fetch(
    track_id: str,
    seconds: Annotated[float | None, Opt(help="Adapt to this exact length")] = None,
    stems: bool = False,
):
    """Download into the library (full track, Adapt version, or with stems)."""
    for c in epidemic.fetch(Library(), epidemic.Epidemic(), track_id, seconds=seconds, stems=stems):
        typer.echo(f"{c.id[:12]}  {c.stem or 'full'}  {Library().path(c)}")


@voice_app.command("clone")
def voice_clone(name: str, samples: list[Path], description: str = ""):
    """Instant clone from sample recordings; prints the voice_id to put in voices.toml."""
    typer.echo(tts.ElevenLabs().clone(name, samples, description))


@voice_app.command("ls")
def voice_ls():
    for v in tts.ElevenLabs().voices():
        typer.echo(f"{v['voice_id']}  {v['category']:<12} {v['name']}")


@voice_app.command("say")
def voice_say(voice: str, text: str, out: Path | None = None):
    """Synthesize into the library (and optionally copy to `out`)."""
    lib = Library()
    c = tts.say(lib, tts.ElevenLabs(), voice, text)
    if out:
        out.write_bytes(lib.path(c).read_bytes())
    typer.echo(f"{c.id[:12]}  {lib.path(c)}")
