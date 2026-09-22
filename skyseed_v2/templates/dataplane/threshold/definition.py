"""Edit declarations here. No scene, Model, C source or Plan is needed to import it."""
from skyseed_v2.backend.bpf.dataplane import RouteData, Record, U32

DATA = RouteData('threshold', rule=Record(threshold=U32),
                 candidate=Record(weight=U32), max_candidates=2)
