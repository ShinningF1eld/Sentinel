# Milestone 3 baseline gate

Sentinel runs one healthy baseline followed by one incident baseline for a complete, nonempty M2 capture. It opens the gate only after valid workload traces reach the prescribed final outcomes and both Compose projects have verified teardown. Every record starts with zero interventions.

## Measured pair

The acceptance run on 2026-11-24 used Windows Docker Desktop, Linux Engine 29.6.2, Compose 5.3.1, and Python 3.13.15. Both trials used a two-connection pool, two designated exceptions, and a 500 ms acquisition timeout. They completed all four checkpoints. Their initial checkpoints each had empty workload history, zero active requests, and zero checked-out connections. [Gate decision](../artifacts/m3/final/passing/baselines/gate.json)

| Baseline | Setup | Total incl. cleanup | Post-batch active / checked out | Final response | Acquisition wait | SQL |
| --- | ---: | ---: | ---: | --- | ---: | --- |
| Healthy | 5.49 s | 13.22 s | 0 / 0 | 200, fixture value `ready` | 0.018 ms | Completed in 0.406 ms |
| Incident | 5.29 s | 13.62 s | 0 / 2 | 503, typed `PoolTimeout` | 500.186 ms | Never started |

The saved trials identify distinct API containers and boot IDs, database fixture UUIDs and Compose volumes, and two PostgreSQL backend identities per pool. Backend start timestamps distinguish PIDs reused across database containers. The live acceptance fixture retained its boot ID, lifecycle events, and checked-out count through the pair and the fault runs. The original M2 project's containers were already stopped when verification began and remained untouched. [Healthy trial](../artifacts/m3/final/passing/baselines/healthy/trial.json) · [Incident trial](../artifacts/m3/final/passing/baselines/incident/trial.json) · [Live observed before](../artifacts/m3/final/observed-before.json) · [Live observed after](../artifacts/m3/final/observed-after.json)

## Acceptance evidence

| Check | Evidence |
| --- | --- |
| M3-R1 | A missing `fixture.version` and a substituted PostgreSQL digest both failed in setup, before any Compose operation. Both trial slots were skipped. [Missing fixture](../artifacts/m3/final/missing-fixture/baselines/gate.json) · [Wrong digest](../artifacts/m3/final/wrong-image-digest/baselines/gate.json) |
| M3-R2 | The live pair reached the final checkpoints with matching workload, image, dependency and effective pool settings. Only the control callback mount and selector differed in application inputs. The live observed fixture survived both trials. [Healthy observations](../artifacts/m3/final/passing/baselines/healthy/observations.json) · [Incident observations](../artifacts/m3/final/passing/baselines/incident/observations.json) |
| M3-R3 | A real sandbox with injected unavailable readiness closed in setup. Recorded-trace fault cases closed for a failed healthy control, generic HTTP 500, missing post-batch measurement, incident final success, and unverified cleanup. The HTTP and measurement faults were invalid. The incident final success was valid and classified as `not_reproduced`. Every gate recorded zero interventions. [Readiness](../artifacts/m3/final/failed-readiness/baselines/gate.json) · [Failed control](../artifacts/m3/final/failed-control/baselines/gate.json) · [HTTP failure](../artifacts/m3/final/generic-http/baselines/gate.json) · [Missing measurement](../artifacts/m3/final/missing-post-batch/baselines/gate.json) · [Final success](../artifacts/m3/final/incident-final-success/baselines/gate.json) · [Cleanup](../artifacts/m3/final/unverified-cleanup/baselines/gate.json) |
| M3-R4 | Effective container inspection verified limits, environment, mounts and private networking. Live API probes denied socket, credential paths, writes and external routing while connecting to PostgreSQL. Live timeout, output overflow and cancellation preserved the initial checkpoint, verified owned teardown, and left the unrelated observed project running. [Isolation probe](../artifacts/m3/final/passing/baselines/incident/isolation-probe.json) · [Timeout](../artifacts/m3/final/timeout/baselines/gate.json) · [Output limit](../artifacts/m3/final/output-limit/baselines/gate.json) · [Cancellation](../artifacts/m3/final/cancelled/baselines/gate.json) |
| M3-R5 | The verifier repeated offline reads while making Docker commands fail if called. File contents stayed identical and a second run request was rejected. Both the passing and closed investigator projections are saved. [Verifier output](../artifacts/m3/final-checks.txt) · [Passing projection](../artifacts/m3/final/m4-passing.json) · [Closed projection](../artifacts/m3/final/m4-closed.json) |

M1 calibration, all five regression pairs, and the five negative trace checks passed. M2's boundary, exhausted-pool capture, citation, and offline integrity checks also passed in a dedicated regression project. [M1 series](../artifacts/m3/regression-m1/summary.md) · [M1 verifier](../artifacts/m3/regression-m1/evidence-checks.txt) · [M2 boundaries](../artifacts/m3/m2-edges.txt) · [M2 capture](../artifacts/m3/m2-capture.txt)

## Replay and isolation

- The manifest binds the validated M2 index hash, observed boot ID, incident Git revision and captured source hashes to the replay's local source, fixture, workload, image identities, package pins and limits. The controller compares local tags against the declared Docker image IDs and digests before Compose preparation. Runtime plans use image IDs with builds and pulls disabled. Readiness and fixed inspections verify the running API and cleanup hashes, telemetry module, package pins, fixture row and initialized pool.
- Both M1 Compose variants are rendered from staged, hashed inputs. The runner starts the services after inspecting the created topology. A dedicated internal bridge with isolated gateway mode has no published ports. The trusted runner sends fixed HTTP requests using an in-container loopback client. The API runs unprivileged with a read-only root and no added capabilities. Bind mounts are limited to read-only fixture and control files. Database writes use the dedicated volume and bounded temporary filesystems.
- The API has one CPU, 256 MiB and 64 processes. PostgreSQL has one CPU, 512 MiB and 128 processes. Swap is disabled for both. The runner uses 60 s for manifest and plan setup, 60 s for trial startup and readiness within a 120 s execution deadline, and a separate 45 s teardown window. The per-command ceiling is 20 s with 256 KiB of combined output. The M1 HTTP and drain deadlines are 5 s and each HTTP body is capped at 64 KiB. The pair above fits inside these bounds on the measured machine.
- The Docker child process gets an explicit host environment allowlist and a fresh client configuration. The runtime audit verifies network membership, resource limits, environment and mounts before startup. Inventory and teardown use a random project name plus a per-trial ownership label. Teardown removes only verified containers, networks and volumes and records any remaining project inventory.

This milestone supports the calibrated local fixture. M2 attests to the observed incident source, while replay image and dependency identity come from the manifest and runtime checks. The replay uses synthetic data, sequential traffic and local networking. An unavailable daemon or a controller killed without Python cleanup can leave owned resources and a partial ledger. A missing decision keeps the gate closed. Further execution requires a fresh investigation directory.

## M4 and M6 handoff

`baselines/gate.json` is the controller's final publication marker. It records `open`, `code`, `reason`, `interventions_used`, the M2 binding, the validated manifest's SHA-256 and references to each trial with artifact hashes. Every slot has a `trial.json`, including skipped slots. A started slot records its last completed phase, runtime fingerprint, readiness, reset identities, validity, reproduction status and cleanup outcome. `observations.json` retains measurements and partial traces. Commands and log artifacts provide operator diagnostics.

M4 can bind `investigator_evidence(directory)` to a single investigation and give a model the returned packet alongside M2's scoped observation and source tools. The packet contains gate status, the healthy outcome summary, and incident checkpoint and event records. The `incident_final_event_id` resolves in the packet's `observations` map. Baseline identifiers use `baseline/incident/checkpoint/<name>` or `baseline/incident/event/<sequence>`. The controller keeps the full ledger, manifest, overrides, source controls, and acceptance probes outside the tool interface. Gate failure or unavailable verification permits zero interventions. The gate makes no mechanism or root-cause judgment.

M6 can use `read_gate(directory)` offline to verify the manifest, capture and trial references and reproduce the open gate's final-outcome checks. The `valid` flag distinguishes a valid negative result from failed execution. `cleanup.status` is independent of both. Use the passing and closed gate records in the acceptance table to exercise those report paths.

Run the acceptance suite from the checkout with staged M1 images and a new output path.

```powershell
python -m tests.verify_m3 artifacts/m3/local-acceptance
```
