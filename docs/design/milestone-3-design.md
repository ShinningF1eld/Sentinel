# Milestone 3 · Gate investigations on paired baselines

**Draft 0.1 · 2026-11-24 · Acceptance pending.** The project confirms a healthy/incident baseline pair, fresh local Compose environments, and zero interventions on gate failure. The manifest fields, gate record, and execution sequence below are proposed interfaces. [Project design](../design.md) · [M3 roadmap](../master-roadmap.md#m3--automate-the-healthy-and-incident-baseline-gate)

## Starting point and inputs

Begin with a complete [M2 capture and source snapshot](milestone-2-design.md). Reuse the M1 project lifecycle, clean-start checks, typed timeout evidence, and workload verifier. M1's five pairs establish fixture feasibility; one investigation requests one pair. Keep the ten-trial runner available for regression. [Trial runner](../../spike/trial.py) · [Workload assessment](../../spike/workload.py) · [Calibrated inputs](../../spike/config.json)

The operator supplies a replay manifest. Validate before starting Docker. Require schema version, captured revision and source hashes, resolved API and PostgreSQL image identities, dependency pins, fixture version and digest, clean-start procedure, workload settings, readiness definition, allowed trial limits, and known fidelity gaps. Resolve local image tags to identities and compare them to the declared values. Report missing field names and expected values on mismatch. Save the exact validated manifest and hash; keep healthy overrides and operator settings outside the investigator's source view.

## Controller and runner protocol

1. Allocate two baseline records under the investigation ID. Bind both to the same manifest, workload, and M2 capture. Check that neither project nor volume identity belongs to the observed instance or an existing run. A blocked setup leaves both slots unstarted.
2. Run the healthy baseline and then the incident baseline, once each under normal operation. Start each with a new Compose project, app process, fixed pool, and freshly seeded database. Compare boot ID, fixture instance, volume, effective settings, and initial quiescent checkpoint. Keep only the exception-path cleanup variant different. On failure, stop and record any unstarted phase as skipped. Preserve completed or partial evidence.
3. Drive an ordinary request, the designated-exception batch, drain, post-batch snapshot, and final ordinary request. Validate checkpoint order and required measurements using M1's checks. Keep the M1 demonstration's expected checked-out counts as observations in the investigation ledger. The investigation gate uses valid workload completion plus the prescribed final symptom; it cannot award a mechanism verdict.
4. For healthy require a successful final query and expected fixture value. For incident require the correlated final terminal event with acquisition timeout, typed pool error, and no query started. Require verified cleanup. Persist the gate decision only after both trial outcomes are available or a terminal failure occurs. A missing decision keeps interventions disabled.
5. Persist each trial's manifest fingerprint, variant, actual settings, timestamps, reset identities, limits, command exits, raw workload and checkpoint observations, validity, reproduction outcome, cleanup result, and reason. The controller alone writes the gate summary with both trial references and intervention count zero. M4 receives an investigator-safe evidence projection from this record.

## Execution boundary and failure handling

Only the trusted local runner translates validated manifest fields into fixed commands. Model output cannot supply shell fragments, mount paths, environment variables, or Compose files. Retain the existing Compose CPU, memory, and process caps; apply bounded command output, request deadlines, and a whole-trial deadline including startup. Stage dependencies before trials and limit runtime network access to the declared local services. Confine writable storage to trial resources. Supply explicit environment variables; keep host credentials, model keys, controller files, and the Docker socket out of app containers and investigator tools. [Current Compose limits](../../compose.yaml)

On cancellation or timeout, stop launching work, preserve the last step and available observations, and tear down only resources verified to belong to this trial. Record remaining identities if cleanup fails. An uncertain reset closes the gate. A fresh investigation is the recovery path for this local milestone.

## Acceptance and handoff

| ID | Check and required evidence |
|---|---|
| M3-R1 | Omit a required fixture field and substitute an image with a different digest. Each yields a specific setup error before any container or intervention starts. |
| M3-R2 | Run a real paired baseline. Both traces reach the final checkpoint, healthy succeeds, and incident records acquisition timeout. Verify distinct process, pool initialization, fixture and volume identities and matching non-variant inputs. Observe that the original instance retains its boot ID and state. |
| M3-R3 | Exercise failed readiness, failed healthy control, generic HTTP error, missing post-batch measurement, and an incident final success. Each closes the gate, preserves the reason, and ends with zero interventions. Distinguish an invalid trace from a valid failure to reproduce. |
| M3-R4 | Inspect effective limits, mounts, environment and network permissions. Demonstrate denied access to the socket, credentials, controller writes, and external destinations using trusted test probes. Capture enforced timeout and cancellation evidence with an unrelated project surviving teardown. |
| M3-R5 | Read the decision from disk and resolve each claim to its trial record. Repeating a gate read leaves the baseline count unchanged. Deliver a passing pair and representative failed gate records to M4 and M6. |

Implement manifest validation, then paired execution, then gate persistence and failure checks. Calibrate the whole-trial and output caps on the supported machine and record the chosen values before the acceptance run.
