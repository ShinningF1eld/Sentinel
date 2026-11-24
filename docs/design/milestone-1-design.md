# Milestone 1 · Demonstrate the connection-pool incident

**Status** Implemented and verified 2026-11-24 · **Design revision** 0.1

**Decision at exit.** Can a developer repeatedly start from a known fixture, trigger exceptions, and show that a subsequent ordinary request times out acquiring a database connection only on the incident configuration? Passing M1 requires both the paired observations and evidence that the reset works. The output is a manually runnable local experiment and its recorded results.

The workload, symptom, checkpoint ordering, Python stack, PostgreSQL fixture, and local Compose environment come from the confirmed project design. The interfaces, starting values, and ten-trial allocation below describe the design for the spike. [Project design, revision 0.14](../design.md) · [Master roadmap, M1](../master-roadmap.md#m1--demonstrate-the-incident-under-controlled-conditions) · [Measured results](../milestone-1-results.md)

## Experiment and fixture

Use one API process with one fixed-capacity database pool. Seed PostgreSQL with a table containing one known row. The ordinary operation checks out a connection, reads the row by key, returns the expected value, and releases the connection. The trigger operation performs the same read and then raises a designated application exception. Both configurations return the same application error for that exception.

The sole controlled difference is how the exception path handles the acquired connection. The healthy variant returns it during cleanup. The incident variant retains the checked-out handle until process exit. Hold a strong reference so object disposal cannot accidentally repair the fixture. Use the pool’s actual acquisition and return operations and a bounded acquisition wait. At capacity the pool must wait and time out. Disable overflow connections, automatic retries, and background requests that borrow from the pool during measurement. Keep the control implementation and variant selection in operator-owned fixture material so M2 can expose the incident source snapshot independently.

| Proposed input | Initial value | Reason and calibration rule |
|---|---|---|
| Pool capacity | 2 connections | Small enough to inspect each checkout. Verify the effective capacity before each trial. |
| Trigger batch | 2 sequential requests | One retained checkout per trigger fills the incident pool. Keep batch size equal to capacity for the acceptance series so each trigger reaches the designated exception. Sequential requests remove concurrent demand as an explanation. |
| Acquisition timeout | 500 ms | Start here and measure on the local host. Capture the error at the acquisition boundary. Elapsed time alone cannot identify the symptom. |
| Driver deadlines | 5 s per HTTP request and drain, 60 s for readiness | Keep the request deadline comfortably above the acquisition timeout. If host startup needs more time, record and freeze the revised deadlines before the series. |
| Fixture identity | Known key and value, e.g. key 1 with value `ready` | Check the exact row and fixture version at startup and the returned value on successful ordinary requests. |

Use the first 2–3 working days as the spike budget. First calibrate one clean healthy/incident pair. If separation fails, inspect checkout and return events, effective capacity, deadlines, and reset identity. Change one setting at a time and retain the failed attempts. Freeze the workload, configuration, source snapshot, dependency versions, fixture, and predicate before collecting the acceptance series.

## Components and operator workflow

| Component | Responsibility and interface |
|---|---|
| Compose project | Starts an API and PostgreSQL on a private trial network with fresh project-owned storage. Build or pull images before the series. Identify each trial with a new project name, database volume, and app process. Record image IDs and dependency pins. |
| Python demo API | Serve `GET /ordinary` and `POST /trigger`. Return the request ID with each result. Use HTTP 200 for an ordinary success, 500 for the designated exception, and 503 for pool acquisition timeout. The structured server event identifies the actual outcome. |
| Instrumentation | Keep request lifecycle events and checkpoint measurements in process memory and export structured copies outside disposable storage. A local diagnostic read takes a consistent snapshot without querying PostgreSQL or borrowing a pool connection. Exclude diagnostic reads and readiness probes from workload counts. M2 can expose these same records through its windowed telemetry endpoint. |
| Python workload driver | Sends requests with unique IDs, waits for completion, captures checkpoints and raw responses, and writes the attempt outcome. Runs the same request sequence for both variants. Only the operator-side assessment reads the expected variant. |
| Operator | Selects a variant, starts a fresh trial, inspects the initial state, invokes the driver, saves evidence, and tears down the owned project. A thin spike script can perform these steps. M3 will reuse them for its baseline gate. |

Proposed manual entry points, run from the repository root after installing the project's pinned Python dependencies and starting local Docker with Compose support:

```powershell
python -m spike.trial --variant healthy --config spike/config.json --output artifacts/m1/calibration/h01
python -m spike.trial --variant incident --config spike/config.json --output artifacts/m1/calibration/i01
```

Each invocation owns one trial. Refuse a populated output directory or an existing Compose project with the same identity. Print the artifact path and final status. Return zero only for a valid trial whose measured outcome meets its variant expectation and whose cleanup succeeded. A faithfully recorded incident timeout is an expected successful demonstration. Save the actual commands, working directory, nonsecret settings, and tool versions so another engineer can repeat it.

## Trial protocol and measurement boundaries

1. **Start clean.** Record setup start. Create empty storage, initialize and verify the fixture, start the API, and complete readiness checks. Release any readiness connection. Capture the initial checkpoint with zero active workload requests, zero checked-out connections, and an empty workload history. Record app boot ID, database volume identity, and setup elapsed time.
2. **Establish ordinary behavior.** Send one ordinary request. Require the expected row, a completed query measurement, and a terminal success event. After cleanup, capture the pre-batch checkpoint with both counts at zero.
3. **Trigger exceptions.** Send the configured number of trigger requests sequentially. Every trigger must complete its query and reach the designated exception. Record each outcome even though the HTTP response is an expected failure. A timeout during this batch means the prescribed trigger sequence did not complete.
4. **Establish quiescence.** Wait until every trigger ID has a terminal event and the active workload count is zero. Emit terminal events after handler cleanup and update the active count and event under the same synchronization boundary. Capture and acknowledge the post-batch snapshot before allowing the driver to send the final request. A sleep or receipt of HTTP responses alone cannot establish quiescence.
5. **Test the symptom.** Send exactly one final ordinary request with a new ID. Capture the terminal event and final checkpoint. For an acquisition timeout, record the typed pool error at the checkout call, acquisition wait, and absence of a started query. For a success, record acquisition, query duration, and returned fixture value.
6. **Preserve and reset.** Export evidence before removing the trial’s containers, private network, and data volume. Verify resource removal by project identity. On timeout, interruption, startup failure, or cleanup failure, save the last completed step, partial observations, and owned resource IDs. Verify cleanup before resuming and always use a new attempt directory and fresh state.

Measure durations with a monotonic clock and timestamp events in UTC. Give all app events and snapshots a monotonically increasing per-process sequence number. Verify that the last trigger’s terminal event precedes the post-batch checkpoint and the final request’s start follows it. Count active workload requests from handler entry through cleanup. Measure checked-out connections from pool state or actual checkout/return instrumentation, independently of active requests and the selected fixture label.

## Evidence contract and assessment

Store one directory per attempt, including failed calibration runs. These proposed filenames define the minimum content needed to review the experiment.

| File | Required content |
|---|---|
| `config.json` | Attempt and series IDs, operator-selected variant, code revision or source hash, fixture hash, image identities, workload order, effective pool and timeout settings, host OS, available Docker CPU/RAM, and tool versions. |
| `commands.txt` | Reproducible setup, invocation, evidence collection, and teardown commands with their exit status. Exclude credentials. |
| `observations.json` | For each request, record ID, phase, event sequence, times, HTTP result, application outcome, acquisition outcome/wait, and query duration in ms. Record a null query duration with reason when acquisition prevents SQL execution. For each checkpoint, record name, sequence, timestamp, active requests, and checked-out connections. Preserve client errors and raw server events for cross-checking. |
| `trial.json` | Boot and storage identities, readiness/fixture checks, setup and workload elapsed seconds, resource samples, validity, observed final outcome, expected-outcome comparison, failure reason, and cleanup result. Sample API and database CPU percentage and memory usage during the trial. Record sampling interval and sample count, and label maxima as observed samples. |
| Series summary | One row per attempt with links to its directory, variant, settings, setup time, resource use, post-batch counts, final outcome, and disposition. Include calibration history, frozen-series identifier, per-variant totals, and the decision to proceed or recalibrate. |

Assess protocol validity first. Require a verified clean start, complete ordered workload, correctly correlated events, all required checkpoints and measurements, and a final terminal observation. An explicitly recorded acquisition timeout legitimately has no query duration. An absent observation, unclassified failure, or incomplete workload makes the trial invalid for acceptance. Preserve a valid but unexpected outcome as a mismatch. Record cleanup separately so a teardown failure cannot erase a measured outcome or allow the reset gate to pass.

| Measured condition | Healthy expectation | Incident expectation |
|---|---|---|
| Initial and pre-batch counts | 0 active, 0 checked out | 0 active, 0 checked out |
| Trigger outcomes | All complete query and raise designated exception | Same |
| Post-batch, before final request begins | 0 active, 0 checked out | 0 active, checked out equals capacity |
| Final ordinary request | Correct fixture value, completed query, HTTP 200 | Recorded acquisition timeout for this request, no query started, HTTP 503 |

The final request defines the symptom. The independent post-batch reading supplies evidence about the mechanism. An HTTP error, a client deadline, and a slow query can each have another cause. None satisfies the acquisition-timeout predicate on its own.

## Acceptance criteria and roadmap traceability

The following checks define acceptance for the implemented spike. The [measured results](../milestone-1-results.md) link to the trial and failure-check records.

| ID | Roadmap M1 obligation | Observable pass criterion |
|---|---|---|
| M1-R1 | Manually runnable demo and clean state | From recorded prerequisites and commands, an engineer starts each variant, verifies fresh fixture, process and pool identities or state, runs the driver, and locates the evidence after verified teardown. |
| M1-R2 | Ordered workload and post-batch checkpoint | Evidence contains one successful ordinary request, exactly the configured trigger IDs, their terminal events, a quiescent post-batch snapshot, then the final ordinary request and checkpoint. Reject a test trace with the snapshot moved after final-request start. |
| M1-R3 | Specific symptom and healthy control | A calibrated pair satisfies every expectation in the table above with the same workload and capacity. A generic HTTP failure or client timeout substituted for the final acquisition event cannot pass. |
| M1-R4 | Independently inspectable measurements | A reviewer can recover request outcomes and query durations, and read active and checked-out counts before final-request activity. A diagnostic read succeeds at full checkout. Removing a required sample makes the assessment invalid. |
| M1-R5 | Calibration and ten clean trials | After calibration, run five healthy/incident pairs, ten individual fresh trials total. Alternate which variant runs first across pairs. All five healthy trials and all five incident trials meet their respective expectations with verified resets. Report actual counts including invalid or interrupted attempts. |
| M1-R6 | Reproducible evidence, cost, and failure handling | Every acceptance attempt records its settings, commands, setup time, resource samples, result, and cleanup. Exercise a failed readiness or dirty-start check and verify an inspectable setup failure. After a mismatch, overlap, invalid measurement, or uncertain reset, keep the series, correct the issue, and run a newly identified full series under frozen inputs. |

## Delivery sequence and handoff

1. Build the fixture and both cleanup paths. Demonstrate one ordinary request and one designated exception on each.
2. Add lifecycle measurements, the checkpoint read, and the workload driver. Exercise the ordering and symptom-negative checks before tuning settings.
3. Implement trial setup, capture, teardown, and summary. Run the calibration pair, freeze the inputs, then execute and assess the ten-trial series.
4. Hand M2 the runnable incident app, event fields, checkpoint semantics, frozen settings, and one reviewed trace from each variant. M3 inherits the fresh-state procedure, exact workload, predicate, and invalid-trial examples. The ten-run series remains M1 feasibility evidence. M3’s investigation baseline uses one healthy run and one incident run.

**Decision record, 2026-11-24.** The implementation used five acceptance pairs, sequential triggers, and the proposed settings after a clean calibration pair. The results record the tested host resources and trial measurements.
