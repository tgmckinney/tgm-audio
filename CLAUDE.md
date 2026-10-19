# CLAUDE.md

Audio stage for the factory (factory.georgewyrd.com, `../workbench/SPEC.md`): Epidemic Sound music,
ElevenLabs cloned-voice TTS, music beds with ducking, loudness mastering, licence ledger.
See README.md for commands.

## Commands

```sh
uv sync
uv run ruff check . && uv run ruff format --check .   # CI runs both; line length 100
uv run pytest -q                                      # offline: synthetic audio + mocked HTTP
```

## Architecture

- Stages are functions over files (`mix.bed`, `master.master`, `mix.mux`); callers (CLI, inflation
  Studio, future factory jobs with `runs_on = "mini"`) compose them. Keep them importable and stateless.
- ffmpeg comes from `static-ffmpeg` (no Homebrew on the work laptop); always call through `ffmpeg.run`.
- Levels: `bed` static-gains music to `music_lufs` and voice to `voice_lufs` (measured), ducks music
  with `sidechaincompress` keyed on the voice, then `master` does linear two-pass `loudnorm`
  (youtube −14, podcast −16 LUFS, −1 dBTP). Master the mix, then mux; never loudnorm inside `bed`.
- **Ledger** (`ledger.py`): every file used is frozen into the content-addressed library with a `Cue`
  (source, licence, credit); placements are `Use` rows (item, channel). `Library.check(item)` is the
  publish gate against `licenses.toml`. Never place audio in a published item without a cue.
- Epidemic: plan is Creator (one channel per platform + one podcast feed, no client work). Licence key
  `epidemic:<EPIDEMIC_PLAN>`. `epidemic.py` talks to the **MCP service** (JSON-RPC over streamable
  HTTP; tools return GraphQL JSON) with an account API key; the Partner Content API returns 401 for
  those keys. Download URLs are pre-signed and expire: freeze immediately.
- Podcast (`podcast.py`): Episode TOML → `voice` (per-track clean/level/compress, amix) →
  `with_music` (intro lead-in = `seconds - overlap`, outro under the end, sidechain-ducked) → master
  (TP −1.5 so the MP3 QA's at −1) → `encode` (ffmetadata tags + chapters, cover). Chapter times are on
  the voice timeline and shifted by the lead-in. `qa.report` is one decode (silencedetect,
  volumedetect, loudnorm) and is the gate for any delivered file.
- TTS: `voices.toml` voices require `consent`; a non-self clone needs the speaker's recorded consent.
- httpx clients use `truststore` (work network TLS inspection). Keys in `.env` (gitignored).
