# Milestone 6 · Deliver the local CLI and auditable report

**Draft 0.1 · 2026-11-24 · Acceptance pending.** One local investigation command, a Markdown report with adjacent JSON evidence, and independent model-free re-scoring define MVP delivery. The command syntax and artifact layout below are proposed. [Project design](../design.md) · [M6 roadmap](../master-roadmap.md#m6--deliver-the-local-cli-and-auditable-report)

## Engineer workflow

With the observed demo running, the engineer supplies its URL, a bounded time window, and a manifest binding source and replay inputs. Read the free-tier Groq credential from the controller environment. Example proposed invocation from the repository root:

```powershell
python -m sentinel investigate --manifest replay.json --observed-url http://127.0.0.1:8000 --since 2026-11-24T08:00:00Z --until 2026-11-24T08:05:00Z --output-root runs
```

Create a unique local run directory, print its path, capture [M2 evidence](milestone-2-design.md), run the [M3 baseline gate](milestone-3-design.md), and invoke the [M4-reviewed investigator](milestone-4-design.md) through the [M5 controller](milestone-5-design.md) only on a passing gate. A healthy-control failure skips model and intervention calls. Persist results after each phase. On cancellation or tool/provider error, preserve evidence, perform owned cleanup, and render a partial report whenever storage is writable. If output creation or final writing fails, print the failed path and reason and exit nonzero. Reserve a nonzero exit for operational errors or cancellation; completed unresolved investigations and exhausted budgets are inspectable domain outcomes.

## Run directory and offline contract

| Proposed path under each run | Owned contents |
|---|---|
| `run.json` | Investigation ID, input window, schema and scenario/scorer versions, phase history, actual budgets, timestamps, final status and stop reason, artifact paths and content hashes. |
| `capture/` | M2 raw telemetry, coverage, stable observation index, source snapshot and citation map. |
| `baseline/` | Validated manifest, both trial records or explicit skipped phases, raw observations and gate reasons. |
| `plans/` | All submissions and validation results in order, including corrections, frozen normalized specs and digests. |
| `trials/` | Slot starts, resolved configuration and reset identity, checkpoint and request observations, validity decisions, scoring inputs and results. |
| `verdict.json`, `report.md` | Controller-owned stopping decision and hypothesis ledger, followed by a human-readable rendering with relative artifact links. Retain versioned scenario definitions and measurement vocabulary needed to interpret these files. |

The model may explain what a citation shows. Render conclusions from controller records and label any investigator interpretation. Keep credentials out of artifacts. Write complete JSON files by atomic replacement. Record missing phases explicitly in partial reports. Prevent concurrent invocations from reusing an investigation directory.

Proposed offline command: `python -m sentinel rescore --run runs/<id>`. Load the saved inputs, validate hashes, schema and rule versions, and recompute baseline gate, trial validity, predicate matches, slot consumption, and terminal decision. Use the same deterministic controller functions as the live run. Compare semantic verdict fields and evidence IDs with the originals; ignore presentation and generation timestamps. Print a mismatch and return nonzero on divergence, missing evidence, unsupported rule versions, or changed frozen input. Leave original files intact. This operation requires neither the observed app, Docker, nor model credentials. Fresh-worker workload execution belongs to M7.

## Report content and status rules

Start with the observed symptom, outcome under the tested conditions, and reason the investigation stopped. Show the tested environment and its fidelity gaps, baseline checks, candidate mechanisms and citations, then a chronological timeline of proposals, validation errors, starts, frozen predictions, measurements and conflicts. Link each substantive conclusion to the underlying local record and its observation IDs. Include both submissions and errors after a failed correction. Show how many of three slots started and which measurements were missing.

| Dimension | Rendering rule |
|---|---|
| Execution | Show completed, cancelled, budget exhausted, or environment error, plus a specific stop reason. A valid pair followed by repeated proposal rejection can complete unresolved. |
| Reproduction | Show reproduced only for a passing paired gate. A valid negative result may show not reproduced. Show unstable for inconsistent valid repetitions when available. For absent or invalid controls explicitly display “not assessed” and the missing prerequisite. |
| Hypotheses | Render supported under tested conditions, contradicted, unresolved, or not testable from controller output. Show unscored on individual invalid trials. If a gate prevented proposal generation, say that no hypotheses were tested. |
| Patch | Show “not evaluated” for every MVP run. Keep this field independent of the diagnosis. |

## Acceptance and MVP release evidence

| ID | Check and required evidence |
|---|---|
| M6-R1 | Run the proposed investigation command against the real demo from documented prerequisites. Verify one new directory, a report and all referenced JSON, a passing baseline pair, and an actual model proposal via the approved provider. Record version, actual cost and limits. |
| M6-R2 | Follow one substantive conclusion from report to verdict, frozen predicate, checkpoint/event ID and original observation. Verify all report artifact links resolve after moving the directory as a unit. |
| M6-R3 | Inspect reports for a discriminating result, failed baseline, twice-rejected plan, and exhausted budget. Use controlled proposal fixtures for branch coverage and identify fixture-driven examples. Check counts, errors, remaining alternatives, and all four status dimensions on each. |
| M6-R4 | Disable network access, remove model credentials and stop the demo. Re-score the saved cases and match original semantic verdicts with zero model calls. Change a frozen value, remove a required input, and try an unsupported schema version on copies; each yields a specific verification error and preserves the originals. |
| M6-R5 | Cancel during a started trial and simulate provider failure and an unwritable output path. Verify appropriate exit status, owned cleanup where applicable, and discoverable partial evidence or explicit write error. |
| M6-R6 | Run inherited M1–M5 acceptance checks or reference matching, versioned evidence for each. Publish a local walkthrough with invocation, run path, example report and offline re-score result. Mark single-incident MVP complete only once these checks and the real end-to-end demonstration pass. |

Implement the CLI orchestration over existing controller functions, then render reports from completed and partial saved records, and finally verify offline re-scoring. The exact display wording and CLI names remain reviewable; durable evidence IDs, independent status dimensions and deterministic claims are the acceptance boundary.
