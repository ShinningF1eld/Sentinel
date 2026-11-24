# Milestone 1 results

Milestone 1 passed on 2026-11-24. The calibration pair and all five acceptance pairs met their variant expectations. Each of the 12 attempts used a distinct API boot ID, database fixture ID, and Compose volume. Teardown verification found zero remaining project containers, networks, and volumes after each attempt. [Per-attempt summary](../artifacts/m1/acceptance-01/summary.md) · [Frozen inputs and reset identities](../artifacts/m1/acceptance-01/series.json)

| Variant | Passed | Post-batch active requests | Post-batch checked out | Final ordinary request |
| --- | ---: | ---: | ---: | --- |
| Healthy | 5 / 5 | 0 | 0 | HTTP 200 with fixture value `ready` and a completed SQL query |
| Incident | 5 / 5 | 0 | 2 | HTTP 503 with `PoolTimeout` recorded at checkout and no query started |

Calibration retained the proposed pool capacity of two, two sequential trigger requests, and a 500 ms acquisition timeout. The request and drain deadlines were five seconds, and readiness had a 60-second deadline. Both triggers reached the designated exception after completing SQL in every acceptance attempt. Across the five incident runs, the final acquisition wait ranged from 500.210 to 500.394 ms. The five healthy final queries took 0.474–0.658 ms.

## Checkpoint and failure checks

In the incident calibration trace, the last trigger terminal event has sequence 12, the successful diagnostic snapshot at full checkout has sequence 13, and the final request starts at sequence 14. The corresponding healthy sequence is 14, 15, and 16. The snapshots show the pool state before the final request in both variants. [Incident trace](../artifacts/m1/acceptance-01/calibration-incident/observations.json) · [Healthy trace](../artifacts/m1/acceptance-01/calibration-healthy/observations.json)

- The verifier rejected a post-batch snapshot moved after the final request start, a generic HTTP failure, a client deadline, a missing post-batch sample, and an untyped final timeout. [Trace checks](../artifacts/m1/acceptance-01/evidence-checks.txt)
- The dirty-start attempt exited 1 with an inspectable `setup_error`. The pre-existing volume survived the attempt. It was removed separately after the refusal was verified. [Attempt result](../artifacts/m1/failure-checks/dirty-start-01/trial.json) · [Volume check](../artifacts/m1/failure-checks/dirty-start-01/operator-check.txt)
- Reusing a populated output directory exited 1 and preserved the prior result file. [Output check](../artifacts/m1/development/populated-output-check.txt)

## Environment and resource observations

The series ran on Windows 11 with Python 3.13.15 and Docker Desktop's Linux engine, Docker 29.6.2 and Compose 5.3.1. Docker reported 12 CPUs and 7.66 GiB of memory. Acceptance setup took 6.06–7.98 seconds per attempt. Docker CPU and memory were sampled once after readiness and once after the final checkpoint, 2.00–2.03 seconds apart. The observed sample maxima were 0.03% CPU and 29.50 MiB for the API, and 6.90% CPU and 35.63 MiB for PostgreSQL. The two samples per attempt limit what the measurements show about resource use during the workload. [Evidence audit](../artifacts/m1/acceptance-01/evidence-audit.txt) · [Image preparation](../artifacts/m1/acceptance-01/preparation.txt)

One development healthy trial passed before the frozen series. The intentional dirty-start run and populated-output probe were outside the series. The raw artifacts under `artifacts/m1/` are local generated files. The [run instructions](../README.md) reproduce the series and the trace checks.
