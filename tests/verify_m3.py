"""Run M3 acceptance against prepared M1 images on the local Docker engine."""

import _thread
import argparse
import json
import os
from copy import deepcopy
from pathlib import Path
from shutil import copyfile
from threading import Timer
from time import monotonic
from unittest.mock import patch
from uuid import uuid4

from sentinel import baseline, runner
from sentinel.evidence import ARTIFACTS, capture, write_json
from sentinel.replay import LIMITS
from spike.trial import Commands, Project
from spike.workload import TrialError, Workload, assess, load_config


ISOLATION_PROBE = """
import errno, json, os, socket
from pathlib import Path
def inaccessible(path):
    try:
        return not Path(path).exists()
    except PermissionError:
        return True

assert all(inaccessible(path) for path in (
    "/var/run/docker.sock", "/run/docker.sock", "/root/.docker/config.json",
    "/root/.aws/credentials", "/controller", "/workspace",
))
assert not {"SENTINEL_TEST_SECRET", "GROQ_API_KEY", "AWS_SECRET_ACCESS_KEY", "DOCKER_HOST"} & os.environ.keys()
try:
    Path("/app/sentinel-write-probe").write_text("test")
except OSError as error:
    assert error.errno in (errno.EACCES, errno.EROFS)
else:
    raise AssertionError("controller-adjacent layer was writable")
with socket.create_connection(("db", 5432), timeout=2):
    pass
try:
    with socket.create_connection(("192.0.2.1", 443), timeout=2):
        raise AssertionError("external destination was reachable")
except OSError as error:
    assert error.errno == errno.ENETUNREACH
assert all(row.split()[1] != "00000000" for row in Path("/proc/net/route").read_text().splitlines()[1:])
print(json.dumps({"socket_credentials_writes_external": "denied", "database": "reachable"}))
"""


def clone_capture(root, case):
    directory = root / case
    directory.mkdir()
    for name in (*ARTIFACTS, "index.json"):
        copyfile(root / "passing" / name, directory / name)
    return directory


def closed(directory, gate, code, reason):
    assert gate == baseline.read_gate(directory)[0]
    assert not gate["open"] and gate["interventions_used"] == 0
    assert gate["code"] == code and reason in gate["reason"], gate
    assert baseline.investigator_evidence(directory)["interventions_used"] == 0
    print(f"PASS {directory.name}: {gate['code']}: {gate['reason']}", flush=True)


def check_manifest(root, manifest_path):
    for case, keys, substitute, reason in (
        ("missing-fixture", ("fixture", "version"), None, "manifest.fixture.version"),
        ("wrong-image-digest", ("images", "db", "digests"), ["postgres@sha256:" + "0" * 64],
         "manifest.images.db.digests"),
    ):
        directory = clone_capture(root, case)
        manifest = json.loads(manifest_path.read_bytes())
        parent = manifest
        for key in keys[:-1]:
            parent = parent[key]
        if substitute is None:
            del parent[keys[-1]]
        else:
            parent[keys[-1]] = substitute
        supplied = root / f"{case}.json"
        write_json(supplied, manifest)
        gate = baseline.run_gate(directory, supplied)
        closed(directory, gate, "setup_error", reason)
        _, trials, _ = baseline.read_gate(directory)
        assert all(t["status"] == "skipped" for t in trials.values())
        transcript = directory / "baselines" / "commands.txt"
        assert not transcript.exists() or "compose" not in transcript.read_text(encoding="utf-8")


def check_recorded_faults(root, manifest_path, trials, traces):
    # Feed the production M1 assessor real traces with controlled changes, then
    # exercise the actual controller's classification, stop rule and disk contract.
    for case, phase, original, expected, reason in (
        ("failed-control", "healthy", "incident", "healthy_control_failed", "healthy final query"),
        ("generic-http", "incident", "incident", "invalid", "HTTP and terminal observations disagree"),
        ("missing-post-batch", "incident", "incident", "invalid", "required checkpoints missing"),
        ("incident-final-success", "incident", "healthy", "not_reproduced", "incident final query succeeded"),
        ("unverified-cleanup", "incident", "incident", "cleanup_failed", "injected cleanup error"),
    ):
        directory = clone_capture(root, case)

        def recorded(project, slot, manifest, fingerprint, client):
            variant = slot["variant"]
            source = original if variant == phase else variant
            trial = deepcopy(trials[source]) | slot | {"manifest_sha256": fingerprint}
            trace = deepcopy(traces[source])
            if variant == phase:
                if case == "generic-http":
                    trace["requests"][-1]["http_status"] = 500
                elif case == "missing-post-batch":
                    del trace["checkpoints"]["post_batch"]
                elif case == "unverified-cleanup":
                    trial["cleanup"] = {"status": "unverified", "errors": ["injected cleanup error"]}
            try:
                outcome = assess(trace, trial["readiness"], manifest["workload"], variant)
                trial.update(assessment=outcome, reproduction="matched" if (
                    outcome["expected_final_outcome"] == outcome["observed_final_outcome"]
                ) else "not_matched")
            except TrialError as error:
                trial.update(status="invalid", valid=False, reproduction="unassessed", reason=str(error))
            target = client.parent / variant
            target.mkdir()
            write_json(target / "trial.json", trial)
            write_json(target / "observations.json", trace)
            return trial

        with patch.object(baseline, "run_trial", side_effect=recorded) as calls:
            gate = baseline.run_gate(directory, manifest_path)
        closed(directory, gate, expected, reason)
        assert calls.call_count == (1 if phase == "healthy" else 2)


def check_live_faults(root, manifest_path, observed_state):
    original_ready = Workload.ready

    def bad_readiness(workload, deadline):
        def unavailable(method, path, timeout, request_id):
            assert (method, path) == ("GET", "/ready")
            return 503, '{"outcome":"test_unavailable"}'

        workload.transport = unavailable
        return original_ready(workload, min(deadline, monotonic() + 0.4))

    def stop_workload(workload, completed):
        project = workload.transport.__self__
        workload.checkpoint("initial")
        completed("initial")
        if case == "timeout":
            project.commands.deadline = monotonic() + 0.7
            project.exec("api", "python", "-c", "import time; time.sleep(30)")
        elif case == "output-limit":
            project.exec("api", "python", "-c", "print('x' * 300000)")
        else:
            timer = Timer(0.5, _thread.interrupt_main)
            timer.start()
            try:
                project.exec("api", "python", "-c", "import time; time.sleep(2)")
            finally:
                timer.cancel()
                timer.join()
        raise AssertionError("fault injection did not stop execution")

    for case, code, reason in (
        ("failed-readiness", "setup_error", "readiness deadline"),
        ("timeout", "invalid", "TimeoutExpired"),
        ("output-limit", "invalid", "command output exceeded"),
        ("cancelled", "cancelled", "operator cancelled"),
    ):
        directory = clone_capture(root, case)
        method, fault = ("ready", bad_readiness) if case == "failed-readiness" else ("run", stop_workload)
        with patch.object(Workload, method, fault):
            gate = baseline.run_gate(directory, manifest_path)
        closed(directory, gate, code, reason)
        _, trials, traces = baseline.read_gate(directory)
        assert trials["healthy"]["cleanup"]["status"] == "verified"
        assert trials["incident"]["status"] == "skipped"
        if method == "run":
            assert trials["healthy"]["last_completed_step"] == "initial"
            assert "initial" in traces["healthy"]["checkpoints"]
        observed_state()


def verify(root):
    root = root.resolve()
    root.mkdir(parents=True)
    name = "sentinel-m3-test-observed-" + uuid4().hex[:12]
    project = Project(name, "incident", Commands(root, os.environ.copy()))
    assert not any(project.resources().values())
    try:
        project.compose("up", "-d", "--no-build", "--pull", "never", timeout=60)
        origin = "http://" + project.compose("port", "api", "8000")
        observed_trace = root / "observed-trace"
        observed_trace.mkdir()
        workload = Workload(origin, load_config("spike/config.json"), observed_trace, project.commands.record)
        ready = workload.ready(monotonic() + 60)
        workload.run(lambda step: None)
        initial, final = (workload.data["checkpoints"][checkpoint] for checkpoint in ("initial", "final"))
        directory, manifest_path = root / "passing", root / "replay.json"
        capture(origin, initial["timestamp"], final["timestamp"], "HEAD", directory, expected_boot=ready["boot_id"])
        write_json(root / "observed-before.json", {"project": name, "ready": ready, "checkpoint": final})

        def observed_state():
            assert workload.http("GET", "/ready", 5)["body"] == ready
            snapshot = workload.http("GET", "/diagnostics", 5)["body"]
            assert snapshot["boot_id"] == final["boot_id"] and snapshot["events"] == workload.data["events"]
            assert snapshot["checked_out"] == final["checked_out"] and snapshot["active_requests"] == 0
            write_json(root / "observed-after.json", {"project": name, "ready": ready, "checkpoint": snapshot})

        baseline.prepare_manifest(directory, manifest_path)
        original_run = Workload.run

        def probe_and_run(replay, completed):
            trial = replay.transport.__self__
            raw = trial.exec("api", "python", "-c", ISOLATION_PROBE)
            write_json(trial.commands.output / "isolation-probe.json", json.loads(raw))
            return original_run(replay, completed)

        with patch.object(Workload, "run", probe_and_run), patch.dict(
            os.environ, {"SENTINEL_TEST_SECRET": "fixture-only", "GROQ_API_KEY": "fixture-only"}
        ):
            gate = baseline.run_gate(directory, manifest_path)
        assert gate["open"] and gate["interventions_used"] == 0, gate
        _, trials, traces = baseline.read_gate(directory)
        observed_state()
        print("PASS live paired baselines, distinct resets, verified cleanup, observed state preserved", flush=True)
        print("PASS live API isolation probes for both baselines", flush=True)

        files = {path: path.read_bytes() for path in (directory / "baselines").rglob("*") if path.is_file()}
        with patch.object(runner.BoundedCommands, "run", side_effect=AssertionError("offline read invoked Docker")):
            assert baseline.read_gate(directory)[0] == baseline.read_gate(directory)[0] == gate
            packet = baseline.investigator_evidence(directory)
            try:
                baseline.run_gate(directory, manifest_path)
            except FileExistsError:
                pass
            else:
                raise AssertionError("existing gate accepted a second run")
        assert files == {path: path.read_bytes() for path in (directory / "baselines").rglob("*") if path.is_file()}
        terminal = packet["observations"][packet["incident_final_event_id"]]
        assert terminal["pool_error_type"] == "PoolTimeout" and terminal["query_started"] is False
        assert set(packet["baselines"]["healthy"]) == {"status", "valid", "reproduction"}
        assert not any(value in json.dumps(packet) for value in ("healthy.py", "fixture_instance", "OWNER"))
        write_json(root / "m4-passing.json", packet)
        print("PASS repeatable offline gate read and restricted investigator evidence", flush=True)
        check_manifest(root, manifest_path)
        check_recorded_faults(root, manifest_path, trials, traces)
        check_live_faults(root, manifest_path, observed_state)
        write_json(root / "m4-closed.json", baseline.investigator_evidence(root / "incident-final-success"))
    finally:
        project.remove()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    verify(parser.parse_args().output)
