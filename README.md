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

[Milestone 1 design](docs/design/milestone-1-design.md) · [Measured results](docs/milestone-1-results.md) · [Roadmap](docs/master-roadmap.md)
