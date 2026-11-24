# Milestone 5 · Run and score bounded experiments

**Draft 0.1 · 2026-11-24 · Acceptance pending.** Frozen predictions, a single changed factor, one correction per rejected plan, validity-first scoring, and the three-start cap are confirmed. Predicate syntax and initial allowed values below are proposed for the M4 feasibility review. [Project design](../design.md) · [M5 roadmap](../master-roadmap.md#m5--execute-and-score-bounded-experiments)

## Dependencies and allowed experiment

Start only with a passing [M3 gate](milestone-3-design.md) and the contract version reviewed in [M4](milestone-4-design.md). The Python investigator reads evidence and proposes experiments. The controller owns validation, budget, freezing and scoring. The trusted runner receives a resolved trial spec. Each intervention starts from the clean incident fixture; use the same request order, quiescence check, and final acquisition-timeout definition as the baseline.

With the calibrated baseline of two connections and two triggers, initially offer pool capacity **3 or 4** with trigger count fixed at 2, or trigger count **1** with pool capacity fixed at 2. These values allow all triggers to reach the designated exception. Store the actual versioned allowlist with each run. Reject a no-op, unsupported value, or simultaneous changes. The controller constructs the effective configuration and records its diff against the incident baseline. [M1 inputs](../../spike/config.json)

## Plan, validity, and scoring

A plan contains a question, change field and value, incident baseline ID, and at least two distinct candidate IDs. For each candidate require a mechanism, resolvable source or observation citations, and a prediction. Preserve the set of remaining candidates across rounds; the model cannot silently remove alternatives. Freeze exact submitted JSON, normalized spec, vocabulary version, evidence references, and digest before dispatch. Retain both versions when a correction is needed.

Use conjunctive predicates over scenario-defined facts. Initial examples are integer `post_batch.checked_out` and `post_batch.active_requests` with equality or ordered comparisons, and categorical `final.outcome` with equality to `success` or `acquisition_timeout`. Expose query durations and other checkpoints as evidence; permit prediction fields only when their units, availability and comparisons are declared in the versioned vocabulary. Reject unknown fields, incompatible operands, empty predictions, and invalid citations with field-level errors and permitted values. A usable plan states what would refute each prediction. The prompt seeks disagreement; the scorer must also handle coincident predictions and shared matches.

For example, increase pool capacity to three while holding both triggers fixed. Two candidates may predict final success. If one also predicts retained checkouts and the other predicts zero at quiescence, the observed count can distinguish them. If both predict only final success, preserve both on a shared match. The example exercises the logic; actual proposals and measurements determine the outcome.

| Stage | Controller rule |
|---|---|
| Validate | On first rejection preserve submission and errors, return specific feedback, and allow one correction for this plan. A valid correction follows the same freeze path. On a second rejection persist both attempts and stop unresolved. Neither rejection starts a trial. |
| Dispatch | Verify gate, frozen digest, available slot, and clean-state preconditions. Record one start at handoff to the runner before setup. The started trial consumes a slot even on setup failure, cancellation, or missing measurements. Never start a fourth trial. |
| Validate trial | Verify reset and environment identity, one-factor diff, complete workload and checkpoint order, terminal event consistency, and every required measurement, including predicate operands. Split structural validity from M1's variant expectation check so an intervention can legitimately change the final outcome. A typed acquisition timeout with no query started permits a null final query duration by definition. [Existing assessment](../../spike/workload.py) |
| Score | On invalid observations mark every candidate unscored for this trial and preserve reasons. On a valid trial evaluate every predicate from saved observations. Save expected and observed values, observation IDs, and match/conflict for each predicate. A candidate matches when all its predicates hold and conflicts when at least one fails. |
| Decide | Stop supported under tested conditions only if the same valid trial matches one candidate and conflicts with every remaining alternative. Otherwise retain the candidate ledger and any shared matches or all-conflict results. Carry candidates forward for another discriminating plan while budget permits. On the third start score available evidence first; record budget exhaustion if the distinction remains unresolved. |

On cancellation or unsafe cleanup, preserve partial evidence and end execution. The runner owns teardown. If no allowed experiment distinguishes the remaining candidates, record unresolved with that reason. Only the controller writes trial scores and verdicts. Store the plan history, baseline reference, change diff, frozen inputs, trial start, measurements, result and final stopping reason in the run directory.

## Acceptance and handoff

| ID | Check and required evidence |
|---|---|
| M5-R1 | Attempt a plan on a closed gate, a two-factor change, an invalid citation, and an unknown predicate. Verify specific errors and zero starts. Correct a single invalid plan and show the accepted digest was persisted before the first runner start. |
| M5-R2 | Reject both submissions for one plan. Inspect both payloads and errors, unchanged slot count and unresolved stop. After a previous trial the same path must preserve the earlier start and scores. |
| M5-R3 | Run allowed changes on separate fresh incident states. Compare fingerprints and actual settings to baseline, validate checkpoint order, and confirm the only experimental setting that changed is the declared factor. |
| M5-R4 | Exercise a discriminating observation, a larger-pool shared success, an all-conflict result, and a missing required measurement. Inspect per-candidate verdicts and references. Shared success stays unresolved; a missing measurement leaves every candidate unscored. |
| M5-R5 | Exercise early support, three starts including an invalid trial, and support on the third start. Verify the recorded reason and attempt count on each path, with no fourth dispatch. |
| M5-R6 | Re-evaluate saved frozen proposals against saved observations with the same scenario rules and obtain identical scores. Hand M6 the terminal records for success, shared matches, exhaustion, rejected plans, and execution errors. |

Implement and test the pure validator and scorer with recorded cases first, then connect the runner, budget logic, and model tools. The M4 review may refine vocabulary and values; changing the contract requires the reviewed version to be recorded before live use.
