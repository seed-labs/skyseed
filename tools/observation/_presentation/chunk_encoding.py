"""Deterministic, exact JSON encoding for lazily loaded Tool chunks."""

from __future__ import annotations

from base64 import b64decode, b64encode
import gzip
from hashlib import sha256
import json
from typing import Any


CHUNK_ENCODING = "gzip-base64-json"


def encode_json_chunk(value: object) -> dict[str, object]:
    """Return a deterministic compressed envelope for one exact JSON value."""

    raw = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=6, mtime=0)
    return {
        "encoding": CHUNK_ENCODING,
        "uncompressed_bytes": len(raw),
        "sha256": sha256(raw).hexdigest(),
        "data": b64encode(compressed).decode("ascii"),
    }


def decode_json_chunk(value: Any) -> Any:
    """Decode and verify an envelope, while accepting legacy plain JSON."""

    if not isinstance(value, dict) or value.get("encoding") != CHUNK_ENCODING:
        return value
    encoded = value.get("data")
    expected_bytes = value.get("uncompressed_bytes")
    expected_sha256 = value.get("sha256")
    if (
        not isinstance(encoded, str)
        or not isinstance(expected_bytes, int)
        or expected_bytes < 0
        or not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
    ):
        raise ValueError("compressed Tool chunk envelope is invalid")
    try:
        raw = gzip.decompress(b64decode(encoded, validate=True))
    except (OSError, ValueError) as error:
        raise ValueError("compressed Tool chunk payload is invalid") from error
    if len(raw) != expected_bytes or sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("compressed Tool chunk exact-payload audit failed")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("compressed Tool chunk JSON is invalid") from error


def encoded_chunk_json(value: object) -> str:
    """Serialize one compressed envelope for assignment inside a script."""

    return json.dumps(
        encode_json_chunk(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).replace("</", "<\\/")


__all__ = [
    "CHUNK_ENCODING",
    "decode_json_chunk",
    "encode_json_chunk",
    "encoded_chunk_json",
]
