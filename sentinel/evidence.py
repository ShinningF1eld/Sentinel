"""Capture an incident once and inspect its evidence offline."""

import argparse
import http.client
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from demo.telemetry import SCHEMA_VERSION, SOURCE_PATHS, parse_timestamp


ARTIFACTS = ("telemetry.json", "capture.json", "source.json")
SOURCE_LINE_LIMIT = 100
LIFECYCLE_KINDS = {"start", "checkout", "return", "terminal"}


class EvidenceError(Exception):
    pass


def require(condition, reason):
    if not condition:
        raise EvidenceError(reason)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return sha256(data).hexdigest()


def write_json(path, data):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def git(repo, *args):
    result = subprocess.run(["git", "--no-replace-objects", "-C", str(repo), *args],
                            capture_output=True, timeout=10)
    require(result.returncode == 0, result.stderr.decode(errors="replace").strip()
            or f"Git {args[0]} failed")
    return result.stdout


def snapshot_source(repo, revision):
    commit = git(repo, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}")
    commit = commit.decode("ascii").strip()
    files = {}
    for path in SOURCE_PATHS.values():
        entry = git(repo, "ls-tree", "--full-tree", commit, "--", path)
        fields, separator, entry_path = entry.rstrip(b"\n").partition(b"\t")
        mode, kind, blob = fields.split() if separator else (b"", b"", b"")
        require(entry_path == path.encode() and kind == b"blob" and mode in (b"100644", b"100755"),
                f"revision must contain a regular source file at {path}")
        content = git(repo, "cat-file", "blob", blob.decode("ascii"))
        files[path] = {"sha256": digest(content), "text": content.decode("utf-8")}
    return {"revision": commit, "files": files}


def verify_source(source, hashes):
    require(re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", source["revision"]),
            "source manifest requires a full Git revision")
    files = source["files"]
    require(set(files) == set(SOURCE_PATHS.values()), "source manifest paths differ from the allowlist")
    for key, path in SOURCE_PATHS.items():
        content_hash = digest(files[path]["text"].encode("utf-8"))
        require(content_hash == files[path]["sha256"] == hashes[key],
                f"source revision/hash mismatch for {path}")


def fetch_telemetry(origin, interval, raw_path, metadata):
    url = urlsplit(origin)
    require(url.scheme in ("http", "https") and url.hostname and url.username is None
            and url.password is None and url.path in ("", "/") and not (url.query or url.fragment),
            "observed URL must be an HTTP(S) origin")
    connect = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    connection = connect(url.hostname, url.port, timeout=10)
    try:
        connection.request("GET", "/telemetry?" + urlencode(interval))
        with connection.getresponse() as response, raw_path.open("xb") as raw:
            metadata["http_status"] = response.status
            while chunk := response.read1(65536):
                raw.write(chunk)
            raw.flush()
            os.fsync(raw.fileno())
            require(response.length in (None, 0), "incomplete telemetry response body")
            require(response.status == 200, f"telemetry endpoint returned HTTP {response.status}")
    finally:
        connection.close()


def observation_index(telemetry, interval, expected_boot=None):
    require(type(telemetry["schema_version"]) is int and telemetry["schema_version"] == SCHEMA_VERSION,
            "unsupported telemetry schema")
    boot = telemetry["boot_id"]
    require(isinstance(boot, str) and str(UUID(boot)) == boot, "invalid telemetry boot ID")
    require(expected_boot is None or expected_boot == boot, "observed boot ID mismatch")
    require(telemetry["interval"] == interval, "telemetry interval mismatch")
    hashes = telemetry["source_hashes"]
    require(hashes.keys() == SOURCE_PATHS.keys() and all(re.fullmatch(r"[0-9a-f]{64}", item)
            for item in hashes.values()), "invalid telemetry source hashes")

    since, until = (parse_timestamp(interval[field]) for field in ("since", "until"))
    coverage = telemetry["coverage"]
    started = parse_timestamp(coverage["process_started_at"])
    observed = parse_timestamp(coverage["observed_at"])
    evicted = coverage["latest_evicted_at"]
    limit, dropped = coverage["retention_limit"], coverage["dropped_records"]
    require(type(limit) is int and limit > 0 and type(dropped) is int and dropped >= 0
            and (dropped > 0) == (evicted is not None), "invalid telemetry retention metadata")
    if coverage["oldest_retained_at"] is not None:
        parse_timestamp(coverage["oldest_retained_at"])
    if evicted is not None:
        evicted = parse_timestamp(evicted)
    flags = {
        "startup_truncated": since < until and since < started,
        "eviction_truncated": since < until and evicted is not None and since <= evicted,
        "future_truncated": since < until and until > observed,
    }
    require(all(coverage[key] is value for key, value in flags.items())
            and coverage["complete"] is (not any(flags.values())), "inconsistent telemetry coverage")
    missing = ", ".join(key for key, value in flags.items() if value)
    require(coverage["complete"], f"incomplete telemetry history ({missing})")

    index = {}
    stamps = {}
    for array, record_kind in (("samples", "sample"), ("events", "event")):
        require(isinstance(telemetry[array], list) and len(telemetry[array]) <= limit,
                f"invalid telemetry {array} array")
        previous = 0
        for offset, record in enumerate(telemetry[array]):
            sequence, timestamp, kind = (record[key] for key in ("sequence", "timestamp", "kind"))
            require(type(sequence) is int and sequence > previous and since <= parse_timestamp(timestamp) < until,
                    f"invalid telemetry {array} ordering or timestamp")
            stamp = (timestamp, kind)
            if record_kind == "sample":
                require(kind in LIFECYCLE_KINDS | {"checkpoint"}
                        and all(type(record[key]) is int and record[key] >= 0
                                for key in ("active_requests", "checked_out")), "invalid pool sample")
                if kind != "checkpoint":
                    stamps[sequence] = stamp
            else:
                require(stamps.pop(sequence, None) == stamp and isinstance(record["request_id"], str),
                        "lifecycle event lacks a matching pool sample or request ID")
            index[f"obs/{boot}/{sequence}/{record_kind}"] = {"array": array, "offset": offset}
            previous = sequence
    require(not stamps, "lifecycle pool sample lacks a matching event")
    return index


def capture(origin, since, until, revision, output, *, repo=Path("."), expected_boot=None):
    """Write a new capture. A published index is the completion marker."""
    directory = Path(output)
    directory.mkdir(parents=True)
    raw_path, metadata_path, source_path = (directory / name for name in ARTIFACTS)
    metadata = {"schema_version": SCHEMA_VERSION, "origin": origin, "requested_revision": revision,
                "requested_interval": {"since": since, "until": until}, "expected_boot_id": expected_boot,
                "started_at": utc_now(), "status": "capturing"}
    try:
        write_json(metadata_path, metadata)
        lower, upper = parse_timestamp(since), parse_timestamp(until)
        require(lower <= upper, "until must be at or after since")
        interval = {"since": lower.isoformat(), "until": upper.isoformat()}
        metadata["interval"] = interval
        fetch_telemetry(origin, interval, raw_path, metadata)
        metadata["captured_at"] = utc_now()
        raw = raw_path.read_bytes()
        metadata["telemetry_sha256"] = digest(raw)
        telemetry = json.loads(raw)
        for key in ("coverage", "boot_id", "source_hashes"):
            metadata[key] = telemetry[key]
        observations = observation_index(telemetry, interval, expected_boot)
        source = snapshot_source(repo, revision)
        metadata["revision"] = source["revision"]
        verify_source(source, telemetry["source_hashes"])
        write_json(source_path, source)
        metadata["status"] = "complete"
        write_json(metadata_path, metadata)
        write_json(directory / "index.json", {
            "schema_version": SCHEMA_VERSION,
            "artifacts": {name: digest((directory / name).read_bytes()) for name in ARTIFACTS},
            "observations": observations,
        })
    except (EvidenceError, OSError, ValueError, TypeError, KeyError, IndexError, AttributeError,
            subprocess.SubprocessError, http.client.HTTPException, KeyboardInterrupt) as error:
        reason = str(error) or type(error).__name__
        metadata.update(status="failed", failed_at=utc_now(), failure_reason=reason)
        if raw_path.is_file() and "telemetry_sha256" not in metadata:
            metadata["telemetry_sha256"] = digest(raw_path.read_bytes())
        write_json(metadata_path, metadata)
        raise EvidenceError(reason) from error
    return directory


class Evidence:
    def __init__(self, directory):
        directory = Path(directory)
        try:
            index_path = directory / "index.json"
            require(index_path.is_file(), "capture is incomplete or unavailable: missing index.json")
            require(not index_path.is_symlink(), "capture index must be a regular file")
            index = json.loads(index_path.read_bytes())
            require(index["schema_version"] == SCHEMA_VERSION and set(index["artifacts"]) == set(ARTIFACTS),
                    "invalid capture index")
            documents = {}
            for name in ARTIFACTS:
                path = directory / name
                require(path.is_file() and not path.is_symlink(), f"capture artifact must be a regular file: {name}")
                content = path.read_bytes()
                require(digest(content) == index["artifacts"][name], f"capture hash mismatch for {name}")
                documents[name] = json.loads(content)
            self.telemetry, self.metadata, self._source = (documents[name] for name in ARTIFACTS)
            self._observations = observation_index(self.telemetry, self.metadata["interval"],
                                                   self.metadata["expected_boot_id"])
            require(self.metadata["status"] == "complete" and self._observations == index["observations"]
                    and self.metadata["telemetry_sha256"] == index["artifacts"]["telemetry.json"]
                    and self.metadata["revision"] == self._source["revision"]
                    and all(self.metadata[key] == self.telemetry[key]
                            for key in ("boot_id", "coverage", "source_hashes")), "inconsistent capture identity")
            verify_source(self._source, self.telemetry["source_hashes"])
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
            raise EvidenceError(f"invalid capture: {error}") from error

    def observation_ids(self):
        return list(self._observations)

    def resolve(self, observation_id):
        require(observation_id in self._observations, f"observation unavailable: {observation_id}")
        entry = self._observations[observation_id]
        return dict(self.telemetry[entry["array"]][entry["offset"]])

    def list_source(self):
        return {"revision": self._source["revision"],
                "files": {path: entry["sha256"] for path, entry in self._source["files"].items()}}

    def read_source(self, path, start, end):
        require(path in SOURCE_PATHS.values(), f"source path is outside the allowlist: {path}")
        require(type(start) is int and type(end) is int and 1 <= start <= end < start + SOURCE_LINE_LIMIT,
                f"source range must use inclusive line numbers and at most {SOURCE_LINE_LIMIT} lines")
        lines = io.StringIO(self._source["files"][path]["text"], newline="").readlines()
        require(end <= len(lines), f"source range exceeds {len(lines)} lines in {path}")
        return {"revision": self._source["revision"], "path": path, "start_line": start,
                "end_line": end, "text": "".join(lines[start - 1:end])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("capture", help="capture a telemetry window and its incident source")
    for option in ("url", "since", "until", "revision", "output"):
        start.add_argument(f"--{option}", required=True)
    start.add_argument("--repo", type=Path, default=Path("."))
    start.add_argument("--boot-id", help="expected boot ID from the observed instance")
    commands.add_parser("list-source", help="list the saved read-only source files").add_argument("directory")
    read = commands.add_parser("read-source", help="read an inclusive range from saved source")
    read.add_argument("directory")
    read.add_argument("path")
    read.add_argument("start", type=int)
    read.add_argument("end", type=int)
    resolve = commands.add_parser("resolve", help="resolve a saved observation ID")
    resolve.add_argument("directory")
    resolve.add_argument("observation_id")
    args = parser.parse_args()
    try:
        if args.command == "capture":
            directory = capture(args.url, args.since, args.until, args.revision, args.output,
                                repo=args.repo, expected_boot=args.boot_id)
            print(directory)
        else:
            evidence = Evidence(args.directory)
            if args.command == "list-source":
                result = evidence.list_source()
            elif args.command == "read-source":
                result = evidence.read_source(args.path, args.start, args.end)
            else:
                result = evidence.resolve(args.observation_id)
            print(json.dumps(result, indent=2))
    except (EvidenceError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
