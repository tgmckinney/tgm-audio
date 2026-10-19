# tgm-audio

Audio stage for the factory: music sourcing (Epidemic Sound), cloned-voice TTS (ElevenLabs),
music beds with ducking, loudness mastering, and a licence ledger that gates publishing.

Pipeline stages are plain functions over files, so they run from the CLI, from other projects
(inflation Studio), or later as factory jobs (`factory.toml`, `runs_on = "mini"`).

```
source (epidemic / tts / own) → library (frozen file + Cue) → bed (fit, duck) → master → mux
                                       └── uses.jsonl ── lib check <item> ── publish gate
```

## Setup

```sh
uv sync
cp .env.example .env            # then fill keys in your editor
cp licenses.example.toml ~/.local/share/tgm-audio/licenses.toml   # list your channels
```

## Commands

```sh
uv run tgm-audio measure clip.mp4
uv run tgm-audio master in.wav out.wav --target podcast        # youtube -14, podcast -16 LUFS
uv run tgm-audio bed music.wav out.mp4 --video silent.mp4 --voice vo.wav

uv run --env-file .env tgm-audio epidemic search --term "curious" --vocals false --bpm-max 110
uv run --env-file .env tgm-audio epidemic fetch <trackId> --seconds 32    # Adapt to exact length
uv run --env-file .env tgm-audio epidemic fetch <trackId> --stems

uv run --env-file .env tgm-audio voice clone tom samples/*.wav           # → voice_id
uv run --env-file .env tgm-audio voice say tom "Egg prices fell 4% this month."

uv run tgm-audio lib ls
uv run tgm-audio lib use <cue> inflation/short/ap-eggs youtube:@channel
uv run tgm-audio lib check inflation/short/ap-eggs                       # exit 1 if not cleared
```

## Library

`$TGM_AUDIO_LIBRARY` (default `~/.local/share/tgm-audio`): `files/` (content-addressed),
`cues.jsonl`, `uses.jsonl`, `licenses.toml`. Run renders on the Mac mini so the library lives there.

## Voices

`voices.toml` maps names to ElevenLabs voice IDs. `consent` is required: `"self"` for your own voice;
for anyone else record who agreed, when, and where. Instant clones need ~1–3 min of clean speech;
Professional clones (better, needs 30+ min and the speaker's verification) are made in the
ElevenLabs web app, then added here by ID.
