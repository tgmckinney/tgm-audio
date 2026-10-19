"""Content-addressed audio library with a licence ledger.

Every file the pipeline uses is frozen into the library (`files/<sha[:2]>/<sha><ext>`) with a
`Cue` record; every placement in a published item is a `Use`. `check()` gates publishing:
each cue's licence must exist in the policy (`licenses.toml`) and cover the target channel.
Records are append-only JSON lines, so the factory can ingest them as Asset rows later.
"""

import hashlib
import os
import shutil
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["music", "sfx", "voice", "tts", "mix"]
Source = Literal["epidemic", "freesound", "elevenlabs", "lyria", "own"]


def default_root() -> Path:
    return Path(os.environ.get("TGM_AUDIO_LIBRARY", "~/.local/share/tgm-audio")).expanduser()


def _now() -> datetime:
    return datetime.now(UTC)


class Cue(BaseModel):
    id: str = Field(description="sha256 of the file; identical files share one cue")
    kind: Kind
    source: Source
    license: str = Field(description='Policy key, e.g. "epidemic:creator", "cc0", "own"')
    file: str = Field(description="Path relative to the library root")
    source_id: str | None = None
    title: str | None = None
    artist: str | None = None
    credit: str | None = None
    url: str | None = None
    stem: str | None = None
    note: str | None = None
    meta: dict = {}
    added_at: datetime = Field(default_factory=_now)


class Use(BaseModel):
    cue_id: str
    item: str = Field(description='Factory item, e.g. "inflation/short/ap-eggs"')
    channel: str = Field(description='Where it publishes, e.g. "youtube:@channel"')
    start: float | None = None
    end: float | None = None
    used_at: datetime = Field(default_factory=_now)


class License(BaseModel):
    channels: list[str] = Field(description='Allowed channels; "*" allows any')
    credit_required: bool = False
    note: str = ""


def load_policy(path: Path) -> dict[str, License]:
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    return {k: License(**v) for k, v in data.get("licenses", {}).items()}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Library:
    def __init__(self, root: Path | None = None):
        self.root = root or default_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.cues_path = self.root / "cues.jsonl"
        self.uses_path = self.root / "uses.jsonl"
        self.policy_path = self.root / "licenses.toml"

    def cues(self) -> dict[str, Cue]:
        if not self.cues_path.exists():
            return {}
        rows = (Cue.model_validate_json(line) for line in self.cues_path.read_text().splitlines())
        return {c.id: c for c in rows}  # later records win (metadata updates)

    def uses(self, item: str | None = None) -> list[Use]:
        if not self.uses_path.exists():
            return []
        rows = [Use.model_validate_json(line) for line in self.uses_path.read_text().splitlines()]
        return [u for u in rows if item is None or u.item == item]

    def path(self, cue: Cue | str) -> Path:
        cue = cue if isinstance(cue, Cue) else self.cues()[cue]
        return self.root / cue.file

    def add(self, src: Path, *, kind: Kind, source: Source, license: str, **meta) -> Cue:
        """Freeze `src` into the library and record it; re-adding the same file updates metadata."""
        digest = sha256(src)
        rel = Path("files") / digest[:2] / f"{digest}{src.suffix.lower()}"
        dst = self.root / rel
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        cue = Cue(id=digest, kind=kind, source=source, license=license, file=str(rel), **meta)
        with self.cues_path.open("a") as f:
            f.write(cue.model_dump_json() + "\n")
        return cue

    def use(self, cue_id: str, item: str, channel: str, **span) -> Use:
        if cue_id not in self.cues():
            raise KeyError(f"unknown cue {cue_id}")
        u = Use(cue_id=cue_id, item=item, channel=channel, **span)
        with self.uses_path.open("a") as f:
            f.write(u.model_dump_json() + "\n")
        return u

    def check(self, item: str) -> list[str]:
        """Problems blocking publication of `item`; empty means cleared."""
        policy, cues, problems = load_policy(self.policy_path), self.cues(), []
        for u in self.uses(item):
            cue = cues.get(u.cue_id)
            if cue is None:
                problems.append(f"{u.cue_id[:12]}: no cue record")
                continue
            name = cue.title or cue.id[:12]
            lic = policy.get(cue.license)
            if lic is None:
                problems.append(f"{name}: licence {cue.license!r} not in {self.policy_path}")
            elif "*" not in lic.channels and u.channel not in lic.channels:
                problems.append(f"{name}: {cue.license} does not cover {u.channel}")
            elif lic.credit_required and not cue.credit:
                problems.append(f"{name}: {cue.license} requires a credit line")
        return problems

    def credits(self, item: str) -> list[str]:
        cues = self.cues()
        lines = (cues[u.cue_id].credit for u in self.uses(item) if u.cue_id in cues)
        return list(dict.fromkeys(c for c in lines if c))
