# Milestone 2 · Capture citable incident evidence

**Revision 0.1 · 2026-11-24 · Acceptance passed.** The confirmed requirements are the read-only windowed endpoint, durable observation IDs, and read-only incident source access from investigation start. [Implementation contract and acceptance results](../milestone-2-results.md) · [Project design](../design.md) · [M2 roadmap](../master-roadmap.md#m2--capture-citable-evidence-from-the-observed-app)

## Starting point and exit

M1 records start, checkout, return, and terminal events. Its diagnostic snapshot acquires the app lock, allocates a sequence number, and copies the current counters and event list. The measured incident reached two checked-out connections after both handlers finished. A snapshot taken later cannot reconstruct the historical counters by itself. M2 adds timestamped samples at the existing state transitions and checkpoint snapshots; the existing workload must still pass its ordering checks. [Demo implementation](../../demo/app.py) · [Workload verifier](../../spike/workload.py) · [M1 results](../milestone-1-results.md)

At investigation start the engineer supplies the observed instance, UTC interval, and incident source revision. The Python controller allocates a fresh investigation directory, binds the source snapshot, captures telemetry once, and exposes those saved inputs for inspection. M3 consumes the capture identity; the live observed instance has a separate lifecycle from its trials.

## Capture and citation contract

1. Under the app lock, append the measured active and checked-out counts alongside each lifecycle event after its counter update. Also retain timestamped diagnostic checkpoint snapshots. Preserve the M1 event and checkpoint sequence numbers. Identify a pool sample by boot ID, sequence, and record kind where it shares a stamp with an event.
2. Serve `GET /telemetry?since=...&until=...` from this process-local buffer. Accept timezone-aware timestamps, normalize to UTC, and select the half-open interval **since inclusive, until exclusive**. Return ordered event and sample arrays, schema version, boot ID, source hashes, requested interval, and coverage metadata. Multiple records can share a timestamp. Sequence orders them within a boot. Reading telemetry must leave the workload sequence, counters, and buffer untouched.
3. Return a field-specific 400 for missing, malformed, or reversed bounds. An equal-bound or genuinely empty interval yields empty arrays. Record process start and oldest retained history; flag intervals affected by startup or eviction. Keep a bounded buffer with an explicit retention limit and dropped-record count. A complete empty capture and a truncated capture must be distinguishable on disk.
4. Save the raw response, interval, capture time, coverage, content hash, and a JSON index in the run directory before investigation tools consume them. Publish the index only after its referenced artifacts exist. Use IDs such as `obs/<boot>/<sequence>/<kind>`. Resolve them against the saved capture even after buffer rotation or app restart. On endpoint failure, truncated required history, or inconsistent identity, preserve the reason and partial evidence and stop before baseline execution.
5. Build a read-only source snapshot from explicitly selected incident app files at the supplied revision. Verify the bytes for the app and cleanup callback against the observed app hashes. Store the revision, permitted relative paths, content hashes, and exact text. Expose a list and a bounded line-range read. Cite revision, path, and line interval. Restrict reads to the saved file map; reject unknown paths, path escapes, symlink aliases, and modifications. The current app and incident callback are the initial selection. Operator overrides, repository history, fixtures, evaluator material, and other workspace files are private. [Incident callback](../../demo/incident.py)

## Acceptance and handoff

| ID | Check and required evidence |
|---|---|
| M2-R1 | Exhaust the real pool with the M1 workload. While checkout count equals capacity, request a window containing the trigger terminal events and post-batch sample. Resolve both event and counts; instrument the checkout boundary to verify zero acquisition attempts during telemetry reads. |
| M2-R2 | Test records on both window edges, tied timestamps, malformed inputs, empty data, and an evicted interval. Verify ordering, exclusion of the upper edge, coverage flags, and identical buffered content across repeated reads with no workload activity. |
| M2-R3 | Save a capture and resolve every indexed ID. Restart or rotate the app, then resolve those IDs to identical saved records and verify the content hash. Interrupt a capture write and verify the controller cannot consume it as complete. |
| M2-R4 | Before any baseline trial, read and cite lines from both permitted files. Verify those citations after the working tree changes. A mismatched revision/hash, healthy override, hidden fix, evaluator path, alias escape, and write request each yield a specific rejection. |
| M2-R5 | Re-run the M1 checkpoint verification. Hand M3 a complete capture, source manifest, and examples of unavailable or incomplete evidence. Keep the observed app running throughout the handoff. |

Implementation order is instrumentation, endpoint, durable indexing, then scoped source reads. Size the proposed buffer from measured demo traffic and record its configured bound in the capture. Completion requires the evidence above as well as the inherited M1 checks.
