"""Epidemic Sound via its MCP service (works on a personal subscription): search, download with
stems, exact-length edits (Adapt).

The key is an account API key (epidemicsound.com/account/api-keys, valid one year) in
EPIDEMIC_API_KEY. The Partner Content API rejects these keys; it needs a partner app. Tools are
called over MCP streamable HTTP (JSON-RPC; responses may be SSE). Each tool returns GraphQL JSON
as text. Downloaded files are frozen into the library with licence `epidemic:<EPIDEMIC_PLAN>`
(default `creator`).
"""

import json
import os
import re
import tempfile
import time
from pathlib import Path

import httpx

from tgm_audio import http
from tgm_audio.ledger import Cue, Library

MCP_URL = "https://www.epidemicsound.com/a/mcp-service/mcp"
STEMS = ("BASS", "DRUMS", "INSTRUMENTS")  # downloadable stems besides FULL


class EpidemicError(RuntimeError):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(f"{message} ({code})" if code else message)
        self.code = code


class Epidemic:
    def __init__(self, token: str | None = None, transport: httpx.BaseTransport | None = None):
        token = (token or http.require_env("EPIDEMIC_API_KEY")).strip()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        }
        if transport:
            self.http = httpx.Client(headers=headers, transport=transport)
        else:
            self.http = http.client("", headers)
        self.http.timeout = httpx.Timeout(120)
        self.session: str | None = None
        self._id = 0

    def _post(self, body: dict) -> httpx.Response:
        headers = {"Mcp-Session-Id": self.session} if self.session else {}
        r = self.http.post(MCP_URL, json=body, headers=headers)
        if r.status_code == 401:
            raise EpidemicError("Epidemic rejected the API key (expired or not an account key)")
        r.raise_for_status()
        self.session = r.headers.get("mcp-session-id", self.session)
        return r

    def _rpc(self, method: str, params: dict) -> dict:
        self._id += 1
        r = self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        events = re.findall(r"^data: (\{.*)$", r.text, re.MULTILINE)
        msg = json.loads(events[-1]) if events else r.json()
        if "error" in msg:
            raise EpidemicError(msg["error"].get("message", "MCP error"))
        return msg["result"]

    def _connect(self) -> None:
        if self.session is None:
            info = {"name": "tgm-audio", "version": "0.1"}
            self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": info})  # fmt: skip
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, tool: str, args: dict, retries: int = 2) -> dict:
        """Call a tool; returns the GraphQL `data` value (its single field unwrapped)."""
        self._connect()
        res = self._rpc("tools/call", {"name": tool, "arguments": args})
        text = "".join(c.get("text", "") for c in res.get("content", []))
        payload = json.loads(text) if text.startswith("{") else {"errors": [{"message": text}]}
        if payload.get("errors"):
            e = payload["errors"][0]
            ext = e.get("extensions", {})
            if ext.get("code") == "RETRY_LATER" and retries:
                time.sleep(min(float(ext.get("retryAfterSeconds") or 5), 60))
                return self.call(tool, args, retries - 1)
            raise EpidemicError(f"{tool}: {e.get('message')}", ext.get("code"))
        data = payload["data"]
        return next(iter(data.values())) if len(data) == 1 else data

    def search(
        self,
        term: str | None = None,
        *,
        bpm_min: int | None = None,
        bpm_max: int | None = None,
        vocals: bool | None = None,
        max_seconds: float | None = None,
        limit: int = 20,
    ) -> list[dict]:
        flt: dict = {}
        if bpm_min or bpm_max:
            flt["bpm"] = {"min": bpm_min, "max": bpm_max}
        if vocals is not None:
            flt["vocals"] = vocals
        if max_seconds:
            flt["duration"] = {"max": int(max_seconds * 1000)}
        args: dict = {"first": limit, "filter": flt or None}
        if term:
            args["query"] = {"term": term}
        return [_track(n["recording"]) for n in self.call("SearchRecordings", args)["nodes"]]

    def metadata(self, track_id: str) -> dict:
        res = self.call("SearchRecordings", {"first": 1, "filter": {"recordingIDs": [track_id]}})
        if not res["nodes"]:
            raise EpidemicError(f"track {track_id} not found", "NOT_FOUND")
        return _track(res["nodes"][0]["recording"])

    def download_url(self, track_id: str, stem: str = "FULL", file_type: str = "WAV") -> str:
        opts = {"fileType": file_type, "stemType": stem}
        return self.call("DownloadRecording", {"id": track_id, "options": opts})["assetUrl"]

    def adapt(self, track_id: str, seconds: float, timeout: float = 300) -> str:
        """Exact-length edit of a track (max 300 s); returns the high-quality WAV url."""
        inp = {"targetDurationMs": int(seconds * 1000), "downloadAudioFormat": "WAV",
               "forceDuration": True, "skipStems": True, "maxResults": 1}  # fmt: skip
        job = self.call("EditRecording", {"id": track_id, "input": inp})
        deadline = time.monotonic() + timeout
        while job["status"] not in ("COMPLETED", "FAILED"):
            if time.monotonic() > deadline:
                raise TimeoutError(f"edit job {job['id']} still {job['status']}")
            time.sleep(3)
            job = self.call("PollEditRecordingJob", {"id": job["id"]})
        edit = (job.get("edits") or [job.get("edit")])[0]
        if job["status"] == "FAILED" or not edit:
            raise EpidemicError(f"edit job {job['id']} failed")
        ids = {"jobId": job["id"], "editId": edit["id"]}
        return self.call("DownloadRecordingEdit", {"input": ids})["assetUrl"]


def _track(rec: dict) -> dict:
    """Normalize a Recording to a flat dict (id, title, mainArtists, bpm, length ms, moods…)."""
    tags = rec.get("tags", [])
    return {
        "id": rec["id"],
        "title": rec["title"],
        "mainArtists": [c["artist"]["name"] for c in rec["credits"] if c["role"] == "MAIN_ARTIST"],
        "bpm": rec.get("bpm"),
        "length": rec["audioFile"]["durationInMilliseconds"],
        "moods": [{"name": t["displayName"]} for t in tags if t["dimension"]["name"] == "mood"],
        "genres": [t["displayName"] for t in tags if t["dimension"]["name"] == "genre"],
        "preview": rec["audioFile"]["lqmp3Url"],
        "stems": [s["type"] for s in rec.get("stems", [])],
    }


def _license() -> str:
    return f"epidemic:{os.environ.get('EPIDEMIC_PLAN', 'creator')}"


def _credit(meta: dict) -> str:
    return f'Music: "{meta["title"]}" by {", ".join(meta["mainArtists"])} (Epidemic Sound)'


def fetch(
    lib: Library,
    es: Epidemic,
    track_id: str,
    *,
    seconds: float | None = None,
    stems: bool = False,
) -> list[Cue]:
    """Download a track (or an exact-length edit of `seconds`, or with stems) into the library."""
    meta = es.metadata(track_id)
    common = {
        "kind": "music",
        "source": "epidemic",
        "license": _license(),
        "source_id": track_id,
        "title": meta["title"],
        "artist": ", ".join(meta["mainArtists"]),
        "credit": _credit(meta),
        "meta": {k: meta.get(k) for k in ("bpm", "length", "moods", "genres")},
    }
    if seconds is not None:
        targets = [(es.adapt(track_id, seconds), None)]
        common["note"] = f"Adapt {seconds:g}s"
    else:
        targets = [(es.download_url(track_id), None)]
        targets += [(es.download_url(track_id, s), s.lower()) for s in STEMS if stems]
    cues = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, (url, stem) in enumerate(targets):
            f = http.download(url, Path(tmp) / f"{i}.wav")
            cues.append(lib.add(f, stem=stem, url=url.split("?")[0], **common))
    return cues
