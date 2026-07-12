# Backends

Concrete realization mechanisms. `linux/` owns namespaces, links, qdisc, and
Linux MPLS/session mechanics; `bpf/` owns native programs, maps, attachment,
counter access, and cleanup. Backends execute plans but do not choose routes.
