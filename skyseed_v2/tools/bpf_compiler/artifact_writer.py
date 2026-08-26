"""Write a compact Compiler bootstrap plus paged exact evidence chunks."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from tools._html import write_json, write_text
from tools._chunk_encoding import CHUNK_ENCODING, encoded_chunk_json


_CHUNK_ROWS = 500


def write_bpf_compiler_artifacts(
    data: dict[str, Any],
    page: str,
    directory: Path,
) -> dict[str, object]:
    """Write one deterministic file://-compatible lazy inspection bundle."""

    if not isinstance(page, str):
        raise TypeError("page must be rendered text")
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    _remove_previous_generated_payloads(output)
    chunks_directory = output / "chunks"
    chunks_directory.mkdir(parents=True, exist_ok=True)

    intervals = data["intervals"]
    diffs = data["adjacent_diffs"]
    plan_entries = []
    chunk_paths: list[Path] = []
    for interval, diff in zip(intervals, diffs, strict=True):
        plan_index = int(interval["plan_index"])
        sections = {
            "plan-connections": interval["plan"]["connection_transitions"],
            "plan-parameters": interval["plan"]["parameter_transitions"],
            "plan-forwarding": interval["plan"]["forwarding_transitions"],
            "plan-precedences": interval["plan"]["forwarding_precedences"],
            "intents-resources": interval["compiled"]["resources"],
            "intents-parameters": interval["compiled"]["parameters"],
            "intents-forwarding": interval["compiled"]["forwarding"],
            "intents-zone-edges": interval["compiled"]["zone_edges"],
            "constraints": interval["compiled"]["constraints"],
            "baseline-resources": interval["end_state"]["resources"],
            "baseline-parameters": interval["end_state"]["parameters"],
            "baseline-forwarding": interval["end_state"]["forwarding"],
            "baseline-zone-edges": interval["end_state"]["zone_edges"],
            "baseline-zone-prefixes": interval["end_state"]["zone_prefixes"],
            **{
                f"diff-{family.replace('_', '-')}": rows
                for family, rows in diff["details"].items()
            },
        }
        catalogs = {}
        for section, values in sections.items():
            catalog, paths = _write_section_chunks(
                chunks_directory,
                plan_index,
                section,
                tuple(values),
            )
            catalogs[section] = catalog
            chunk_paths.extend(paths)
        plan_entries.append(
            {
                "plan_index": plan_index,
                "realization_index": plan_index,
                "label": interval["label"],
                "coverage": interval["coverage"],
                "start_slice_id": interval["start_slice_id"],
                "start_timestamp": interval["start_timestamp"],
                "end_slice_id": interval["end_slice_id"],
                "end_timestamp": interval["end_timestamp"],
                "summary": interval["summary"],
                "serialized_bytes": interval["serialized_bytes"],
                "realization_sha256": interval["realization_sha256"],
                "compile_evidence": interval["compile_evidence"],
                "adjacent_diff_summary": interval["adjacent_diff_summary"],
                "sections": catalogs,
            }
        )

    manifest = {
        "format_version": 2,
        "tool": data["tool"],
        "authority": data["authority"],
        "inspection_limits": data["inspection_limits"],
        "inventory": data["inventory"],
        "observed_behavior": data["observed_behavior"],
        "run_summary": data["run_summary"],
        "plans": tuple(plan_entries),
        "chunk_rows": _CHUNK_ROWS,
        "chunk_count": len(chunk_paths),
        "chunk_encoding": CHUNK_ENCODING,
    }
    page_path = output / "index.html"
    manifest_path = output / "manifest.json"
    manifest_script_path = output / "manifest.js"
    summary_path = output / "summary.txt"
    inventory_path = output / "inventory.json"
    checksum_path = output / "checksums.sha256"
    write_text(page_path, page)
    write_json(manifest_path, manifest)
    write_text(
        manifest_script_path,
        "window.__SKYSEED_BPF_COMPILER_MANIFEST__="
        + _script_json(manifest)
        + ";\n",
    )
    inventory = data["inventory"]
    behavior = data["observed_behavior"]
    write_text(
        summary_path,
        "\n".join(
            (
                "status=PASS",
                "tool=bpf-compiler-inspection",
                "authority=non-authoritative-view",
                f"chunk_encoding={CHUNK_ENCODING}",
                *(f"{key}={value}" for key, value in inventory.items()),
                *(f"behavior_{key}={value}" for key, value in behavior.items()),
                f"chunk_count={len(chunk_paths)}",
                "",
            )
        ),
    )
    payload_files = (
        page_path,
        manifest_path,
        manifest_script_path,
        summary_path,
        *chunk_paths,
    )
    write_json(
        inventory_path,
        {
            "format_version": 3,
            "chunk_encoding": CHUNK_ENCODING,
            "files": tuple(_file_record(path, output) for path in payload_files),
        },
    )
    checksum_files = (*payload_files, inventory_path)
    write_text(
        checksum_path,
        "".join(
            f"{_sha256(path)}  {path.relative_to(output).as_posix()}\n"
            for path in checksum_files
        ),
    )
    all_files = (*checksum_files, checksum_path)
    artifact_metrics = _artifact_metrics(
        output,
        page_path,
        manifest_script_path,
        chunk_paths,
        all_files,
    )
    realization_inventory = tuple(
        {
            "plan_index": entry["plan_index"],
            "realization_index": entry["realization_index"],
            "record_count": entry["summary"]["baseline_total"],
            "serialized_bytes": entry["serialized_bytes"]["baseline_total"],
            "sha256": entry["realization_sha256"],
        }
        for entry in plan_entries
    )
    return {
        "status": "PASS",
        "message": (
            f"{inventory['plans']} Plans / "
            f"{inventory['forwarding_intents']} forwarding / "
            f"{inventory['constraints']} constraints"
        ),
        "directory": output,
        "files": tuple(path.relative_to(output).as_posix() for path in all_files),
        "inventory": inventory,
        "observed_behavior": behavior,
        "realization_inventory": realization_inventory,
        "artifact_metrics": artifact_metrics,
    }


def _write_section_chunks(
    directory: Path,
    plan_index: int,
    section: str,
    values: tuple[object, ...],
) -> tuple[tuple[dict[str, object], ...], tuple[Path, ...]]:
    catalogs = []
    paths = []
    for page_index, start in enumerate(range(0, len(values), _CHUNK_ROWS)):
        rows = values[start : start + _CHUNK_ROWS]
        chunk_id = f"p{plan_index:03d}-{section}-{page_index:03d}"
        path = directory / f"{chunk_id}.js"
        payload = {
            "chunk_id": chunk_id,
            "plan_index": plan_index,
            "section": section,
            "page_index": page_index,
            "start_index": start,
            "end_index": start + len(rows),
            "rows": rows,
        }
        write_text(
            path,
            "window.__SKYSEED_BPF_COMPILER_CHUNKS__="
            "window.__SKYSEED_BPF_COMPILER_CHUNKS__||{};"
            f"window.__SKYSEED_BPF_COMPILER_CHUNKS__[{json.dumps(chunk_id)}]="
            + encoded_chunk_json(payload)
            + ";\n",
        )
        catalogs.append(
            {
                "chunk_id": chunk_id,
                "path": f"chunks/{path.name}",
                "start_index": start,
                "end_index": start + len(rows),
                "rows": len(rows),
            }
        )
        paths.append(path)
    return tuple(catalogs), tuple(paths)


def _remove_previous_generated_payloads(output: Path) -> None:
    inventory_path = output / "inventory.json"
    if inventory_path.is_file():
        try:
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            inventory = {}
        for record in inventory.get("files", ()):
            relative = record.get("path") if isinstance(record, dict) else None
            if not isinstance(relative, str):
                continue
            candidate = (output / relative).resolve()
            if candidate.parent == output.resolve() or output.resolve() in (
                candidate.parents
            ):
                candidate.unlink(missing_ok=True)
    (output / "bpf-compiler-inspection.json").unlink(missing_ok=True)


def _script_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).replace("</", "<\\/")


def _artifact_metrics(
    root: Path,
    page: Path,
    manifest_script: Path,
    chunks: list[Path],
    all_files: tuple[Path, ...],
) -> dict[str, object]:
    family_bytes: dict[str, int] = {}
    for path in chunks:
        section = path.stem.split("-", 1)[1].rsplit("-", 1)[0]
        family_bytes[section] = family_bytes.get(section, 0) + path.stat().st_size
    largest = max(chunks, key=lambda value: value.stat().st_size, default=None)
    return {
        "html_bytes": page.stat().st_size,
        "bootstrap_manifest_bytes": manifest_script.stat().st_size,
        "initial_browser_load_bytes": (
            page.stat().st_size + manifest_script.stat().st_size
        ),
        "total_artifact_bytes": sum(path.stat().st_size for path in all_files),
        "chunk_count": len(chunks),
        "largest_chunk": (
            None
            if largest is None
            else {
                "path": largest.relative_to(root).as_posix(),
                "size_bytes": largest.stat().st_size,
            }
        ),
        "chunk_bytes_by_family": dict(sorted(family_bytes.items())),
    }


def _file_record(path: Path, root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["write_bpf_compiler_artifacts"]
