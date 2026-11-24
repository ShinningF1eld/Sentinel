"""Prepare, execute, and read Sentinel's one-time M3 baseline gate."""

import argparse
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from sentinel.evidence import Evidence, EvidenceError, digest, write_json
from sentinel.replay import LIMITS, local_inputs, resolve_images, validate_manifest
from sentinel.runner import BoundedCommands, RUN_ERRORS, prepare_client, run_trial, stage_plans
from spike.workload import CHECKPOINTS, assess, require, utc


VARIANTS = ("healthy", "incident")


def prepare_manifest(directory, output):
    template, _ = local_inputs(directory)
    # Manifest preparation needs only Docker image inspection and leaves the capture untouched.
    with TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        config = scratch / "docker"
        prepare_client(config)
        images = resolve_images(BoundedCommands(scratch, config, LIMITS["setup_seconds"]))
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(template | {"images": images}, stream, indent=2, allow_nan=False)
        stream.write("\n")


def trial_failure(trial):
    if trial["cleanup"]["status"] not in ("not_owned", "verified"):
        return "cleanup_failed", "; ".join(trial["cleanup"]["errors"]) or "trial cleanup unverified"
    if not trial["valid"]:
        return trial["status"], trial.get("reason", "trial execution is invalid")
    if trial["cleanup"]["status"] != "verified":
        return "cleanup_failed", "completed trial has no verified cleanup"
    if trial["reproduction"] != "matched":
        return (("healthy_control_failed", "healthy final query did not succeed")
                if trial["variant"] == "healthy" else
                ("not_reproduced", "incident final query succeeded without an acquisition timeout"))
    return None


def verify_pair(healthy, incident):
    for key in ("boot_id", "api_container", "fixture_instance", "volume", "pool_backends"):
        require(healthy["reset"][key] != incident["reset"][key], f"paired reset reused {key}")
    for key in ("manifest_sha256", "environment"):
        require(healthy[key] == incident[key], f"paired trials differ in {key}")
    fields = ("pool_capacity", "pool_min_size", "acquisition_timeout_ms")
    require(all(healthy["readiness"][key] == incident["readiness"][key] for key in fields),
            "paired effective pool settings differ")
    require({tuple(entry) for entry in healthy["reset"]["pool_backends"]}.isdisjoint(
        tuple(entry) for entry in incident["reset"]["pool_backends"]), "paired pool backends overlap")


def run_gate(directory, manifest_path):
    directory = directory.resolve()
    store = directory / "baselines"
    # The exclusive directory claims the one attempt even if a prior process died before publication.
    store.mkdir()
    slots = {variant: {"variant": variant, "project": "sentinel-m3-" + uuid4().hex,
                       "owner": uuid4().hex} for variant in VARIANTS}
    decision = {"schema_version": 1, "open": False, "interventions_used": 0, "started_at": utc(),
                "manifest_sha256": None, "capture": None, "trials": {}}
    attempts = {}
    active = None
    try:
        write_json(store / "slots.json", slots)
        client_config = store / "docker-client"
        prepare_client(client_config)
        commands = BoundedCommands(store, client_config, LIMITS["setup_seconds"])
        raw, manifest, files = validate_manifest(manifest_path, directory, commands)
        decision["manifest_sha256"] = digest(raw)
        decision["capture"] = manifest["capture"]
        with (store / "manifest.json").open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        projects = stage_plans(store, commands, files, manifest, slots)
        for active in VARIANTS:
            trial = attempts[active] = run_trial(
                projects[active], slots[active], manifest, decision["manifest_sha256"], client_config,
            )
            failure = trial_failure(trial)
            if failure:
                decision.update(code=failure[0], reason=failure[1], failed_phase=active)
                break
        else:
            active = "pair"
            verify_pair(attempts["healthy"], attempts["incident"])
            decision.update(open=True, code="passed", reason="both final outcomes matched and cleanup was verified")
    except KeyboardInterrupt:
        decision.update(code="cancelled", reason="operator cancelled the gate", failed_phase=active or "setup")
    except (*RUN_ERRORS, EvidenceError) as error:
        decision.update(code="reset_mismatch" if active == "pair" else "execution_error" if active else "setup_error",
                        reason=str(error) or type(error).__name__, failed_phase=active or "setup")
    for variant in VARIANTS:
        target = store / variant
        target.mkdir(exist_ok=True)
        trial_path = target / "trial.json"
        if not trial_path.is_file():
            write_json(trial_path, slots[variant] | {
                "status": "skipped", "valid": False, "reproduction": "unassessed",
                "reason": f"gate stopped at {decision.get('failed_phase', 'setup')}",
                "cleanup": {"status": "not_owned"},
            })
        decision["trials"][variant] = {"record": f"{variant}/trial.json",
                                       "sha256": digest(trial_path.read_bytes())}
        for filename in ("observations.json", "commands.txt", "command-failures.txt", "compose.log"):
            path = target / filename
            if path.is_file():
                decision["trials"][variant][filename] = digest(path.read_bytes())
    decision["finished_at"] = utc()
    write_json(store / "gate.json", decision)
    return decision


def read_gate(directory):
    store = directory / "baselines"
    require((store / "gate.json").is_file(), "no saved gate decision; interventions are disabled")
    gate = json.loads((store / "gate.json").read_bytes())
    require(gate["schema_version"] == 1 and type(gate["open"]) is bool and gate["interventions_used"] == 0,
            "unsupported baseline gate record")
    if gate["manifest_sha256"]:
        raw = (store / "manifest.json").read_bytes()
        require(digest(raw) == gate["manifest_sha256"], "saved manifest hash mismatch")
        manifest = json.loads(raw)
        require(manifest["capture"] == gate["capture"], "saved manifest capture binding mismatch")
        require(digest((directory / "index.json").read_bytes()) == gate["capture"]["index_sha256"],
                "saved gate M2 capture binding mismatch")
        Evidence(directory)
    require(set(gate["trials"]) == set(VARIANTS), "invalid gate trial references")
    trials = {}
    observations = {}
    for variant in VARIANTS:
        reference = gate["trials"][variant]
        require(reference["record"] == f"{variant}/trial.json", "invalid gate trial path")
        content = (store / reference["record"]).read_bytes()
        require(digest(content) == reference["sha256"], f"saved {variant} trial hash mismatch")
        trials[variant] = json.loads(content)
        for filename in ("observations.json", "commands.txt", "command-failures.txt", "compose.log"):
            if filename in reference:
                artifact = (store / variant / filename).read_bytes()
                require(digest(artifact) == reference[filename], f"saved {variant} {filename} hash mismatch")
                if filename == "observations.json":
                    observations[variant] = json.loads(artifact)
    if gate["open"]:
        require(gate["code"] == "passed" and gate["manifest_sha256"] and set(observations) == set(VARIANTS),
                "open gate is missing its validated pair")
        for variant, trial in trials.items():
            require(trial["variant"] == variant and trial["manifest_sha256"] == gate["manifest_sha256"]
                    and trial_failure(trial) is None, f"open gate has a failed {variant} baseline")
            trace = observations[variant]
            assessment = assess(trace, trial["readiness"], manifest["workload"], variant)
            final = trace["requests"][-1]
            require(assessment == trial["assessment"]
                    and assessment["observed_final_outcome"] == assessment["expected_final_outcome"]
                    and final["request_id"] == trial["final_request_id"]
                    and final["server_events"][-1]["sequence"] == trial["final_event_sequence"],
                    f"open gate's {variant} final evidence differs")
        verify_pair(trials["healthy"], trials["incident"])
    return gate, trials, observations


def investigator_evidence(directory):
    gate, trials, observations = read_gate(directory)
    incident = observations.get("incident", {})
    records = {}
    for name in CHECKPOINTS:
        sample = incident.get("checkpoints", {}).get(name)
        if sample:
            records[f"baseline/incident/checkpoint/{name}"] = {
                key: sample[key] for key in ("timestamp", "sequence", "boot_id", "active_requests", "checked_out")
            }
    for event in incident.get("events", []):
        records[f"baseline/incident/event/{event['sequence']}"] = {
            key: event[key] for key in (
                "sequence", "timestamp", "request_id", "kind", "operation", "outcome", "http_status",
                "acquisition_outcome", "pool_error_type", "acquisition_wait_ms", "query_started",
                "query_duration_ms", "query_duration_reason", "connection_id",
            ) if key in event
        }
    final = trials["incident"].get("final_event_sequence")
    return {"schema_version": 1, "open": gate["open"], "code": gate["code"], "interventions_used": 0,
            "capture": gate["capture"], "baselines": {
                variant: {key: trial[key] for key in ("status", "valid", "reproduction")}
                for variant, trial in trials.items()
            }, "incident_final_event_id": f"baseline/incident/event/{final}" if final is not None else None,
            "observations": records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "run", "read", "investigator"):
        command = subcommands.add_parser(name)
        command.add_argument("directory", type=Path)
        if name in ("prepare", "run"):
            command.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare_manifest(args.directory, args.manifest)
            return 0
        if args.command == "run":
            result = run_gate(args.directory, args.manifest)
        elif args.command == "read":
            result, _, _ = read_gate(args.directory)
        else:
            result = investigator_evidence(args.directory)
        print(json.dumps(result, indent=2))
        return int(args.command == "run" and not result["open"])
    except (*RUN_ERRORS, EvidenceError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
