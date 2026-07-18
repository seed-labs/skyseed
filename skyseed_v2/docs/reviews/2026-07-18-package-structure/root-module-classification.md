# Root-level Module Classification

The five modules are architecturally classifiable. Their problem is primarily
discoverability and scale, not absence of responsibility. Git history shows
that each arrived as an independent vertical-slice module; no commit message or
design record states that root placement itself is a permanent contract.

| Module | Lines | Public definitions | Actual responsibility | Natural grouping | Size/split finding | API consequence of moving |
| --- | ---: | --- | --- | --- | --- | --- |
| `specifications.py` | 867 | 17 classes + `ground_station_id_for` | immutable, backend-independent input specification graph and its intrinsic/cross-reference validation | `specifications/` or `scenario_inputs/` | clearly oversized; several cohesive families can split, but the aggregate validates across them | **not merely physical**: `skyseed_v2.specifications` declares 18 names in `__all__`; callers/tests/docs must change or a new aggregate facade must be designed |
| `scenario_loader.py` | 632 | Loader, LoadError, two schema constants | strict YAML/path/schema serialization adapter that constructs specifications only | `serialization/`, `scenario_files/`, or alongside specifications with an explicit YAML name | oversized; parse methods separate cleanly by input family, while document reading/error/schema rules remain shared | large example imports it directly; module path and two public constants/error path change |
| `scenario_application.py` | 261 | Applier + ApplyResult | application sequencing from validated input to domain/SEED create/join calls and Catalog | `application/` | coherent and not yet oversized; result belongs with the operation that creates it | large example imports both names from this exact module; move is a public path change |
| `projection.py` | 81 | Satellite and GS Projectors | one-entity domain-to-Descriptor projection | `application/`, perhaps `descriptor_projection.py` | small and cohesive; splitting one file per Projector would add fragmentation without independence | CatalogBuilder/tests import the exact module; no root export currently |
| `catalog_builder.py` | 81 | CatalogBuilder | multi-AS traversal, delegation to Projectors and immutable Catalog assembly | `application/`, near projection | small and single-purpose; filename could match the full class/output more closely | complete example and tests import the exact module |

## Why they are at package root now

The observable history is incremental:

- `projection.py` was introduced with the first Satellite slice and extended
  for GS/identity work.
- `catalog_builder.py` was introduced when the complete Catalog slice arrived.
- `specifications.py` arrived with generic static construction and later grew
  during self-contained example migration.
- `scenario_loader.py` was established during that migration.
- `scenario_application.py` was introduced when the prior Builder was
  converged into an explicitly application-level Applier.

No existing `application`, `serialization` or `specifications` subpackage was
present when those slices landed, so a single root module was the shortest
route. That explains chronology; it does not prove root placement is optimal.

## `specifications.py` detailed inventory

AST counts:

- 17 public classes;
- 8 module-level functions: seven private scalar validators plus public
  `ground_station_id_for`;
- each class also owns intrinsic `__post_init__` validation; the aggregate owns
  reference validation helpers.

Natural families are:

1. **Satellite generation values:** `RaanDistributionSpecification`,
   `InOrbitPhaseDistributionSpecification`, `OrbitGenerationSpecification`.
2. **Satellite hierarchy inputs:** `SatelliteSpecification`,
   `OrbitSpecification`, `ShellSpecification`.
3. **GroundStation input:** `GroundStationSpecification` and
   `ground_station_id_for`.
4. **Attachments and ground topology:**
   `InternetExchangeAttachmentSpecification`,
   `LocalNetworkAttachmentSpecification`, `InternetExchangeSpecification`,
   `OrdinaryNetworkSpecification`, `OrdinaryRouterSpecification`,
   `OrdinaryAutonomousSystemSpecification`.
5. **Skyseed AS aggregate:** `SkyseedAutonomousSystemSpecification`.
6. **Peering input:** `EbgpRouterReferenceSpecification`,
   `EbgpPeeringSpecification`.
7. **Complete input root:** `StaticEmulatorScenarioSpecification`, which
   performs cross-family uniqueness and reference checks.

Clear one-to-one construction counterparts are:

| Specification | Constructed object/action |
| --- | --- |
| `SatelliteSpecification` | `Satellite` |
| `OrbitSpecification` | `Orbit` |
| `ShellSpecification` | `Shell` |
| `GroundStationSpecification` | `GroundStation` |
| `SkyseedAutonomousSystemSpecification` | `SkyseedAutonomousSystem` |
| `InternetExchangeSpecification` | upstream SEED IX creation |
| `OrdinaryNetworkSpecification` | upstream SEED Network creation |
| `OrdinaryRouterSpecification` | upstream SEED Router creation |
| `OrdinaryAutonomousSystemSpecification` | upstream SEED AS creation |
| `EbgpPeeringSpecification` | one SEED eBGP configuration action, not a domain entity |

The following should remain together or at least have an explicit family
facade to avoid one-class-file fragmentation:

- RAAN + phase + Orbit generation;
- Satellite + Orbit + Shell explicit hierarchy inputs;
- ordinary Network + Router + AS plus their local attachment;
- eBGP peering + router reference;
- GS input + its stable-ID function and IX attachment relationship.

The aggregate Scenario specification can live separately, but it must import
all families because it checks global ASN/entity/IX/router/address references.
Splitting files does not eliminate that real coupling.

## `scenario_loader.py` detailed inventory

AST counts:

- three classes: two public (`StaticEmulatorScenarioLoader`,
  `StaticEmulatorScenarioLoadError`) and private `_UniqueKeySafeLoader`;
- four module-level private parsing helpers;
- 16 class methods: public `load`, shared `_read_document`, and family-specific
  parsers for Skyseed AS, external GS files, Shell/generation/Orbit/Satellite,
  GS/IX attachment, IX, ordinary AS/Network/Router/local attachment and eBGP.

Independent parse sections line up with the specification families and could
be moved mechanically to focused private parser modules. The following are
shared and should not be duplicated:

- unique-key YAML behavior;
- bounded-path/document loading and error translation;
- allowed/required-field checking;
- schema constants/version dispatch;
- final construction of `StaticEmulatorScenarioSpecification`.

A split therefore improves navigation but needs a central loader facade or
coordinating function. It must not introduce a second public application layer.

## Classification conclusion

A natural package organization exists:

```text
specifications/       immutable input model families
serialization/        YAML loader and parsing adapter
application/          Applier, operation result, Projectors and CatalogBuilder
```

This organization does not require new domain concepts. It does change public
module paths, because all five current modules deliberately declare public
symbols and three formal examples import four of the paths directly. Whether
to preserve facade imports or migrate without aliases is a separate API choice
for the user/GPT review.
