# Milestone 4 · Test whether the model can propose a useful experiment

**Draft 0.1 · 2026-11-24 · Acceptance pending.** The project confirms three independent Groq sessions using `openai/gpt-oss-120b`, a $0 model budget, and a review before full-loop implementation. The proposed rubric and trial packet make that review repeatable. [Project design](../design.md) · [M4 roadmap](../master-roadmap.md#m4--review-model-feasibility-against-a-recorded-baseline)

## Question, cost, and inputs

Can the model turn recorded evidence into a grounded intervention whose outcomes distinguish at least two plausible mechanisms? Use one passing [M3 baseline](milestone-3-design.md) and the associated [M2 source and telemetry](milestone-2-design.md). No new Docker trial is needed. The experiment costs three fresh sessions of operator time and free-tier quota. Confirm free capacity and availability of the configured model at execution; log an unavailable service or exhausted quota and suspend the check if necessary.

Freeze a packet containing the exact starting messages, a neutral symptom description, recorded incident evidence, source snapshot ID, tool definitions, versioned measurement vocabulary, allowed interventions and values, and declared generation settings and request/token/time ceilings. Derive public fields by allowlist. Keep variant labels, healthy diffs, hidden fixes, reviewer notes, and other sessions’ responses outside the packet. Hash it and retain a local copy.

## Session procedure and proposal contract

1. Open a new conversation for each session with the same packet, settings, model identifier, and tool definitions. Provide bounded tools to retrieve observations by ID, inspect source lines, list permitted experiments, and submit a typed plan. Return the requested excerpts and checkpoints; save full tool exchanges on disk. Give tools read-only access to the frozen packet.
2. Request at least two competing mechanisms. For each require a stable hypothesis ID, explanation, resolving evidence references, and an explicit prediction about the effect of the single proposed change. Express predictions in scenario-defined checkpoint/measurement/comparison/value tuples. State what observed result would contradict each prediction. Use the M5 contract for the initial vocabulary and revise it in one place when calibration changes. [M5 scoring design](milestone-5-design.md)
3. Record each completed proposal verbatim, including malformed submissions. If time expires without a proposal, record the stopping reason. Store session ID, packet hash, model requested and returned, settings, tool calls/results, usage, elapsed time, and cost in local JSON. Scrub authorization headers. A transport failure is recorded separately from model behavior; any replacement starts in a fresh conversation and is linked to the interrupted attempt.
4. Review the sessions side by side. Save one row per session with three independent judgments, evidence for each judgment, and a reviewer explanation. For example, two predictions of final success after increasing capacity can both be grounded; they do not distinguish the mechanisms on that measurement. A post-batch count of zero versus a positive count under the same change can distinguish them.

| Rubric dimension | Reviewer procedure |
|---|---|
| Grounding | Resolve references for every mechanism against the packet. Inspect the cited lines or values and explain whether they support the stated rationale. An invented or irrelevant citation fails this dimension. |
| Intervention | Verify exactly one supported factor, a permitted value different from baseline, and unchanged workload conditions for all other factors. Record rejected fields and allowed values. |
| Discrimination | Check that each hypothesis makes an executable, falsifiable prediction before observing any intervention. Identify a permitted observation that would make the hypotheses score differently. A prose-only diagnosis or equivalent predicates fail this dimension. |

## Decision and acceptance

| ID | Check and required evidence |
|---|---|
| M4-R1 | Show three fresh session records using the same packet hash, model and settings, with no prior session messages. Show free-tier execution, actual usage, and $0 spend. An interrupted or unavailable provider leaves outstanding sessions pending. |
| M4-R2 | Resolve a source excerpt and checkpoint through the recorded tools. Verify bounded tool output, complete local logs, and absence of evaluator-only fields or credentials from the packet. |
| M4-R3 | Save all three outcomes, including missing or malformed proposals, and the three rubric judgments for each. Demonstrate the rubric catches an invented reference, an unsupported change, and equivalent predictions. |
| M4-R4 | Record a dated reviewer decision to proceed with the identified contract or revise it, citing specific session evidence. There is no numeric pass threshold. If revised, retain the old packet and review, version the change, and repeat three fresh sessions before integrating the full loop. |

The milestone output is a reviewable decision, even if the trial reveals a poor contract. M5 implementation depends on a proceed decision for a specific version. Model feasibility and provider availability have not been measured in this design task.
