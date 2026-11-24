"""Exercise the assessment against recorded Compose evidence and altered copies."""

import argparse
from copy import deepcopy
import json
from pathlib import Path

from spike.workload import TrialError, assess, require


def read(directory, filename):
    return json.loads((directory / filename).read_text(encoding="utf-8"))


def move_post_batch(data):
    checkpoint = data["checkpoints"]["post_batch"]
    snapshot = next(record["body"] for record in data["snapshots"]
                    if record["checkpoint"] == "post_batch"
                    and record["body"]["sequence"] == checkpoint["sequence"])
    final_start = next(event for event in data["events"]
                       if event["request_id"] == data["requests"][-1]["request_id"]
                       and event["kind"] == "start")
    checkpoint["sequence"], final_start["sequence"] = final_start["sequence"], checkpoint["sequence"]
    snapshot["sequence"] = checkpoint["sequence"]
    snapshot["events"].append(deepcopy(final_start))
    final_snapshot = next(record["body"] for record in data["snapshots"]
                          if record["checkpoint"] == "final")
    final_snapshot["events"] = deepcopy(data["events"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("series", type=Path)
    root = parser.parse_args().series
    for variant in ("healthy", "incident"):
        directory = root / f"calibration-{variant}"
        data = read(directory, "observations.json")
        trial = read(directory, "trial.json")
        config = read(directory, "config.json")["settings"]
        readiness = trial["readiness"]
        require(assess(data, readiness, config, variant)["expectation_met"], f"{variant} trace failed")
        require(trial["cleanup"]["status"] == "verified", f"{variant} teardown unverified")
        print(f"PASS {variant} trace and verified teardown")

    invalid_cases = (
        ("post-batch moved after final start", move_post_batch, "crossed a checkpoint"),
        ("generic HTTP failure in place of 503",
         lambda trace: trace["requests"][-1].update(http_status=500), "observations disagree"),
        ("client deadline in place of a response",
         lambda trace: trace["requests"][-1].update(client_error="TimeoutError"), "observations disagree"),
        ("missing post-batch sample",
         lambda trace: trace["checkpoints"].pop("post_batch"), "required checkpoints missing"),
        ("untyped final timeout",
         lambda trace: replace_timeout_type(trace, "OperationalError"), "missing typed checkout timeout"),
    )
    for name, mutate, expected_error in invalid_cases:
        changed = deepcopy(data)
        mutate(changed)
        try:
            assess(changed, readiness, config, "incident")
        except TrialError as error:
            require(expected_error in str(error), f"{name}: unexpected rejection: {error}")
            print(f"PASS rejected {name}: {error}")
        else:
            raise TrialError(f"assessor accepted {name}")


def replace_timeout_type(data, error_type):
    # Change both copies so the check reaches the typed-outcome predicate.
    data["events"][-1]["pool_error_type"] = error_type
    final_snapshot = next(record["body"] for record in data["snapshots"]
                          if record["checkpoint"] == "final")
    final_snapshot["events"][-1]["pool_error_type"] = error_type


if __name__ == "__main__":
    main()
