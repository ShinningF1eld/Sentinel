import argparse
import json
import sys
from pathlib import Path

from spike.trial import run_trial
from spike.workload import TrialError, require, write_json


FROZEN_FIELDS = (
    "settings", "source_hashes", "fixture_hash", "images", "dependencies", "docker_engine",
    "docker_client", "compose_version", "python", "host_os", "workload_order",
)


def run_series(config, output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    require(not any(output.iterdir()), f"series directory is populated: {output}")
    with (output / "summary.md").open("x", encoding="utf-8"):
        pass
    series_id = output.name
    trials = []
    frozen = None
    decision = "Calibration pending."

    def save():
        write_json(output / "series.json", {
            "series_id": series_id, "frozen_inputs": frozen, "trials": trials, "decision": decision,
        })
        lines = [
            f"# M1 trial series {series_id}", "",
            "| Attempt | Variant | Pool / batch / timeout | Setup (s) | API CPU / memory | DB CPU / memory | Post-batch active / checked out | Final | Disposition |",
            "| --- | --- | --- | ---: | --- | --- | --- | --- | --- |",
        ]
        for trial in trials:
            settings = trial["settings"]
            setup = trial["setup_seconds"]
            lines.append(
                f"| [{trial['attempt']}]({trial['attempt']}/trial.json) | {trial['variant']} | "
                f"{settings.get('pool_capacity', '—')} / {settings.get('trigger_count', '—')} / "
                f"{settings.get('acquisition_timeout_ms', '—')} ms | {setup:.2f} | "
                f"{trial['sampled_usage']['api']} | {trial['sampled_usage']['db']} | "
                f"{trial['post_batch']} | {trial['final_outcome']} | {trial['status']} / {trial['cleanup']} |"
            )
        lines += ["", "Resource use shows the individual observed samples, in capture order.", ""]
        for variant in ("healthy", "incident"):
            relevant = [trial for trial in trials if not trial["calibration"] and trial["variant"] == variant]
            passed = sum(trial["passed"] for trial in relevant)
            lines.append(f"- {variant.title()} acceptance attempts with verified teardown: {passed} passed of {len(relevant)} attempted, 5 planned.")
        lines += ["", decision, ""]
        (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")

    def attempt(pair, variant, calibration=False):
        nonlocal decision
        name = f"{'calibration' if calibration else f'pair-{pair:02d}'}-{variant}"
        directory = output / name
        code, fingerprint, result = run_trial(variant, config, directory, series_id, frozen=frozen, pair=pair)
        observations = json.loads((directory / "observations.json").read_text(encoding="utf-8"))
        post = observations["checkpoints"].get("post_batch", {})
        usage = {}
        for service in ("api", "db"):
            samples = [sample["services"][service] for sample in result["resource_samples"]]
            usage[service] = ", ".join(
                f"{sample['cpu_percent']:.2f}% / {sample['memory_usage']}" for sample in samples
            ) or "—"
        trials.append({
            "attempt": name, "pair": pair, "variant": variant, "calibration": calibration,
            "settings": fingerprint.get("effective", {}) | fingerprint.get("settings", {}),
            "setup_seconds": result["setup_elapsed_seconds"], "sampled_usage": usage,
            "post_batch": f"{post['active_requests']} / {post['checked_out']}" if post else "—",
            "final_outcome": result.get("observed_final_outcome", "—"), "status": result["status"],
            "cleanup": result["cleanup"]["status"], "passed": code == 0,
            "boot_id": result.get("readiness", {}).get("boot_id"),
            "fixture_instance": result.get("readiness", {}).get("fixture", {}).get("instance"),
            "volume": result.get("storage", {}).get("name"),
        })
        decision = "Series in progress."
        save()
        return fingerprint

    def identities_unique():
        for key in ("boot_id", "fixture_instance", "volume"):
            values = [trial[key] for trial in trials if trial["passed"]]
            if len({value for value in values if value}) != len(values):
                return False
        return True

    save()
    try:
        first = attempt(0, "healthy", calibration=True)
        if trials[-1]["passed"]:
            frozen = {key: first[key] for key in FROZEN_FIELDS}
        if trials[-1]["cleanup"] == "verified" and trials[-1]["status"] != "interrupted":
            attempt(0, "incident", calibration=True)
        require(len(trials) == 2 and all(trial["passed"] for trial in trials) and identities_unique(),
                "Calibration did not establish a clean separated pair.")
        decision = "Calibration passed. Inputs are frozen for the acceptance series."
        save()
        for pair in range(1, 6):
            variants = ("healthy", "incident") if pair % 2 else ("incident", "healthy")
            for variant in variants:
                attempt(pair, variant)
                require(trials[-1]["cleanup"] == "verified", "Acceptance stopped after an unverified reset.")
                require(trials[-1]["status"] != "interrupted", "Acceptance was interrupted.")
        require(all(trial["passed"] for trial in trials) and identities_unique(),
                "The series failed acceptance or reused a reset identity.")
        decision = "Calibration and acceptance passed. Proceed to M2."
        return 0
    except (OSError, TrialError, KeyboardInterrupt) as error:
        decision = f"Repeat under a new series ID after resolving the failure. {error}"
        return 1
    finally:
        save()
        print(output / "summary.md", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Calibrate an M1 pair and run five alternating acceptance pairs.")
    parser.add_argument("--config", default="spike/config.json", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        return run_series(args.config, args.output)
    except (OSError, TrialError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
