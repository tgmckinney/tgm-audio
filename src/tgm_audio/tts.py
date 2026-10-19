"""ElevenLabs TTS with cloned voices.

Voices are named in `voices.toml` (repo root or $TGM_AUDIO_VOICES):

    [voices.tom]
    voice_id = "…"
    model = "eleven_multilingual_v2"
    consent = "self"

A clone of anyone else needs their recorded consent noted in `consent`; `say()` refuses a voice
without it. Key: ELEVENLABS_API_KEY.
"""

import os
import tempfile
import tomllib
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from tgm_audio import http
from tgm_audio.ledger import Cue, Library

BASE = "https://api.elevenlabs.io"


class VoiceSettings(BaseModel):
    stability: float = Field(default=0.5, ge=0, le=1)
    similarity_boost: float = Field(default=0.75, ge=0, le=1)
    style: float = Field(default=0.0, ge=0, le=1)
    speed: float = Field(default=1.0, ge=0.7, le=1.2)


class Voice(BaseModel):
    voice_id: str
    model: str = "eleven_multilingual_v2"
    consent: str = Field(min_length=1, description='"self" or who consented, when and how')
    settings: VoiceSettings = VoiceSettings()


def voices_path() -> Path:
    return Path(os.environ.get("TGM_AUDIO_VOICES", "voices.toml"))


def load_voices(path: Path | None = None) -> dict[str, Voice]:
    path = path or voices_path()
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    return {k: Voice(**v) for k, v in data.get("voices", {}).items()}


class ElevenLabs:
    def __init__(self, key: str | None = None, transport: httpx.BaseTransport | None = None):
        headers = {"xi-api-key": key or http.require_env("ELEVENLABS_API_KEY")}
        if transport:
            self.http = httpx.Client(base_url=BASE, headers=headers, transport=transport)
        else:
            self.http = http.client(BASE, headers)

    def speak(self, text: str, voice: Voice, fmt: str = "mp3_44100_128") -> bytes:
        r = self.http.post(
            f"/v1/text-to-speech/{voice.voice_id}",
            params={"output_format": fmt},
            json={
                "text": text,
                "model_id": voice.model,
                "voice_settings": voice.settings.model_dump(),
            },
        )
        r.raise_for_status()
        return r.content

    def clone(self, name: str, samples: list[Path], description: str = "") -> str:
        """Instant voice clone from sample recordings; returns the new voice_id."""
        files = [("files", (p.name, p.read_bytes())) for p in samples]
        r = self.http.post("/v1/voices/add", data={"name": name, "description": description},
                           files=files)  # fmt: skip
        r.raise_for_status()
        return r.json()["voice_id"]

    def voices(self) -> list[dict]:
        r = self.http.get("/v1/voices")
        r.raise_for_status()
        return r.json()["voices"]


def say(lib: Library, el: ElevenLabs, voice_name: str, text: str) -> Cue:
    """Synthesize `text` in a named voice and freeze it into the library."""
    voices = load_voices()
    if voice_name not in voices:
        raise KeyError(f"voice {voice_name!r} not in {voices_path()}")
    voice = voices[voice_name]
    audio = el.speak(text, voice)
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "tts.mp3"
        f.write_bytes(audio)
        return lib.add(f, kind="tts", source="elevenlabs", license="elevenlabs", title=text[:60],
                       source_id=voice.voice_id, note=f"voice={voice_name} model={voice.model}",
                       meta={"text": text, "characters": len(text)})  # fmt: skip
