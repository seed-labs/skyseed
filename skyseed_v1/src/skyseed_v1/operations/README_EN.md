# Operations

Host preflight, readiness, session checks, and owned cleanup. Operations may
inspect or prepare the environment but do not define an experiment scenario.

`example.py` exposes the shared example lifecycle. `demo_ping.py` is a
presentation-side probe: it resolves immutable UT/GW policy from the selected
example and executes traffic from an already-running UT container. Runtime
updates never call or wait for it.
