#!/usr/bin/env python3
"""Repack accepted exact Tool chunks without rerunning domain execution."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter

from tools._chunk_encoding import (
    CHUNK_ENCODING,
    decode_json_chunk,
    encoded_chunk_json,
)
from tools._html import write_json, write_text
from tools.bpf_compiler.renderer import render_bpf_compiler_page
from tools.planner.renderer import render_planner_page
from tools.slice.renderer import render_slice_difference_page


def repack_inspection_chunks(output: Path) -> dict[str, object]:
    """Compress one canonical output tree and refresh all audit metadata."""

    output = Path(output)
    inspection = output / "inspection"
    qualification = output / "qualification"
    run_path = qualification / "run.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    started = perf_counter()
    results = {}
    for name, record_key in (
        ("slice", "slice_difference_tool"),
        ("planner", "planner_tool"),
        ("compiler", "compiler_tool"),
    ):
        root = inspection / name
        before = _bundle_metrics(root, name)
        tool_started = perf_counter()
        _repack_tool(root, name)
        after = _bundle_metrics(root, name)
        evidence = {
            "mode": "artifact-only exact-chunk repack",
            "domain_execution_rerun": False,
            "encoding": CHUNK_ENCODING,
            "seconds": perf_counter() - tool_started,
            "before": before,
            "after": after,
        }
        record = run.get(record_key)
        if isinstance(record, dict):
            record["files"] = _directory_sizes(root)
            record["artifact_scale_cleanup"] = evidence
        results[name] = evidence

    write_json(run_path, run)
    metric_paths = tuple(sorted(qualification.glob("*-metrics.jsonl")))
    _write_checksums(
        qualification / "checksums.sha256",
        (run_path, *metric_paths),
        qualification,
    )
    return {
        "status": "PASS",
        "encoding": CHUNK_ENCODING,
        "total_seconds": perf_counter() - started,
        "tools": results,
    }


def _repack_tool(root: Path, name: str) -> None:
    chunks = tuple(sorted((root / "chunks").glob("*.js")))
    for path in chunks:
        source = path.read_text(encoding="utf-8")
        assignment = source.rfind("]=")
        if assignment < 0 or not source.endswith(";\n"):
            raise ValueError(f"unsupported exact chunk wrapper: {path}")
        encoded = json.loads(source[assignment + 2 : -2])
        exact = decode_json_chunk(encoded)
        write_text(path, source[: assignment + 2] + encoded_chunk_json(exact) + ";\n")

    if name == "slice":
        manifest_path = root / "slice-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["chunk_encoding"] = CHUNK_ENCODING
        write_json(manifest_path, manifest)
        write_text(root / "index.html", render_slice_difference_page(manifest))
        _update_summary(
            root / "summary.txt",
            "offline-lazy-compressed-slice-chunks",
        )
        payloads = (
            root / "index.html",
            manifest_path,
            root / "summary.txt",
            *chunks,
        )
        inventory = {
            "format_version": 3,
            "representation": "offline-lazy-compressed-slice-chunks",
            "chunk_encoding": CHUNK_ENCODING,
            "files": [_file_record(path, root) for path in payloads],
        }
    elif name == "planner":
        manifest_path = root / "planner-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["chunk_encoding"] = CHUNK_ENCODING
        write_json(manifest_path, manifest)
        write_text(
            root / "planner-manifest.js",
            "window.SKYSEED_PLANNER_MANIFEST="
            + _script_json(manifest)
            + ";\n",
        )
        write_text(root / "index.html", render_planner_page(manifest))
        _update_summary(
            root / "summary.txt",
            "offline-hierarchical-compressed-planner-chunks",
        )
        payloads = (
            root / "index.html",
            manifest_path,
            root / "planner-manifest.js",
            root / "summary.txt",
            *chunks,
        )
        inventory = {
            "format_version": 4,
            "representation": (
                "offline-hierarchical-compressed-planner-chunks"
            ),
            "chunk_encoding": CHUNK_ENCODING,
            "files": [_file_record(path, root) for path in payloads],
        }
    elif name == "compiler":
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["chunk_encoding"] = CHUNK_ENCODING
        write_json(manifest_path, manifest)
        write_text(
            root / "manifest.js",
            "window.__SKYSEED_BPF_COMPILER_MANIFEST__="
            + _script_json(manifest)
            + ";\n",
        )
        write_text(root / "index.html", render_bpf_compiler_page(manifest))
        _update_summary(root / "summary.txt", None)
        payloads = (
            root / "index.html",
            manifest_path,
            root / "manifest.js",
            root / "summary.txt",
            *chunks,
        )
        inventory = {
            "format_version": 3,
            "chunk_encoding": CHUNK_ENCODING,
            "files": [_file_record(path, root) for path in payloads],
        }
    else:
        raise ValueError(f"unsupported Tool: {name}")

    inventory_path = root / "inventory.json"
    write_json(inventory_path, inventory)
    checksum_files = (*payloads, inventory_path)
    _write_checksums(root / "checksums.sha256", checksum_files, root)


def _update_summary(path: Path, representation: str | None) -> None:
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.startswith(("representation=", "chunk_encoding="))
    ]
    if representation is not None:
        lines.append(f"representation={representation}")
    lines.append(f"chunk_encoding={CHUNK_ENCODING}")
    write_text(path, "\n".join(lines) + "\n")


def _bundle_metrics(root: Path, name: str) -> dict[str, object]:
    files = tuple(sorted(path for path in root.rglob("*") if path.is_file()))
    chunks = tuple(sorted((root / "chunks").glob("*.js")))
    family_bytes: dict[str, int] = {}
    family_counts: dict[str, int] = {}
    for path in chunks:
        family = _chunk_family(path.name, name)
        family_bytes[family] = family_bytes.get(family, 0) + path.stat().st_size
        family_counts[family] = family_counts.get(family, 0) + 1
    largest = max(chunks, key=lambda path: path.stat().st_size, default=None)
    bootstrap = {
        "slice": (),
        "planner": (root / "planner-manifest.js",),
        "compiler": (root / "manifest.js",),
    }[name]
    html = root / "index.html"
    return {
        "html_bytes": html.stat().st_size,
        "bootstrap_manifest_bytes": sum(path.stat().st_size for path in bootstrap),
        "initial_browser_load_bytes": html.stat().st_size
        + sum(path.stat().st_size for path in bootstrap),
        "total_artifact_bytes": sum(path.stat().st_size for path in files),
        "file_count": len(files),
        "chunk_count": len(chunks),
        "largest_chunk": (
            None
            if largest is None
            else {
                "path": largest.relative_to(root).as_posix(),
                "size_bytes": largest.stat().st_size,
            }
        ),
        "chunk_count_by_family": dict(sorted(family_counts.items())),
        "chunk_bytes_by_family": dict(sorted(family_bytes.items())),
    }


def _chunk_family(name: str, tool: str) -> str:
    stem = name.removesuffix(".js")
    if tool == "compiler":
        stem = stem.split("-", 1)[1]
        return stem.rsplit("-", 1)[0]
    if stem.startswith("catalog-"):
        return "catalog-" + stem.split("-", 2)[1]
    return stem.split("-", 1)[0]


def _directory_sizes(directory: Path) -> list[dict[str, object]]:
    return [
        {
            "path": path.relative_to(directory).as_posix(),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(value for value in directory.rglob("*") if value.is_file())
    ]


def _file_record(path: Path, root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _write_checksums(path: Path, files: tuple[Path, ...], root: Path) -> None:
    write_text(
        path,
        "".join(
            f"{_sha256(value)}  {value.relative_to(root).as_posix()}\n"
            for value in files
        ),
    )


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _script_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).replace("</", "<\\/")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(repack_inspection_chunks(arguments.output), indent=2))


if __name__ == "__main__":
    main()
