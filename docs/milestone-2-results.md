# Milestone 2 results

M2-R1 through M2-R5 passed on 2026-11-24 with the local Compose API and PostgreSQL fixture. The M1 calibration pair and all five regression pairs also passed with verified teardown. Healthy final requests returned HTTP 200. Incident final requests returned HTTP 503 with `PoolTimeout`. The existing verifier accepted both calibration traces and rejected all five altered traces. [M1 trial series](../artifacts/m2/m1-regression/summary.md) · [M1 verifier output](../artifacts/m2/m1-regression/evidence-checks.txt)

| Check | Result |
| --- | --- |
| M2-R1 | The real two-connection pool was fully checked out after the triggers finished. The M1 workload issued the telemetry read before the final ordinary request. Instrumenting `ConnectionPool.getconn` measured zero attempts during that read. Sequence 12 records the second trigger's terminal event. Sequence 13 records zero active requests and two checked-out connections. [Full-pool response](../artifacts/m2/acceptance/full-pool-response.json) |
| M2-R2 | Lower-edge inclusion, upper-edge exclusion, UTC offset normalization, tied timestamps, sequence order, empty intervals, all three coverage flags, and field-specific 400s passed. Repeated reads preserved the buffer and workload state. [Edge checks](../artifacts/m2/edge-checks.txt) |
| M2-R3 | All 23 observation IDs resolved to identical records after eviction, and the raw SHA-256 matched the index. An interruption immediately before index publication left an unusable capture. HTTP 503 and a truncated response both preserved their response bytes and failure reason. [Capture checks](../artifacts/m2/capture-checks.txt) · [Interrupted capture](../artifacts/m2/acceptance/interrupted/capture.json) · [Partial response](../artifacts/m2/acceptance/partial-transport/capture.json) |
| M2-R4 | Both source citations resolved against the selected commit after the test checkout was edited. The inspector refused healthy, fixture, hidden-fix, evaluator, traversal, and alias paths, invalid line ranges, and a write command. Capture rejected a mismatched source revision, a Git symlink at an allowed path, and a mismatched boot ID. [Source manifest](../artifacts/m2/acceptance/investigation/source.json) · [Revision rejection](../artifacts/m2/acceptance/mismatched-revision/capture.json) |
| M2-R5 | The M1 checkpoint verification passed. The complete capture, empty-window capture, and failed-capture examples were saved. The observed Compose project `sentinel-m2-observed` was still running under its original boot ID at handoff. [Complete capture](../artifacts/m2/acceptance/investigation/capture.json) · [Complete empty capture](../artifacts/m2/acceptance/empty-window/capture.json) · [Evicted history](../artifacts/m2/acceptance/evicted-window/capture.json) · [Unavailable endpoint](../artifacts/m2/acceptance/unavailable/capture.json) |

## Telemetry contract

`GET /telemetry?since=...&until=...` accepts ISO 8601 date-times with `Z` or a numeric offset and optional microsecond precision. Encode a literal `+` in query parameters as `%2B`. The server normalizes bounds to UTC and returns schema version 1, `boot_id`, `source_hashes`, `interval`, `events`, `samples`, and `coverage`. Each array is in sequence order. Lifecycle events and their pool samples share a sequence and timestamp. Diagnostic checkpoints contribute a sample with `kind: checkpoint`. Samples measure `active_requests` and `checked_out` after the corresponding state update. Telemetry reads allocate no sequence and use only process memory.

The default buffer holds 1,024 sequence entries and can be set using `TELEMETRY_LIMIT`. Calibration used 16 entries for the incident workload and 20 for healthy. The default holds 51 healthy-run equivalents at that traffic level. The acceptance probe used 32 entries to demonstrate eviction. Each entry holds one sample and, for lifecycle transitions, one event. Eviction removes the pair together. `dropped_records` counts individual events and samples. Coverage records the start of process-local history, the read time, the oldest retained entry, the greatest evicted timestamp and the configured limit. A nonempty interval is incomplete if it extends before startup, starts at or before the eviction watermark, or extends past the read time. The eviction check is conservative when timestamps tie or the wall clock moves backwards. Equal bounds always describe a complete empty interval.

The M1 diagnostic event list retains its original full-history behavior independently of the bounded telemetry buffer. The demo’s timestamps use the process wall clock, with sequence numbers providing order within each boot. Pool samples describe the state at transitions and diagnostic checkpoints.

## Evidence contract for M3

Open a new run with `sentinel.evidence.capture(origin, since, until, revision, output, repo=..., expected_boot=...)`. It fetches telemetry once, then verifies the selected Git blobs against the running API’s raw-byte SHA-256 hashes. Use the observed `/ready` boot ID as `expected_boot` to detect replacement of the intended process. The capture records the full resolved commit ID. The two incident source paths use LF checkout attributes so Windows and Linux builds can match the Git bytes.

| Artifact | Contents |
| --- | --- |
| `telemetry.json` | Exact response body bytes, including any bytes received on HTTP failure. |
| `capture.json` | Requested and normalized intervals, origin, capture time, HTTP status, boot ID, coverage, revision, raw content hash, and status or failure reason as available. |
| `source.json` | Resolved commit ID and an exact UTF-8 text and SHA-256 map for `demo/app.py` and `demo/incident.py`. |
| `index.json` | Schema version, artifact SHA-256 values, and `obs/<boot>/<sequence>/<event or sample>` entries pointing to an array and zero-based offset in `telemetry.json`. This file is published last by atomic rename. |

Use `Evidence(output)` as the M3 precondition. It verifies the index, artifact hashes, coverage, and source identity before allowing offline inspection. Missing publication, unavailable telemetry, incomplete required history, or identity mismatch closes the gate. An interruption can leave temporary files or metadata with status `capturing`. Use a new directory for any later attempt. A complete empty capture has a valid index with zero observations. M3 can enforce its own evidence requirements before it schedules a baseline.

`resolve(id)` returns the original saved record. `list_source()` returns the revision and the allowed path hashes. `read_source(path, start, end)` returns the citation and exact text from a one-based inclusive range of at most 100 lines. The read-only inspector looks up exact keys in the source manifest. Capture rejects Git symlinks at the source paths. The saved bundle can be copied and inspected offline.

In the acceptance capture, resolve `obs/033deb59-93f9-4e1c-9282-0ea4ccc13742/12/event` and `obs/033deb59-93f9-4e1c-9282-0ea4ccc13742/13/sample`. Source citations use `f799480ee3b53ce8c431321bad3a6eed11e1cb2a`, `demo/app.py` lines 1–2, and `demo/incident.py` lines 1–2. The test commit lives in the local acceptance fixture repository and contains the tested demo source. A normal capture should use the actual build commit. The hashes verify byte identity. Trust rests on the local observed app and the operator's custody of the bundle. [Observation index](../artifacts/m2/acceptance/investigation/index.json) · [Test source repository](../artifacts/m2/acceptance/source-repo)

## Reproduce the checks

Build the API, rerun `spike.series`, and run `tests.verify_m1` against its output using the README commands. To run the M2 probe, create a local Compose override at `artifacts/m2/observed.compose.yaml`.

```yaml
services:
  api:
    command: ["python", "/checks/verify_m2.py", "observed"]
    environment:
      PYTHONPATH: /app
    volumes:
      - ./tests/verify_m2.py:/checks/verify_m2.py:ro
```

Start a fresh observed project and use a fresh verification directory. The test-only server adds measured connection-attempt counts to its telemetry response. The verifier deliberately rotates its buffer once the complete capture is saved.

```powershell
docker compose -p sentinel-m2-observed -f compose.yaml -f artifacts/m2/observed.compose.yaml up -d --no-build --pull never
docker compose -p sentinel-m2-observed -f compose.yaml -f artifacts/m2/observed.compose.yaml exec -T api python /checks/verify_m2.py edges
$sentinelBinding = docker compose -p sentinel-m2-observed -f compose.yaml -f artifacts/m2/observed.compose.yaml port api 8000
python -m tests.verify_m2 verify --url "http://$sentinelBinding" --output artifacts/m2/local-acceptance
```

The raw verification artifacts are local generated files under `artifacts/m2/`. For a fresh run, use a new Compose project name and output path. [Run instructions](../README.md)
