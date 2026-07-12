# Tools

Offline analysis, conversion, and benchmark commands live here. Tools may use
public Skyseed APIs, but production modules must never import tools.

The cutover classified historical commands before relocation:

- `analyze_*`, `inspect_*`, `summarize_*`, and `diagnose_*`: offline tools;
- `bench_*`: opt-in benchmarks, never acceptance gates;
- `replay_*` and `check_*`: audit or replay front ends over package APIs;
- catalog/preview builders and fetchers: data preparation tools;
- `run_*`, preflight, and session commands: supported CLI or operations fronts.

Only commands with a stable input/output contract are carried forward. A
historical one-off script is retained in Git history or documented history,
not promoted into the package merely because it exists in the baseline tree.

`ground_presets/s5_operational_clean/` is the reference for a reproducible
shell-specific data builder: it writes committed catalogs and an explicit
qualification report, while production modules consume only those outputs and
never import the tool.
