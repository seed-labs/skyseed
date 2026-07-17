"""Fail-closed Git snapshot pin for the selected v1 reference files."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from subprocess import CompletedProcess, run
from typing import Callable, Sequence


PINNED_V1_REFERENCE_COMMIT = "ad331b9c15e3a5fb522af9ff18a06434f99c0860"
V1_REFERENCE_FILE_PATHS = (
    "data/orbits/planned_shell_orbit_data_s1_s5.json",
    "data/ground/starlink_gateways.json",
    "data/ground/s4_visualization_gateway_binding_12.json",
)

GitCommandRunner = Callable[[Sequence[str]], CompletedProcess[str]]


def _default_command_runner(command: Sequence[str]) -> CompletedProcess[str]:
    return run(
        tuple(command),
        check=False,
        capture_output=True,
        text=True,
    )


class V1ReferenceSnapshotError(ValueError):
    """The selected worktree does not match the pinned reference snapshot."""


@dataclass(frozen=True, slots=True)
class ReferenceFileBlob:
    relative_path: str
    git_blob_id: str


@dataclass(frozen=True, slots=True)
class V1ReferenceSnapshotAudit:
    pinned_commit: str
    head_commit: str
    repository_root: Path
    reference_root: Path
    file_blobs: tuple[ReferenceFileBlob, ...]


def _git_blob_id(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return sha1(header + content).hexdigest()


def validate_v1_reference_snapshot(
    reference_root: Path,
    *,
    pinned_commit: str = PINNED_V1_REFERENCE_COMMIT,
    command_runner: GitCommandRunner | None = None,
) -> V1ReferenceSnapshotAudit:
    """Prove that worktree files equal their blobs at one pinned ancestor."""

    root = Path(reference_root).resolve()
    if not root.is_dir():
        raise V1ReferenceSnapshotError(
            f"v1 reference root does not exist: {root}"
        )
    if not isinstance(pinned_commit, str) or len(pinned_commit) != 40:
        raise V1ReferenceSnapshotError(
            "pinned v1 reference commit must be a full 40-character Git ID"
        )
    runner = command_runner or _default_command_runner

    def execute(arguments: tuple[str, ...], context: str) -> CompletedProcess[str]:
        command = ("git", "-C", str(root), *arguments)
        try:
            result = runner(command)
        except OSError as error:
            raise V1ReferenceSnapshotError(
                f"{context} could not execute git: {error}"
            ) from error
        if not isinstance(result, CompletedProcess):
            raise TypeError(
                "reference snapshot command_runner must return CompletedProcess"
            )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            detail = f": {stderr}" if stderr else ""
            raise V1ReferenceSnapshotError(
                f"{context} failed with exit code {result.returncode}{detail}"
            )
        return result

    repository_result = execute(
        ("rev-parse", "--show-toplevel"),
        "v1 reference repository discovery",
    )
    repository_text = repository_result.stdout.strip()
    if not repository_text:
        raise V1ReferenceSnapshotError(
            "v1 reference repository discovery returned an empty path"
        )
    repository_root = Path(repository_text).resolve()
    try:
        reference_prefix = root.relative_to(repository_root)
    except ValueError as error:
        raise V1ReferenceSnapshotError(
            "v1 reference root is outside its reported Git repository"
        ) from error

    execute(
        ("cat-file", "-e", f"{pinned_commit}^{{commit}}"),
        "pinned v1 reference commit lookup",
    )
    head_commit = execute(
        ("rev-parse", "HEAD"),
        "v1 reference HEAD lookup",
    ).stdout.strip()
    if len(head_commit) != 40:
        raise V1ReferenceSnapshotError(
            "v1 reference HEAD lookup did not return a full Git ID"
        )
    execute(
        ("merge-base", "--is-ancestor", pinned_commit, head_commit),
        "pinned v1 reference ancestry check",
    )

    audited_files: list[ReferenceFileBlob] = []
    for relative_path in V1_REFERENCE_FILE_PATHS:
        worktree_path = root / relative_path
        if not worktree_path.is_file():
            raise V1ReferenceSnapshotError(
                f"pinned v1 reference file is missing: {worktree_path}"
            )
        repository_path = (reference_prefix / relative_path).as_posix()
        expected_blob = execute(
            ("rev-parse", f"{pinned_commit}:{repository_path}"),
            f"pinned blob lookup for {relative_path}",
        ).stdout.strip()
        try:
            content = worktree_path.read_bytes()
        except OSError as error:
            raise V1ReferenceSnapshotError(
                f"cannot read pinned v1 reference file: {worktree_path}"
            ) from error
        actual_blob = _git_blob_id(content)
        if actual_blob != expected_blob:
            raise V1ReferenceSnapshotError(
                "v1 reference file drifted from pinned commit "
                f"{pinned_commit}: {relative_path}; expected blob "
                f"{expected_blob}, found {actual_blob}"
            )
        audited_files.append(
            ReferenceFileBlob(
                relative_path=relative_path,
                git_blob_id=expected_blob,
            )
        )
    return V1ReferenceSnapshotAudit(
        pinned_commit=pinned_commit,
        head_commit=head_commit,
        repository_root=repository_root,
        reference_root=root,
        file_blobs=tuple(audited_files),
    )
