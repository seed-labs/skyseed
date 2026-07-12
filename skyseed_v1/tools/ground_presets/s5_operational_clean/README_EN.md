# S5 Operational-Clean Ground Preset

`build.py` derives the committed S5 ground preset from the canonical planned
S5 orbit and the existing geographic candidate catalogs. Selection covers two
hours at 10-second intervals (720 frames), so it is independent of the shorter
default four-horizon experiment window.

A gateway or UT is retained only when the frozen link model serves it in every
analysis frame. Policies are then rebuilt from the retained gateway bindings;
the S4 policy file is never used by the S5 runtime.

From the repository root:

```bash
python skyseed_v1/tools/ground_presets/s5_operational_clean/build.py
```

The command deterministically rewrites the five `s5_operational_clean_*` files
under `data/ground/`. Review their diff and run the S5 preview contract before
committing a changed orbit, link model, or ground preset.
