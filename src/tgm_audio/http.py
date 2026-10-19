"""httpx clients on the OS trust store (the work network's TLS inspection breaks certifi)."""

import os
import ssl
from pathlib import Path

import httpx
import truststore


def client(base_url: str, headers: dict[str, str], **kw) -> httpx.Client:
    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return httpx.Client(base_url=base_url, headers=headers, verify=ctx, timeout=60, **kw)


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set; add it to .env (see .env.example)")
    return value


def download(url: str, dst: Path) -> Path:
    """Fetch a pre-signed URL (no API auth header) to `dst`."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with client("", {}).stream("GET", url, follow_redirects=True) as r:
        r.raise_for_status()
        with dst.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    return dst
