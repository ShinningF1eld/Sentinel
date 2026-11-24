# Sentinel

Milestone 1 demonstrates how a connection pool behaves after handlers raise a designated exception. The demo uses one Python API process and a PostgreSQL fixture with one row. The same workload runs against both configurations.

## Run the demo

From the repository root, use Python 3.13 and a running local Docker Engine with Compose v2 or later and Linux containers. The host driver uses the Python standard library. The API image installs the pinned dependencies in `requirements.txt`. Build and pull the images before starting any trials.

```powershell
docker compose pull db
docker compose build api
python -m spike.trial --variant healthy --config spike/config.json --output artifacts/m1/manual/healthy
python -m spike.trial --variant incident --config spike/config.json --output artifacts/m1/manual/incident
```

Each invocation uses a new Compose project, database volume, and API process. Use a fresh output directory for every attempt. Exit code 0 requires a valid workload, the expected final outcome and post-batch pool count, and verified teardown. An incident attempt passes when the final request records an acquisition timeout.

To run calibration followed by the five alternating acceptance pairs, use a new series directory.

```powershell
python -m spike.series --config spike/config.json --output artifacts/m1/local-series
python -m tests.verify_m1 artifacts/m1/local-series
```

## Read the evidence

- `summary.md` and `series.json` list attempts, the frozen inputs, setup time, resource samples, and the decision.
- `config.json` and `commands.txt` identify the effective settings, source and image hashes, host environment, and recorded commands.
- `observations.json` contains client responses, server events, query timings, and the initial, pre-batch, post-batch, and final snapshots. Compare the post-batch snapshot sequence with the last trigger terminal event and the final ordinary request start event.
- `trial.json` contains reset identities, readiness checks, resource measurements, protocol validity, and cleanup verification. `compose.log` contains bounded container logs.

The incident app supplies `GET /ordinary`, `POST /trigger`, `GET /ready`, and `GET /diagnostics`. The diagnostic read uses process memory and can run while the pool is fully checked out. The healthy cleanup callback and its Compose override live under `spike/`. Only the incident app source is baked into the API image.

If teardown is unverified, use the project name recorded in the attempt's `config.json` and inspect the owned resources in `trial.json`. Run the recorded teardown command for that project and verify that its labeled resources are gone before starting another attempt.

## Capture and cite historical evidence

Run the incident API from a clean checkout of the Git revision you want to investigate. Supply its origin, a historical UTC interval, and the revision used to build the running image. Set `$sentinelBoot` to the boot ID from `/ready` to pin the observed process. `--repo` selects the local Git repository and defaults to the current directory.

```powershell
python -m sentinel.evidence capture --url $sentinelOrigin --since $sentinelSince --until $sentinelUntil --boot-id $sentinelBoot --revision $sentinelRevision --output artifacts/m2/investigation-01
python -m sentinel.evidence list-source artifacts/m2/investigation-01
python -m sentinel.evidence read-source artifacts/m2/investigation-01 demo/incident.py 1 2
python -m sentinel.evidence resolve artifacts/m2/investigation-01 $sentinelObservationId
```

Use timezone-aware bounds such as `2026-11-24T10:00:00Z`. `since` is inclusive and `until` is exclusive. The output directory must be new. Choose an observation ID from `index.json`. The inspection commands read the saved capture and can run after the observed API stops. A failed capture leaves its available evidence and error in the investigation directory. [Evidence contract and verification](docs/milestone-2-results.md)

## Run and read the baseline gate

Use a complete M2 investigation with a nonempty observation window and the matching incident sources. Stage the two M1 images first. M3 uses the default local Linux Docker endpoint and an Engine that supports the isolated bridge gateway mode. The operator prepares and reviews the replay manifest before running the gate.

```powershell
$sentinelInvestigation = 'artifacts/m2/investigation-01'
$sentinelManifest = 'artifacts/replay-01.json'
python -m sentinel.baseline prepare $sentinelInvestigation $sentinelManifest
python -m sentinel.baseline run $sentinelInvestigation $sentinelManifest
python -m sentinel.baseline read $sentinelInvestigation
python -m sentinel.baseline investigator $sentinelInvestigation
```

Run performs one baseline attempt per investigation and exits with status 0 when the gate opens. Read verifies the saved decision offline. The investigator command prints the gate status, incident checkpoints and events, and a healthy outcome summary. For another attempt, use a fresh investigation directory. [M3 results and handoff contract](docs/milestone-3-results.md)

[Milestone 1 design](docs/design/milestone-1-design.md) · [Measured results](docs/milestone-1-results.md) · [Roadmap](docs/master-roadmap.md)
