# GroundStation access selection

The default Model retains feasible reservations and allocates remaining access
under satellite and station capacities. Shared GS capacity is allocated across
Shell scopes; a GS is never a cross-Shell transit router. Optional candidate search
advice cooperates with physical query limits, expanding when admission needs it.

The role receives prior selection facts and returns selected candidate identities;
Planner owns acquisition and operational timing. S5's greedy replacement provides
a simpler author algorithm. See [Model roles](../../../../../docs/development/model-interfaces.md)
and [planning](../../../../../docs/development/planner-state-and-plans.md).
