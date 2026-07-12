# Data

This directory contains reusable, committed orbit, gateway, and catalog inputs.
The canonical orbit and rich ground data moved here byte-for-byte from the
baseline and is guarded by `tests/contracts/test_data_ownership.py`. Orbit data
covers S1-S5; geographic ground identities can be reused by more than one
shell when the example explicitly chooses that policy.

- `orbits/`: planned constellation records shared by examples;
- `ground/`: gateway, UT, policy, binding, and zone catalogs;
- example choices and profile policy stay under `examples/`;
- generated previews, TLE caches, frozen run bundles, and runtime artifacts do
  not belong here.
