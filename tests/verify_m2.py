"""M2 acceptance harness. Run `observed` and `edges` in the demo container."""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from time import monotonic
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import uuid4


def get(origin, path, **parameters):
    if parameters:
        path += "?" + urlencode(parameters)
    try:
        response = urlopen(origin + path, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response), response.headers


def observed():
    from psycopg_pool import ConnectionPool

    from demo import app, incident

    class CountingPool(ConnectionPool):
        attempts = 0

        def getconn(self, *args, **kwargs):
            self.attempts += 1
            return super().getconn(*args, **kwargs)

    with CountingPool(min_size=2, max_size=2, timeout=0.5, kwargs={"autocommit": True}) as pool:
        pool.wait(timeout=15)
        handler = app.handler_for(app.Demo(pool, incident, telemetry_limit=32))

        class MeasuredHandler(handler):
            def read_telemetry(self, query):
                self.attempts_before = pool.attempts
                super().read_telemetry(query)

            def respond(self, status, data):
                if hasattr(self, "attempts_before"):
                    data = data | {"test_checkout_attempts": pool.attempts - self.attempts_before,
                                   "test_total_checkout_attempts": pool.attempts}
                super().respond(status, data)

        with ThreadingHTTPServer(("0.0.0.0", 8000), MeasuredHandler) as server:
            server.serve_forever()


def edges():
    from psycopg_pool import ConnectionPool

    from demo import app, incident

    zero = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def at(seconds):
        return (zero + timedelta(seconds=seconds)).isoformat()

    with ConnectionPool(min_size=1, max_size=1, kwargs={"autocommit": True}) as pool:
        pool.wait(timeout=15)
        with patch.object(app, "datetime", wraps=datetime) as clock:
            clock.now.return_value = zero
            demo = app.Demo(pool, incident, telemetry_limit=3)
            with ThreadingHTTPServer(("127.0.0.1", 0), app.handler_for(demo)) as server:
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                origin = f"http://127.0.0.1:{server.server_port}"
                try:
                    clock.now.return_value = zero + timedelta(seconds=1)
                    demo.snapshot()
                    demo.execute("ordinary", "ordinary")
                    clock.now.return_value = zero + timedelta(seconds=2)
                    demo.snapshot()
                    clock.now.return_value = zero + timedelta(seconds=3)
                    state = (demo.sequence, demo.active, set(demo.checked_out), list(demo.history), list(demo.events))
                    status, data, _ = get(origin, "/telemetry", since=at(1), until=at(2))
                    assert status == 200 and data["coverage"]["eviction_truncated"]
                    assert data["coverage"]["dropped_records"] == 5
                    assert data["coverage"]["oldest_retained_at"] == at(1)
                    assert [(sample["sequence"], sample["kind"]) for sample in data["samples"]] == [
                        (4, "return"), (5, "terminal")]
                    assert [event["sequence"] for event in data["events"]] == [4, 5]
                    assert data == get(origin, "/telemetry", since=at(1), until=at(2))[1]
                    for lower, upper, flag in ((2, 2, None), (2.1, 3, None), (-1, 0, "startup_truncated"),
                                               (3, 4, "future_truncated")):
                        status, result, _ = get(origin, "/telemetry", since=at(lower), until=at(upper))
                        assert status == 200 and not result["samples"] and not result["events"]
                        assert result["coverage"]["complete"] is (flag is None)
                        if flag:
                            assert result["coverage"][flag]
                    offset = timezone(timedelta(hours=7))
                    converted = (zero + timedelta(seconds=2)).astimezone(offset).isoformat()
                    _, valid, _ = get(origin, "/telemetry", since=converted, until=at(3).replace("+00:00", "Z"))
                    assert valid["interval"] == {"since": at(2), "until": at(3)}
                    assert valid["coverage"]["complete"] and [r["sequence"] for r in valid["samples"]] == [6]
                    for field, params in (
                        ("since", {"until": at(2)}), ("until", {"since": at(2)}),
                        ("since", {"since": "", "until": at(2)}),
                        ("since", {"since": "2026-01-01T00:00:00", "until": at(2)}),
                        ("since", {"since": [at(1), at(1)], "until": at(2)}),
                        ("until", {"since": at(2), "until": "invalid"}),
                        ("until", {"since": at(2), "until": at(1)}),
                    ):
                        target = "/telemetry?" + urlencode(params, doseq=True)
                        status, result, _ = get(origin, target)
                        assert status == 400 and result["field"] == field
                    assert state == (demo.sequence, demo.active, demo.checked_out, list(demo.history), demo.events)
                finally:
                    server.shutdown()
                    thread.join()
    print("PASS M2-R2 boundaries, ties, coverage, validation, and read-only state")


def fixture_revision(root):
    root.mkdir()

    def git(*args, input=None):
        return subprocess.run(["git", "-C", str(root), "-c", "user.name=Sentinel Test", "-c",
                               "user.email=sentinel-test@example.invalid", "-c", "core.autocrlf=false", *args],
                              input=input, capture_output=True, check=True).stdout.decode().strip()

    git("init", "--quiet")
    (root / "demo").mkdir()
    for relative in ("demo/app.py", "demo/incident.py", "demo/telemetry.py"):
        (root / relative).write_bytes(Path(relative).read_bytes())
    git("add", "--", "demo")
    git("commit", "--quiet", "-m", "Snapshot the tested incident source")
    revision = git("rev-parse", "HEAD")
    (root / "demo/app.py").write_text("# modified working tree\n", encoding="utf-8")
    git("add", "--", "demo/app.py")
    git("commit", "--quiet", "-m", "Change the API to test a revision hash mismatch")
    mismatch = git("rev-parse", "HEAD")
    git("reset", "--mixed", revision)
    blob = git("hash-object", "-w", "--stdin", input=b"../demo/app.py")
    git("update-index", "--cacheinfo", f"120000,{blob},demo/incident.py")
    git("commit", "--quiet", "-m", "Record a source symlink that must be rejected")
    symlink = git("rev-parse", "HEAD")
    return revision, mismatch, symlink


def verify_transport_failure(save, reject, output):
    body = b'{"error":"endpoint unavailable"}'
    statuses = iter(((503, len(body)), (200, len(body) + 1)))

    class FailedEndpoint(BaseHTTPRequestHandler):
        def do_GET(self):
            status, length = next(statuses)
            self.send_response(status)
            self.send_header("Content-Length", str(length))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), FailedEndpoint) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            for case, reason in (("unavailable", "HTTP 503"), ("partial-transport", "incomplete telemetry response")):
                reject(lambda: save(case, url=url), reason)
                directory = output / case
                assert (directory / "telemetry.json").read_bytes() == body
                assert not (directory / "index.json").exists()
                assert reason in json.loads((directory / "capture.json").read_bytes())["failure_reason"]
        finally:
            server.shutdown()
            thread.join()


def verify(origin, output):
    from sentinel.evidence import Evidence, EvidenceError, capture
    from spike.workload import Workload, assess, load_config

    output.mkdir(parents=True)
    trace = output / "workload"
    trace.mkdir()
    config = load_config("spike/config.json")
    workload = Workload(origin, config, trace, lambda *args, **kwargs: None)
    ready = workload.ready(monotonic() + 60)
    observed = {}

    def post_batch(step):
        if step != "post_batch":
            return
        checkpoint = workload.data["checkpoints"][step]
        assert checkpoint["active_requests"] == 0 and checkpoint["checked_out"] == ready["pool_capacity"] == 2
        status, upper, _ = get(origin, "/diagnostics")
        assert status == 200 and upper["checked_out"] == 2
        observed.update(since=workload.data["checkpoints"]["initial"]["timestamp"], until=upper["timestamp"])
        _, telemetry, _ = get(origin, "/telemetry", **observed)
        assert telemetry["test_checkout_attempts"] == 0 and telemetry["test_total_checkout_attempts"] == 4
        assert telemetry["coverage"]["complete"]
        (output / "full-pool-response.json").write_text(json.dumps(telemetry, indent=2), encoding="utf-8")

    workload.run(post_batch)
    assert assess(workload.data, ready, config, "incident")["expectation_met"]
    repo = output / "source-repo"
    revision, mismatch, symlink = fixture_revision(repo)
    boot = ready["boot_id"]

    def save(name, selected_revision=revision, expected_boot=boot, interval=observed, url=origin):
        return capture(url, **interval, revision=selected_revision, output=output / name,
                       repo=repo, expected_boot=expected_boot)

    def reject(operation, message):
        try:
            operation()
        except EvidenceError as error:
            assert message in str(error), str(error)
        else:
            raise AssertionError(f"accepted evidence expected to fail with {message}")

    directory = save("investigation")
    evidence = Evidence(directory)
    index = json.loads((directory / "index.json").read_bytes())
    records = {identifier: evidence.resolve(identifier) for identifier in evidence.observation_ids()}
    assert len(records) == len(evidence.telemetry["events"]) + len(evidence.telemetry["samples"])
    for identifier, record in records.items():
        location = index["observations"][identifier]
        assert record == evidence.telemetry[location["array"]][location["offset"]]
    post = workload.data["checkpoints"]["post_batch"]["sequence"]
    sample = f"obs/{boot}/{post}/sample"
    assert records[sample]["checked_out"] == 2 and records[sample]["active_requests"] == 0
    terminals = [identifier for identifier, record in records.items() if identifier.endswith("/event")
                 and record.get("outcome") == "designated_exception"]
    assert len(terminals) == 2 and all(records[key]["sequence"] < post for key in terminals)
    print(f"PASS M2-R1 real pool at capacity, zero checkout attempts, {terminals[-1]} and {sample}")

    assert set(evidence.list_source()["files"]) == {"demo/app.py", "demo/incident.py"}
    (repo / "demo/incident.py").write_bytes(b"# changed after capture\n")
    for path in evidence.list_source()["files"]:
        citation = Evidence(directory).read_source(path, 1, 2)
        assert citation["revision"] == revision and citation["path"] == path
        assert citation["text"] == b"".join(Path(path).read_bytes().splitlines(keepends=True)[:2]).decode()
    for path in ("spike/healthy.py", "spike/fixture.sql", "demo/hidden_fix.py", "tests/evaluator.py",
                 "demo/../demo/app.py", "demo/./app.py", "demo\\app.py", str(repo / "demo/app.py")):
        reject(lambda: evidence.read_source(path, 1, 1), "source path is outside the allowlist")
    reject(lambda: evidence.read_source("demo/app.py", 1, 101), "at most 100")
    reject(lambda: evidence.read_source("demo/incident.py", 1, 3), "exceeds 2 lines")
    refusal = subprocess.run([sys.executable, "-m", "sentinel.evidence", "write-source", str(directory)],
                             capture_output=True, text=True)
    assert refusal.returncode == 2 and "invalid choice: 'write-source'" in refusal.stderr
    reject(lambda: save("mismatched-revision", mismatch), "revision/hash mismatch for demo/app.py")
    reject(lambda: save("symlink-revision", symlink), "regular source file at demo/incident.py")
    reject(lambda: save("mismatched-boot", expected_boot=str(uuid4())), "observed boot ID mismatch")
    print(f"PASS M2-R4 both citations at {revision}, revision mismatch, symlink, path and write rejection")

    replace = Path.replace

    def interrupted_index(temporary, target):
        if target.name == "index.json":
            raise KeyboardInterrupt
        return replace(temporary, target)

    with patch.object(Path, "replace", interrupted_index):
        reject(lambda: save("interrupted"), "KeyboardInterrupt")
    assert (output / "interrupted" / "index.tmp").is_file()
    reject(lambda: Evidence(output / "interrupted"), "missing index.json")
    verify_transport_failure(save, reject, output)
    reject(lambda: Evidence(output / "unavailable"), "missing index.json")
    empty = save("empty-window", interval={"since": observed["until"], "until": observed["until"]})
    assert Evidence(empty).observation_ids() == []
    for _ in range(evidence.telemetry["coverage"]["retention_limit"]):
        get(origin, "/diagnostics")
    reject(lambda: save("evicted-window"), "eviction_truncated")
    assert (output / "evicted-window" / "telemetry.json").is_file()
    reject(lambda: Evidence(output / "evicted-window"), "missing index.json")
    offline = Evidence(directory)
    assert {identifier: offline.resolve(identifier) for identifier in offline.observation_ids()} == records
    assert digest_file(directory / "telemetry.json") == index["artifacts"]["telemetry.json"]
    reject(lambda: offline.resolve(f"obs/{boot}/999999/sample"), "observation unavailable")
    _, still_live, _ = get(origin, "/ready")
    assert still_live["boot_id"] == boot
    print(f"PASS M2-R3 {len(records)} offline IDs resolve after eviction, raw hash verified, interrupted write rejected")
    print(f"M2-R5 handoff at {directory}, observed boot {boot} still running")


def digest_file(path):
    return sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("observed", "edges", "verify"))
    parser.add_argument("--url")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.mode == "observed":
        observed()
    elif args.mode == "edges":
        edges()
    else:
        verify(args.url, args.output)
