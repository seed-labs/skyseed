"""Expose the repository-local BPF Compiler Inspection Tool facade."""

from .inspection import collect_bpf_compiler_data, write_bpf_compiler_tool

__all__ = ["collect_bpf_compiler_data", "write_bpf_compiler_tool"]
