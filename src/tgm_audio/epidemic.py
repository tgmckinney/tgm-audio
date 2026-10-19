"""Epidemic Sound Partner Content API: search, metadata, beats, download (+stems), Adapt lengths.

Auth is a bearer token in EPIDEMIC_API_KEY (`epidemic_live_…`). Downloaded files are frozen into
the library with licence `epidemic:<EPIDEMIC_PLAN>` (default `creator`).
"""

import os
import tempfile
import time
from pathlib import Path

import httpx

from tgm_audio import http
from tgm_audio.ledger import Cue, Library

BASE = "https://partner-content-api.epidemicsound.com"


class Epidemic:
    def __init__(self, token: str | None = None, transport: httpx.BaseTransport | None = None):
        token = token or http.require_env("EPIDEMIC_API_KEY")
        headers = {"Authorization": f"Bearer {token}"}
        if transport:
            self.http = httpx.Client(base_url=BASE, headers=headers, transport=transport)
        else:
            self.http = http.client(BASE, headers)

    def _get(self, path: str, **params) -> dict | list:
        params = {k: v for k, v in params.items() if v is not None}
        r = self.http.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def search(
        self,
        term: str | None = None,
        *,
        mood: str | None = None,
        genre: str | None = None,
        bpm_min: int | None = None,
        bpm_max: int | None = None,
        vocals: bool | None = None,
        limit: int = 20,
    ) -> list[dict]:
        vocal = None if vocals is None else (["LEAD", "PRESENCE"] if vocals else ["NONE"])
        res = self._get("/v0/tracks/search", term=term, mood=mood, genre=genre, bpmMin=bpm_min,
                        bpmMax=bpm_max, vocalType=vocal, limit=limit)  # fmt: skip
        return res["tracks"]

    def metadata(self, track_id: str) -> dict:
        return self._get("/v0/tracks/metadata", trackId=[track_id])[0]

    def beats(self, track_id: str) -> list[dict]:
        return self._get(f"/v0/tracks/{track_id}/beats")["beats"]

    def download_urls(self, track_id: str, stems: bool = False) -> dict:
        return self._get(f"/v0/tracks/{track_id}/download", quality="high", format="mp3",
                         expand="stems" if stems else None)  # fmt: skip

    def adapt(self, track_id: str, seconds: float, timeout: float = 300) -> str:
        """Start an Adapt (track versions) job for an exact length; returns the WAV url."""
        r = self.http.post("/v0/tracks/versions",
                           json={"trackId": track_id, "durationMs": int(seconds * 1000)})  # fmt: skip
        r.raise_for_status()
        job, deadline = r.json(), time.monotonic() + timeout
        while job["status"] not in ("COMPLETED", "FAILED"):
            if time.monotonic() > deadline:
                raise TimeoutError(f"Adapt job {job['jobId']} still {job['status']}")
            time.sleep(3)
            job = self._get(f"/v0/tracks/versions/{job['jobId']}")
        if job["status"] == "FAILED" or not job["results"]:
            raise RuntimeError(f"Adapt job failed: {job.get('message')}")
        return job["results"][0]["highQualityUrl"]


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
    """Download a track (or an Adapt version of `seconds`, or its stems) into the library."""
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
        targets = [(es.adapt(track_id, seconds), None, ".wav")]
        common["note"] = f"Adapt {seconds:g}s"
    else:
        urls = es.download_urls(track_id, stems)
        targets = [(urls["url"], None, ".mp3")]
        targets += [(s["url"], s["type"], ".mp3") for s in urls.get("stems", [])]
    cues = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, (url, stem, ext) in enumerate(targets):
            f = http.download(url, Path(tmp) / f"{i}{ext}")
            cues.append(lib.add(f, stem=stem, url=url.split("?")[0], **common))
    return cues
