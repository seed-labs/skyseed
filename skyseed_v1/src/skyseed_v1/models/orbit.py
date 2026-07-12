#!/usr/bin/env python3
# encoding: utf-8

"""Orbit data sources, caching, and TLE normalization models."""

from __future__ import annotations

import hashlib
import html
import json
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence

import requests


DEFAULT_STARLINK_GP_TLE_URL = (
    "https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=tle"
)
DEFAULT_STARLINK_TABLE_URL = "https://celestrak.org/NORAD/ELEMENTS/table.php?GROUP=starlink"
DEFAULT_HTTP_HEADERS = {
    "User-Agent": "Skyseed-StarlinkPreview/1.0 (+https://github.com/seed-labs/skyseed)",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_rendered(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _http_get_text(
    url: str,
    *,
    timeout_seconds: float,
    max_attempts: int = 3,
    headers: Optional[Dict[str, str]] = None,
) -> str:
    last_error: Exception | None = None
    request_headers = dict(DEFAULT_HTTP_HEADERS)
    if headers:
        request_headers.update(headers)
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        try:
            response = requests.get(
                url,
                timeout=timeout_seconds,
                headers=request_headers,
            )
            response.raise_for_status()
            return response.text
        except Exception as exc:
            last_error = exc
            if attempt < max_attempts:
                time.sleep(1.0 * attempt)
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"failed to fetch text from {url}")


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_orbit_cache_artifact(
    *,
    cache_path: Path,
    manifest_path: Path,
    source_name: str,
    source_url: Optional[str],
    orbit_format: str,
    raw_text: str,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> OrbitDataArtifact:
    parse_result = parse_tle_catalog(raw_text)
    downloaded_at = _utc_now()
    metadata = {
        "source_name": source_name,
        "source_url": source_url,
        "download_timestamp_utc": _utc_rendered(downloaded_at),
        "format": orbit_format,
        "object_count": len(parse_result.records),
        "malformed_record_count": len(parse_result.malformed_records),
        "sha256": _sha256_text(raw_text),
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(raw_text, encoding="utf-8")
    _write_json(manifest_path, metadata)
    return OrbitDataArtifact(
        source_name=source_name,
        source_url=source_url,
        orbit_format=orbit_format,
        fetched_at_utc=metadata["download_timestamp_utc"],
        cache_path=str(cache_path),
        manifest_path=str(manifest_path),
        sha256=metadata["sha256"],
        raw_text=raw_text,
        metadata=metadata,
    )


def _parse_tle_epoch(epoch_token: str) -> datetime:
    stripped = epoch_token.strip()
    if len(stripped) < 5:
        raise ValueError(f"invalid TLE epoch token: {epoch_token!r}")
    short_year = int(stripped[:2])
    year = 1900 + short_year if short_year >= 57 else 2000 + short_year
    day_of_year = float(stripped[2:])
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    return start + timedelta(days=day_of_year - 1.0)


@dataclass(frozen=True)
class OrbitDataArtifact:
    source_name: str
    source_url: Optional[str]
    orbit_format: str
    fetched_at_utc: Optional[str]
    cache_path: str
    manifest_path: Optional[str]
    sha256: str
    raw_text: str
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParsedTleRecord:
    satellite_name: str
    norad_id: int
    epoch_utc: str
    inclination_deg: float
    eccentricity: float
    mean_motion_rev_per_day: float
    raan_deg: float
    argument_of_perigee_deg: float
    mean_anomaly_deg: float
    line1: str
    line2: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TleMalformedRecord:
    record_index: int
    lines: Sequence[str]
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_index": self.record_index,
            "lines": list(self.lines),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TleCatalogParseResult:
    records: Sequence[ParsedTleRecord]
    malformed_records: Sequence[TleMalformedRecord]
    summary: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "records": [record.to_dict() for record in self.records],
            "malformed_records": [
                record.to_dict() for record in self.malformed_records
            ],
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class StarlinkCatalogTableRecord:
    international_designator: str
    norad_id: int
    satellite_name: str
    period_minutes: float
    inclination_deg: float
    apogee_height_km: float
    perigee_height_km: float
    eccentricity: float
    gp_age_days: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StarlinkCatalogTableParseResult:
    records: Sequence[StarlinkCatalogTableRecord]
    current_as_of_utc: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "records": [record.to_dict() for record in self.records],
            "current_as_of_utc": self.current_as_of_utc,
        }


class OrbitDataSource(ABC):
    """Boundary for real or cached orbit catalog inputs."""

    source_name: str
    orbit_format: str

    @abstractmethod
    def materialize(self, *, refresh: bool = False) -> OrbitDataArtifact:
        """Return the currently selected orbit data artifact."""


class LocalOrbitDataSource(OrbitDataSource):
    source_name = "local_orbit_data_source"

    def __init__(
        self,
        *,
        orbit_path: Path,
        orbit_format: str = "tle",
        manifest_path: Optional[Path] = None,
    ) -> None:
        self.orbit_path = orbit_path
        self.orbit_format = orbit_format
        self.manifest_path = manifest_path

    def materialize(self, *, refresh: bool = False) -> OrbitDataArtifact:
        if refresh:
            raise ValueError("refresh is unsupported for LocalOrbitDataSource")
        if not self.orbit_path.exists():
            raise FileNotFoundError(f"orbit data file does not exist: {self.orbit_path}")
        raw_text = self.orbit_path.read_text(encoding="utf-8")
        metadata = (
            _load_json(self.manifest_path)
            if self.manifest_path is not None and self.manifest_path.exists()
            else {}
        )
        return OrbitDataArtifact(
            source_name=str(metadata.get("source_name", self.source_name)),
            source_url=metadata.get("source_url"),
            orbit_format=str(metadata.get("format", self.orbit_format)),
            fetched_at_utc=metadata.get("download_timestamp_utc"),
            cache_path=str(self.orbit_path),
            manifest_path=str(self.manifest_path) if self.manifest_path is not None else None,
            sha256=_sha256_text(raw_text),
            raw_text=raw_text,
            metadata=metadata,
        )


class CelesTrakGpTleOrbitDataSource(OrbitDataSource):
    source_name = "celestrak_gp_tle_orbit_data_source"
    orbit_format = "tle"

    def __init__(
        self,
        *,
        source_url: str = DEFAULT_STARLINK_GP_TLE_URL,
        cache_path: Path,
        manifest_path: Path,
        timeout_seconds: float = 30.0,
        max_attempts: int = 3,
    ) -> None:
        self.source_url = source_url
        self.cache_path = cache_path
        self.manifest_path = manifest_path
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, int(max_attempts))

    def _write_cache(self, raw_text: str) -> OrbitDataArtifact:
        return write_orbit_cache_artifact(
            cache_path=self.cache_path,
            manifest_path=self.manifest_path,
            source_name=self.source_name,
            source_url=self.source_url,
            orbit_format=self.orbit_format,
            raw_text=raw_text,
        )

    def materialize(self, *, refresh: bool = False) -> OrbitDataArtifact:
        if self.cache_path.exists() and self.manifest_path.exists() and not refresh:
            return LocalOrbitDataSource(
                orbit_path=self.cache_path,
                orbit_format=self.orbit_format,
                manifest_path=self.manifest_path,
            ).materialize()

        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._write_cache(
                    _http_get_text(
                        self.source_url,
                        timeout_seconds=self.timeout_seconds,
                        max_attempts=1,
                    )
                )
            except Exception as exc:
                last_error = exc
                if attempt < self.max_attempts:
                    time.sleep(1.0 * attempt)
        if last_error is not None:
            raise last_error
        raise RuntimeError("failed to materialize CelesTrak GP/TLE data")


def parse_tle_catalog(raw_text: str) -> TleCatalogParseResult:
    lines = [line.rstrip() for line in raw_text.splitlines() if line.strip()]
    records: List[ParsedTleRecord] = []
    malformed_records: List[TleMalformedRecord] = []

    index = 0
    record_index = 0
    while index < len(lines):
        record_index += 1
        chunk = lines[index:index + 3]
        if len(chunk) != 3:
            malformed_records.append(
                TleMalformedRecord(
                    record_index=record_index,
                    lines=chunk,
                    reason="expected_three_line_tle_record",
                )
            )
            break
        name_line, line1, line2 = chunk
        index += 3
        try:
            if not line1.startswith("1 ") or not line2.startswith("2 "):
                raise ValueError("invalid_tle_line_prefix")
            norad_id = int(line1[2:7])
            if int(line2[2:7]) != norad_id:
                raise ValueError("norad_id_mismatch_between_lines")
            epoch_utc = _utc_rendered(_parse_tle_epoch(line1[18:32]))
            records.append(
                ParsedTleRecord(
                    satellite_name=name_line.strip(),
                    norad_id=norad_id,
                    epoch_utc=epoch_utc,
                    inclination_deg=float(line2[8:16]),
                    raan_deg=float(line2[17:25]),
                    eccentricity=float(f"0.{line2[26:33].strip()}"),
                    argument_of_perigee_deg=float(line2[34:42]),
                    mean_anomaly_deg=float(line2[43:51]),
                    mean_motion_rev_per_day=float(line2[52:63]),
                    line1=line1,
                    line2=line2,
                )
            )
        except Exception as exc:
            malformed_records.append(
                TleMalformedRecord(
                    record_index=record_index,
                    lines=chunk,
                    reason=str(exc),
                )
            )

    epochs = [datetime.fromisoformat(record.epoch_utc.replace("Z", "+00:00")) for record in records]
    inclinations = [record.inclination_deg for record in records]
    mean_motions = [record.mean_motion_rev_per_day for record in records]
    norad_ids = [record.norad_id for record in records]

    summary = {
        "record_count": len(records),
        "malformed_record_count": len(malformed_records),
        "norad_id_min": min(norad_ids) if norad_ids else None,
        "norad_id_max": max(norad_ids) if norad_ids else None,
        "inclination_deg_min": round(min(inclinations), 6) if inclinations else None,
        "inclination_deg_max": round(max(inclinations), 6) if inclinations else None,
        "mean_motion_rev_per_day_min": (
            round(min(mean_motions), 9) if mean_motions else None
        ),
        "mean_motion_rev_per_day_max": (
            round(max(mean_motions), 9) if mean_motions else None
        ),
        "epoch_utc_min": _utc_rendered(min(epochs)) if epochs else None,
        "epoch_utc_max": _utc_rendered(max(epochs)) if epochs else None,
    }
    return TleCatalogParseResult(
        records=tuple(records),
        malformed_records=tuple(malformed_records),
        summary=summary,
    )


def fetch_starlink_catalog_table_html(
    *,
    source_url: str = DEFAULT_STARLINK_TABLE_URL,
    timeout_seconds: float = 30.0,
    max_attempts: int = 3,
) -> str:
    return _http_get_text(
        source_url,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
    )


def _strip_html_tags(value: str) -> str:
    cleaned = re.sub(r"<[^>]+>", "", value)
    return html.unescape(cleaned).replace("\xa0", " ").strip()


def parse_starlink_catalog_table(html_text: str) -> StarlinkCatalogTableParseResult:
    current_match = re.search(
        r"Current as of ([0-9]{4} [A-Za-z]{3} [0-9]{2} [0-9:]{8} UTC)",
        html_text,
    )
    row_matches = re.findall(
        r"<tr align=center>(.*?)</tr>",
        html_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    records: List[StarlinkCatalogTableRecord] = []
    for row in row_matches:
        cells = re.findall(
            r"<td\b[^>]*\bclass=(?:\"small\"|'small'|small)\b[^>]*>(.*?)</td>",
            row,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if len(cells) < 10:
            continue
        cell_values = [_strip_html_tags(cell) for cell in cells]
        try:
            records.append(
                StarlinkCatalogTableRecord(
                    international_designator=cell_values[0].split(" ")[0],
                    norad_id=int(cell_values[1]),
                    satellite_name=cell_values[2].split(" satellite")[0].strip(),
                    period_minutes=float(cell_values[3]),
                    inclination_deg=float(cell_values[4]),
                    apogee_height_km=float(cell_values[5]),
                    perigee_height_km=float(cell_values[6]),
                    eccentricity=float(cell_values[7]),
                    gp_age_days=float(cell_values[9]),
                )
            )
        except Exception:
            continue
    current_as_of_utc = None
    if current_match is not None:
        current_as_of_utc = current_match.group(1)
    return StarlinkCatalogTableParseResult(
        records=tuple(records),
        current_as_of_utc=current_as_of_utc,
    )


def fetch_tle_for_catnr(
    *,
    norad_id: int,
    timeout_seconds: float = 30.0,
    max_attempts: int = 3,
) -> ParsedTleRecord:
    url = f"https://celestrak.org/NORAD/elements/gp.php?CATNR={int(norad_id)}&FORMAT=tle"
    parse_result = parse_tle_catalog(
        _http_get_text(
            url,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
        )
    )
    if not parse_result.records:
        raise RuntimeError(f"no TLE records were returned for CATNR {norad_id}")
    return parse_result.records[0]
