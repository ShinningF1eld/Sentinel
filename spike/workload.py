import json
import math
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4


FIXTURE = {"key": 1, "value": "ready", "version": 1}
STATUSES = {"success": 200, "designated_exception": 500, "acquisition_timeout": 503}
CHECKPOINTS = ("initial", "pre_batch", "post_batch", "final")


class TrialError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise TrialError(message)


def utc():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_config(path):
    return validate_config(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def validate_config(config):
    keys = {
        "pool_capacity", "trigger_count", "acquisition_timeout_ms",
        "request_timeout_seconds", "drain_timeout_seconds", "readiness_timeout_seconds",
    }
    require(isinstance(config, dict) and config.keys() == keys, "unexpected configuration fields")
    require(all(type(value) is int and value > 0 for value in config.values()),
            "configuration fields must be positive integers")
    require(config["trigger_count"] == config["pool_capacity"],
            "the M1 trigger count must equal pool capacity")
    require(config["request_timeout_seconds"] * 1000 > config["acquisition_timeout_ms"],
            "HTTP request deadline must exceed acquisition timeout")
    return config


class Workload:
    def __init__(self, base_url, config, directory, record_command, transport=None):
        self.base_url = base_url
        self.config = config
        self.directory = directory
        self.record_command = record_command
        self.transport = transport
        self.opener = build_opener(ProxyHandler({}))
        self.data = {"requests": [], "checkpoints": {}, "snapshots": [], "readiness_attempts": []}
        self.save()

    def save(self):
        write_json(self.directory / "observations.json", self.data)

    def http(self, method, path, timeout, request_id=None):
        url = self.base_url + path
        headers = {"X-Request-ID": request_id} if request_id else {}
        command = ["curl", "--noproxy", "*", "--max-time", str(timeout), "-X", method]
        if request_id:
            command += ["-H", f"X-Request-ID: {request_id}"]
        command.append(url)
        result = {"method": method, "url": url, "sent_at": utc()}
        start = monotonic()
        try:
            if self.transport:
                result["http_status"], result["raw_body"] = self.transport(method, path, timeout, request_id)
            else:
                try:
                    response = self.opener.open(Request(url, headers=headers, method=method), timeout=timeout)
                except HTTPError as error:
                    response = error
                with response:
                    result["http_status"] = response.status
                    result["raw_body"] = response.read().decode("utf-8", errors="replace")
            result["body"] = json.loads(result["raw_body"])
        except (OSError, URLError, ValueError) as error:
            result["client_error"] = f"{type(error).__name__}: {error}"
        finally:
            result.update(received_at=utc(), elapsed_ms=(monotonic() - start) * 1000)
            self.record_command(
                command, f"HTTP {result.get('http_status', 'unavailable')}"
                + (f"; {result['client_error']}" if "client_error" in result else ""),
                equivalent=True,
            )
        return result

    def ready(self, deadline):
        while (remaining := deadline - monotonic()) > 0:
            result = self.http("GET", "/ready", min(remaining, self.config["request_timeout_seconds"]))
            self.data["readiness_attempts"].append(result)
            self.save()
            if result.get("http_status") == 200 and "client_error" not in result:
                verify_readiness(result["body"], self.config)
                return result["body"]
            sleep(min(0.1, max(0, deadline - monotonic())))
        raise TrialError("API readiness deadline expired")

    def request(self, phase, operation):
        request_id = str(uuid4())
        method = "POST" if operation == "trigger" else "GET"
        result = self.http(method, "/" + operation, self.config["request_timeout_seconds"], request_id)
        record = {"phase": phase, "operation": operation, "request_id": request_id, **result}
        self.data["requests"].append(record)
        self.save()
        require("client_error" not in record, f"{phase}: {record.get('client_error')}")
        return record

    def checkpoint(self, name):
        deadline = monotonic() + self.config["drain_timeout_seconds"]
        expected_ids = [request["request_id"] for request in self.data["requests"]]
        while (remaining := deadline - monotonic()) > 0:
            result = self.http("GET", "/diagnostics", min(remaining, self.config["request_timeout_seconds"]))
            self.data["snapshots"].append({"checkpoint": name, **result})
            self.save()
            if result.get("http_status") == 200 and "client_error" not in result:
                snapshot = result["body"]
                terminal_ids = [event["request_id"] for event in snapshot["events"] if event["kind"] == "terminal"]
                if snapshot["active_requests"] == 0 and terminal_ids == expected_ids:
                    checkpoint = {key: value for key, value in snapshot.items() if key != "events"}
                    self.data["checkpoints"][name] = checkpoint
                    self.data["events"] = snapshot["events"]
                    self.save()
                    return
            sleep(min(0.05, max(0, deadline - monotonic())))
        raise TrialError(f"{name}: diagnostic checkpoint deadline expired")

    def run(self, completed):
        self.checkpoint("initial")
        require(not self.data["events"] and self.data["checkpoints"]["initial"]["checked_out"] == 0,
                "initial pool or workload history is dirty")
        completed("initial")
        self.request("ordinary", "ordinary")
        self.checkpoint("pre_batch")
        verify_request(self.data["requests"][0], self.data["events"], {"success"})
        require(self.data["checkpoints"]["pre_batch"]["checked_out"] == 0,
                "ordinary request left a connection checked out")
        completed("pre_batch")
        for index in range(self.config["trigger_count"]):
            self.request(f"trigger_{index + 1}", "trigger")
        self.checkpoint("post_batch")
        for request in self.data["requests"][1:]:
            verify_request(request, self.data["events"], {"designated_exception"})
        completed("post_batch")
        self.request("final", "ordinary")
        self.checkpoint("final")
        for request in self.data["requests"]:
            request["server_events"] = [
                event for event in self.data["events"] if event["request_id"] == request["request_id"]
            ]
        self.save()
        completed("final")


def verify_readiness(ready, config):
    fixture = ready["fixture"]
    require(all(fixture.get(key) == value for key, value in FIXTURE.items()) and fixture.get("instance"),
            "readiness fixture identity or contents differ")
    require(ready.get("boot_id") and ready["pool_capacity"] == ready["pool_min_size"]
            == config["pool_capacity"], "unexpected effective pool capacity or missing boot ID")
    require(ready["acquisition_timeout_ms"] == config["acquisition_timeout_ms"],
            "unexpected effective acquisition timeout")


def duration(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def verify_request(request, events, allowed):
    request_id = request["request_id"]
    matching = [event for event in events if event["request_id"] == request_id]
    kinds = [event["kind"] for event in matching]
    require(len(kinds) >= 2 and kinds[0] == "start" and kinds[-1] == "terminal"
            and all(kind in {"checkout", "return"} for kind in kinds[1:-1]),
            f"{request_id}: missing, duplicate, or unordered lifecycle events")
    start, terminal = matching[0], matching[-1]
    require(start["operation"] == terminal["operation"] == request["operation"],
            f"{request_id}: operation mismatch")
    outcome = terminal["outcome"]
    require(outcome in allowed, f"{request_id}: unexpected or unclassified outcome {outcome}")
    body = request["body"]
    require("client_error" not in request and body["request_id"] == request_id
            and body["outcome"] == outcome
            and request["http_status"] == terminal["http_status"] == STATUSES[outcome],
            f"{request_id}: HTTP and terminal observations disagree")
    require(duration(terminal["acquisition_wait_ms"]), f"{request_id}: missing acquisition time")
    if outcome == "acquisition_timeout":
        require(kinds == ["start", "terminal"] and terminal["acquisition_outcome"] == "timeout"
                and terminal["pool_error_type"] == "PoolTimeout"
                and terminal["query_started"] is False
                and terminal["query_duration_ms"] is None
                and terminal["query_duration_reason"] == "acquisition_timeout",
                f"{request_id}: missing typed checkout timeout or unexpected SQL activity")
    else:
        require(kinds in (["start", "checkout", "terminal"], ["start", "checkout", "return", "terminal"])
                and terminal["acquisition_outcome"] == "acquired"
                and terminal["pool_error_type"] is None and terminal["query_started"] is True
                and duration(terminal["query_duration_ms"]) and terminal["query_duration_reason"] is None
                and terminal["value"] == FIXTURE["value"],
                f"{request_id}: missing acquisition, completed query, or fixture value")
        if outcome == "success":
            require(kinds == ["start", "checkout", "return", "terminal"]
                    and body["value"] == FIXTURE["value"], f"{request_id}: ordinary operation failed")
    return start["sequence"], terminal["sequence"], outcome


def assess(data, ready, config, variant):
    events, checkpoints, requests = (data[key] for key in ("events", "checkpoints", "requests"))
    expected_phases = ["ordinary"] + [
        f"trigger_{index + 1}" for index in range(config["trigger_count"])
    ] + ["final"]
    require([request["phase"] for request in requests] == expected_phases, "workload order or count differs")
    ids = {request["request_id"] for request in requests}
    require(len(ids) == len(requests) and {event["request_id"] for event in events} == ids,
            "duplicate IDs or unrelated workload events")
    require(set(checkpoints) == set(CHECKPOINTS), "required checkpoints missing")
    sequence = [event["sequence"] for event in events]
    require(sequence == sorted(set(sequence)) and all(event.get("timestamp") for event in events),
            "missing timestamp or invalid raw event sequence")
    checkpoint_sequence = [checkpoints[name]["sequence"] for name in CHECKPOINTS]
    require(checkpoint_sequence == sorted(set(checkpoint_sequence))
            and not set(sequence).intersection(checkpoint_sequence), "checkpoint sequence is invalid")
    for name in CHECKPOINTS:
        snapshot = checkpoints[name]
        require(snapshot["boot_id"] == ready["boot_id"] and snapshot.get("timestamp")
                and snapshot["active_requests"] == 0
                and type(snapshot["checked_out"]) is int
                and 0 <= snapshot["checked_out"] <= config["pool_capacity"],
                f"{name}: invalid or missing pool, process, or active-request measurement")
        matching = [entry["body"] for entry in data["snapshots"] if entry["checkpoint"] == name
                    and entry.get("http_status") == 200
                    and entry["body"]["sequence"] == snapshot["sequence"]]
        require(len(matching) == 1 and all(matching[0][key] == value for key, value in snapshot.items())
                and matching[0]["events"] == [event for event in events if event["sequence"] < snapshot["sequence"]],
                f"{name}: checkpoint differs from the raw server snapshot")
    initial, pre, post, final = checkpoint_sequence
    require(checkpoints["initial"]["checked_out"] == checkpoints["pre_batch"]["checked_out"] == 0,
            "initial or pre-batch pool is dirty")
    previous = initial
    for index, request in enumerate(requests):
        if index == 0:
            lower, upper, allowed = initial, pre, {"success"}
        elif index == len(requests) - 1:
            lower, upper, allowed = post, final, {"acquisition_timeout", "success"}
        else:
            lower, upper, allowed = pre, post, {"designated_exception"}
        operation = "trigger" if allowed == {"designated_exception"} else "ordinary"
        require(request["operation"] == operation, "workload operation differs")
        start, end, outcome = verify_request(request, events, allowed)
        require(max(previous, lower) < start < end < upper, "request crossed a checkpoint or request boundary")
        previous = end
    expected_outcome = "success" if variant == "healthy" else "acquisition_timeout"
    expected_checked_out = 0 if variant == "healthy" else config["pool_capacity"]
    return {
        "observed_final_outcome": outcome,
        "expected_final_outcome": expected_outcome,
        "expectation_met": outcome == expected_outcome
        and checkpoints["post_batch"]["checked_out"] == expected_checked_out,
    }
