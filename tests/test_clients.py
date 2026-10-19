import json
from pathlib import Path

import httpx
import pytest

from tgm_audio import epidemic, tts
from tgm_audio.ledger import Library

REC = {
    "id": "abc", "title": "Rise", "bpm": 120,
    "audioFile": {"durationInMilliseconds": 90000, "lqmp3Url": "https://cdn/p.mp3"},
    "credits": [{"role": "MAIN_ARTIST", "artist": {"name": "Ana"}},
                {"role": "PRODUCER", "artist": {"name": "Bo"}}],
    "tags": [{"displayName": "hopeful", "dimension": {"name": "mood"}}],
    "stems": [{"type": "DRUMS"}],
}  # fmt: skip


def mcp(tools: dict, calls: list):
    """MockTransport for the MCP service: `tools` maps tool name -> fn(args) -> GraphQL data."""

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer k"
        body = json.loads(req.content)
        if body["method"] == "initialize":
            return httpx.Response(200, headers={"mcp-session-id": "s1"},
                                  json={"jsonrpc": "2.0", "id": body["id"], "result": {}})  # fmt: skip
        assert req.headers["Mcp-Session-Id"] == "s1"
        if "id" not in body:
            return httpx.Response(202)
        name, args = body["params"]["name"], body["params"]["arguments"]
        calls.append(name)
        text = json.dumps(tools[name](args))
        msg = {"jsonrpc": "2.0", "id": body["id"],
               "result": {"content": [{"type": "text", "text": text}]}}  # fmt: skip
        return httpx.Response(200, text=f"id: 0\ndata: {json.dumps(msg)}\n\n")

    return httpx.MockTransport(handler)


def search(args):
    return {"data": {"recordings": {"nodes": [{"recording": REC}]}}}


def test_epidemic_search_normalizes():
    calls = []
    es = epidemic.Epidemic("k", transport=mcp({"SearchRecordings": search}, calls))
    t = es.search("rise", vocals=False)[0]
    assert t["mainArtists"] == ["Ana"] and t["length"] == 90000
    assert t["moods"] == [{"name": "hopeful"}]


def test_epidemic_fetch_with_stems(tmp_path: Path, monkeypatch):
    def download(args):
        return {
            "data": {
                "recordingDownload": {
                    "assetUrl": f"https://cdn/{args['options']['stemType']}.wav?sig=1"
                }
            }
        }

    monkeypatch.setattr(epidemic.http, "download", lambda url, dst: dst.write_bytes(
        url.encode()) and dst)  # fmt: skip
    calls = []
    es = epidemic.Epidemic("k", transport=mcp({"SearchRecordings": search,
                                               "DownloadRecording": download}, calls))  # fmt: skip
    cues = epidemic.fetch(Library(tmp_path), es, "abc", stems=True)
    assert [c.stem for c in cues] == [None, "bass", "drums", "instruments"]
    assert cues[0].license == "epidemic:creator" and cues[0].url == "https://cdn/FULL.wav"
    assert cues[0].credit == 'Music: "Rise" by Ana (Epidemic Sound)'


def test_epidemic_adapt_polls_and_retries(monkeypatch):
    monkeypatch.setattr(epidemic.time, "sleep", lambda s: None)
    polls = iter([{"id": "j", "status": "IN_PROGRESS"},
                  {"id": "j", "status": "COMPLETED", "edit": {"id": "e"}}])  # fmt: skip
    busy = iter([True, False])

    def edit_dl(args):
        if next(busy):
            return {"errors": [{"message": "busy", "extensions": {"code": "RETRY_LATER"}}]}
        assert args == {"input": {"jobId": "j", "editId": "e"}}
        return {"data": {"recordingEditDownload": {"assetUrl": "https://cdn/a.wav"}}}

    tools = {
        "EditRecording": lambda a: {"data": {"recordingEdit": {"id": "j", "status": "PENDING"}}},
        "PollEditRecordingJob": lambda a: {"data": {"recordingEditJob": next(polls)}},
        "DownloadRecordingEdit": edit_dl,
    }
    calls = []
    es = epidemic.Epidemic("k", transport=mcp(tools, calls))
    assert es.adapt("abc", 30) == "https://cdn/a.wav"
    assert calls.count("PollEditRecordingJob") == 2 and calls.count("DownloadRecordingEdit") == 2


def test_epidemic_errors_raise():
    tools = {"SearchRecordings": lambda a: {"data": {"recordings": {"nodes": []}}}}
    es = epidemic.Epidemic("k", transport=mcp(tools, []))
    with pytest.raises(epidemic.EpidemicError, match="NOT_FOUND"):
        es.metadata("nope")


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
