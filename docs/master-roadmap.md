# Sentinel master roadmap

**MVP finish line — M6.** An engineer can run one local Python CLI command against the connection-pool demo, inspect the resulting Markdown report, and independently reproduce the controller’s verdict from saved JSON with zero model calls.

This plan follows [design revision 0.14](design.md). The confirmed MVP decisions govern M1–M6. M7–M10 organize the design’s proposed follow-on work. M1 passed on 2026-11-24. M2–M10 are pending. Each milestone uses the preceding milestone’s deliverable and retains its acceptance checks.

The design allows 8–12 weeks for a solo build. Use the first 2–3 working days for M1, aim for M2–M3 by the end of week 2, and target M4–M6 in weeks 3–4. Re-estimate the remaining work from measured setup time, local Docker capacity, and weekly availability.

## M1 · Demonstrate the incident under controlled conditions

[Milestone 1 design](design/milestone-1-design.md)

**Status** Passed. [Measured results](milestone-1-results.md)

**Deliverable.** A Python demo API, PostgreSQL fixture, and workload driver that an engineer can run manually with Docker Compose. Calibrate pool size, exception batch size, and acquisition timeout here. Save the configuration, commands, and observations from the spike.

**Exit criteria**

- From clean state, send an ordinary request, issue the exception-triggering batch, wait for its handlers to finish, capture the post-batch checkpoint, then send the final ordinary request.
- The final request records a database connection acquisition timeout on the incident configuration and succeeds on the healthy control. Record request outcomes, query durations, active requests, and checked-out connections so the post-batch state can be examined independently of the final request.
- Attempt the design’s proposed ten clean trials and record the configuration and result of each, setup time, and resource use. If outcomes overlap or resets are unreliable, refine the fixture or predicate and repeat before proceeding.

## M2 · Capture citable evidence from the observed app

[Milestone 2 design](design/milestone-2-design.md)

**Deliverable.** Use the running M1 demo as the observed instance. At investigation start, save its windowed telemetry in a new local run directory. Expose the corresponding incident source revision to the investigator through a scoped, read-only tool.

**Exit criteria**

- With every pool connection checked out, read-only `GET /telemetry?since=...&until=...` returns previously buffered request events and timestamped pool measurements for the requested window without acquiring a database connection.
- Each saved observation has a stable ID. A reviewer can resolve a telemetry citation after the live buffer changes and a source citation against the recorded revision.
- Source inspection works from investigation start. Tests confirm that healthy-control diffs, fixture labels, hidden fixes, and evaluator files are outside the tool’s scope.

## M3 · Automate the healthy and incident baseline gate

[Milestone 3 design](design/milestone-3-design.md)

**Deliverable.** A Python controller and trusted local Compose runner use M1’s workload and M2’s evidence format to establish whether an investigation may proceed. A replay manifest identifies the source revision, dependency and image pins, fixtures, configuration, workload, readiness checks, and fidelity gaps.

**Exit criteria**

- Once per investigation, run the same workload on a clean healthy configuration and a clean incident configuration, separately from the observed instance. Each run starts with a fresh app process, pool, and database fixture. Save the environment fingerprint, reset, checkpoints, limits, and outcome.
- Open the gate only when both workloads reach the final checkpoint with valid measurements and the acquisition-timeout symptom is absent on healthy and present on incident. Demonstrate that a startup error, generic HTTP failure, missing measurement, or failed control ends the investigation with a recorded reason and zero interventions. Missing manifest inputs produce a specific setup error.
- Exercise timeout, cancellation, and cleanup on the trusted demo. The runner enforces resource limits and cleans up its own resources. Application containers and the investigator cannot access the Docker socket, production credentials, or writable controller files. Model credentials stay with the controller.

## M4 · Review model feasibility against a recorded baseline

[Milestone 4 design](design/milestone-4-design.md)

**Deliverable.** Give three fresh Groq `openai/gpt-oss-120b` sessions the same M3 incident evidence and tool definitions. Use the free tier within the confirmed $0 model budget. Return relevant excerpts and checkpoint measurements through tools and retain full logs locally.

**Exit criteria**

- Save each proposal and review it against three questions. Do its citations resolve to actual evidence? Does it specify one allowed intervention? Do its committed predictions distinguish competing mechanisms using available measurements?
- Record all three outcomes and a decision on whether to proceed or revise the contract before building the full loop. The design sets no required pass count. Preserve the evidence for any revision and repeat the check if the contract changes.

## M5 · Execute and score bounded experiments

[Milestone 5 design](design/milestone-5-design.md)

**Deliverable.** Connect the reviewed M4 proposal contract to the M3 runner. The investigator states competing mechanisms, cites evidence for each, and predicts what would support or contradict each one. Scenario code defines the allowed measurements, checkpoints, comparisons, and trial-validity rules.

**Exit criteria**

- Accept exactly one change per trial, either pool size or exception-triggering request count. Validate the proposal and freeze it before execution. Start from clean incident state and record the baseline, changed value, unchanged workload conditions, and resulting measurements.
- Return the specific validation error and allowed values where applicable. Allow one correction per plan. Freeze a valid correction. A second rejection ends the run unresolved, preserves both submissions and errors, and consumes zero trial slots for that plan.
- The controller checks validity before scoring. A missing required measurement leaves all hypotheses unscored for that trial. On valid trials, record matches and conflicts for each frozen prediction. Demonstrate a shared match with the larger-pool case where final-request success alone cannot distinguish explanations.
- Each started intervention, including an invalid trial, consumes one of three slots after the baseline pair. Stop early when a valid trial matches one candidate and conflicts with every remaining alternative. Record support only under the tested conditions. At the cap, record budget exhaustion and retain unresolved alternatives.

## M6 · Deliver the local CLI and auditable report

[Milestone 6 design](design/milestone-6-design.md)

**Deliverable.** One command runs M2–M5 end to end. Each investigation directory contains baseline observations, proposals, frozen predictions, trial measurements, controller verdicts, and a Markdown report. Only the controller writes verdicts.

**Exit criteria**

- Run the CLI against the demo. Trace a report conclusion through its evidence IDs to the frozen prediction and checkpoint observations. Re-score the saved inputs offline and obtain the same controller verdicts with zero model calls.
- Inspect reports for a discriminating result, a failed baseline, a twice-rejected plan, and budget exhaustion. Each explains why execution stopped, which trials ran, and what remains unresolved. Include both submissions and errors for a twice-rejected plan.
- Show execution, reproduction, hypothesis, and patch status separately, alongside the tested environment, experiment timeline, and fidelity limits. Record the MVP patch status as “not evaluated.” Passing M1–M6 completes the single-incident MVP.

## M7 · Export a reproducer and verify a supplied patch

**Deliverable.** Extend the M6 evidence directory into an executable replay bundle. Freeze the incident predicate, reproducer, and test definitions outside the patch workspace before comparing revisions.

**Exit criteria**

- A fresh supported worker can run the exported commands with no model call and reproduce the recorded result. Include hashes, raw observations, environment requirements, and known fidelity gaps.
- Run the unchanged reproducer on original and patched revisions, then run existing regressions and evaluator-owned held-out workloads unchanged. Include a longer workload that exposes a capacity increase which only delays exhaustion. Record the outcome and coverage of each check independently of hypothesis status.

## M8 · Recover interrupted backend jobs

**Deliverable.** Build durable transitions, worker leases, and retry handling around M7’s replayable trials. Job idempotency enters scope here.

**Exit criteria**

- Restart the controller during a trial and recover its state and provenance. Retrying the same request yields at most one active trial.
- Simulate an abandoned worker and an uncertain completion. Reconcile leases and owned resources before retry, preserve available evidence, and recreate state when reset cannot be verified. Confirm that cleanup leaves an unrelated trial running.

## M9 · Measure the contribution on held-out cases

**Deliverable.** Use the repeatable M8 runner and M7 checks for a versioned comparison. Reserve cases and workload variants before tuning. The proposed target is three development incidents followed by approximately twelve total cases, subject to capacity.

**Exit criteria**

- Compare observation-only, general experiment-capable, and structured Sentinel investigators. Match initial evidence and source access across all three, match experiment tools for the latter two, and record model version, sampling settings, budget ceilings, and actual usage.
- Include healthy, misleading-telemetry, missing-input, symptom-masking patch, and inconclusive cases. Score against withheld ground truth and executable checks. Keep repeated seeds distinguishable from independent cases.
- Publish per-case outcomes and raw counts for supported-cause accuracy, false verification, coverage and abstention, reproduction reliability, patch acceptance error, replay success, time, and cost. Report repeated-run variation and uncertainty. Mark false-verification rate inapplicable when no conclusion was supported.

## M10 · Package the portfolio demonstration

**Deliverable.** Turn M9’s measured runs into a CLI walkthrough, example report, architecture explanation, replay instructions, and recorded demo.

**Exit criteria**

- A reviewer can follow the setup on a supported machine, run the CLI, inspect a cited observation, and replay the example using the published instructions.
- Link measured results and limitations to the case records. Include examples of a rejected hypothesis, rejected bad patch, and unresolved investigation, identifying any outcome the evaluation did not demonstrate.
- Use remaining time for reliability gaps or one separately chosen extension. A hosted API, dashboard, generated patches, RestaurantOS adapter, additional incident families, and performance optimization remain outside MVP acceptance.

## Requirement coverage

| Design requirement | First deliverable and later evidence |
|---|---|
| R1 · Declared application state | M3 validates the manifest. M7 exercises it on a fresh worker. |
| R2 · Valid reproduction | M1 calibrates the symptom. M3 enforces the paired gate. M9 measures repeatability. |
| R3 · Competing hypotheses | M2 supplies citable evidence. M4 evaluates proposals. M5 freezes predictions. |
| R4 · Controlled interventions | M3 supplies clean resets. M5 varies one declared factor and accounts for every start. |
| R5 · Bounded claims | M5 scores predictions. M6 exposes each verdict and stopping reason. |
| R6 · Independent fix verification | M7 compares revisions with frozen and held-out checks. |
| R7 · Isolation and budgets | M3 enforces runner isolation. M5 caps interventions. M8 exercises recovery and cleanup. |
| R8 · Replayable evidence | M6 re-scores from disk. M7 re-executes the workload on a fresh worker. |
| R9 · Reliable backend jobs | M8 tests restart, retry, abandoned work, and ownership. |
| R10 · Comparative evaluation | M9 measures all three approaches. M10 publishes the evidence. |
