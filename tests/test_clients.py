import json
from pathlib import Path

import httpx
import pytest

from tgm_audio import epidemic, tts
from tgm_audio.ledger import Library

META = {"id": "abc", "title": "Rise", "mainArtists": ["Ana"], "bpm": 120, "length": 90000,
        "moods": [], "genres": []}  # fmt: skip


def test_epidemic_fetch_with_stems(tmp_path: Path, music: Path, monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer k"
        if req.url.path == "/v0/tracks/metadata":
            return httpx.Response(200, json=[META])
        assert req.url.params["expand"] == "stems"
        return httpx.Response(
            200,
            json={
                "url": "https://cdn/full.mp3?sig=1",
                "stems": [{"type": "drums", "url": "https://cdn/d.mp3"}],
            },
        )

    monkeypatch.setattr(epidemic.http, "download", lambda url, dst: dst.write_bytes(
        url.encode()) and dst)  # fmt: skip
    es = epidemic.Epidemic("k", transport=httpx.MockTransport(handler))
    cues = epidemic.fetch(Library(tmp_path), es, "abc", stems=True)
    assert [c.stem for c in cues] == [None, "drums"]
    assert cues[0].license == "epidemic:creator" and cues[0].url == "https://cdn/full.mp3"
    assert cues[0].credit == 'Music: "Rise" by Ana (Epidemic Sound)'


def test_epidemic_adapt_polls(monkeypatch):
    monkeypatch.setattr(epidemic.time, "sleep", lambda s: None)
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.method)
        if req.method == "POST":
            assert json.loads(req.content) == {"trackId": "abc", "durationMs": 30000}
            return httpx.Response(202, json={"status": "PENDING", "jobId": "j", "results": []})
        return httpx.Response(
            200,
            json={
                "status": "COMPLETED",
                "jobId": "j",
                "results": [{"highQualityUrl": "https://cdn/a.wav"}],
            },
        )

    es = epidemic.Epidemic("k", transport=httpx.MockTransport(handler))
    assert es.adapt("abc", 30) == "https://cdn/a.wav"
    assert calls == ["POST", "GET"]


def test_say_requires_configured_voice_with_consent(tmp_path: Path, monkeypatch):
    voices = tmp_path / "voices.toml"
    voices.write_text('[voices.tom]\nvoice_id = "v1"\nconsent = "self"\n')
    monkeypatch.setenv("TGM_AUDIO_VOICES", str(voices))

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/text-to-speech/v1"
        return httpx.Response(200, content=b"ID3fake")

    el = tts.ElevenLabs("k", transport=httpx.MockTransport(handler))
    cue = tts.say(Library(tmp_path / "lib"), el, "tom", "Egg prices fell.")
    assert cue.kind == "tts" and cue.meta["characters"] == 16
    with pytest.raises(KeyError):
        tts.say(Library(tmp_path / "lib"), el, "nobody", "x")
    voices.write_text('[voices.guest]\nvoice_id = "v2"\nconsent = ""\n')
    with pytest.raises(ValueError):
        tts.load_voices()
