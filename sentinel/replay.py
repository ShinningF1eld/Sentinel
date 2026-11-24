"""Operator-side replay manifest for the calibrated Sentinel demo."""

import json
from pathlib import Path

from sentinel.evidence import Evidence, digest
from spike.trial import ROOT, SOURCE_FILES
from spike.workload import FIXTURE, require, validate_config


SOURCE_INPUTS = (*SOURCE_FILES, "spike/config.json", "sentinel/evidence.py",
                 "sentinel/replay.py", "sentinel/runner.py", "sentinel/baseline.py")
IMAGE_TAGS = {"api": "sentinel-m1-api:local", "db": (
    "postgres:17.5-bookworm@sha256:fbcea1bd13b6a882cd6caa6b58db3ae5c102efe50ec625b3e2a5cbc50db5bfe4"
)}
LIMITS = {
    "setup_seconds": 60, "trial_seconds": 120, "cleanup_seconds": 45,
    "command_seconds": 20, "command_output_bytes": 256 * 1024,
    "http_body_bytes": 64 * 1024,
    "services": {"api": {"cpus": 1, "memory_bytes": 256 * 1024**2, "pids": 64},
                 "db": {"cpus": 1, "memory_bytes": 512 * 1024**2, "pids": 128}},
}
GAPS = [
    "The replay starts from a synthetic database and omits observed database history.",
    "M2 attests to captured incident source. Replay checks image, package, and telemetry module identity.",
    "The sequential local workload omits observed client traffic and topology.",
]
IMAGE_FORMAT = ('{"id":{{json .Id}},"digests":{{json .RepoDigests}},'
                '"os":{{json .Os}},"architecture":{{json .Architecture}}}')


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"manifest contains duplicate field {key}")
        result[key] = value
    return result


def matches(actual, expected, field="manifest"):
    if isinstance(expected, dict):
        require(isinstance(actual, dict), f"{field}: expected an object")
        for key, value in expected.items():
            require(key in actual, f"missing required field {field}.{key}")
            matches(actual[key], value, f"{field}.{key}")
        require(actual.keys() == expected.keys(), f"{field}: unknown fields {sorted(actual.keys() - expected.keys())}")
    else:
        require(type(actual) is type(expected) and actual == expected,
                f"{field}: expected {expected!r}, got {actual!r}")


def local_inputs(directory):
    index = (directory / "index.json").read_bytes()
    capture = Evidence(directory)
    require((directory / "index.json").read_bytes() == index, "M2 capture index changed during validation")
    require(capture.observation_ids(), "M2 capture contains no observations")
    source = capture.list_source()
    files = {name: (ROOT / name).read_bytes() for name in SOURCE_INPUTS}
    hashes = {name: digest(content) for name, content in files.items()}
    for name, expected in source["files"].items():
        matches(hashes[name], expected, f"local source {name} against M2 capture")
    return {
        "schema_version": 1,
        "capture": {"index_sha256": digest(index), "boot_id": capture.metadata["boot_id"]},
        "source_revision": source["revision"], "source_hashes": hashes,
        "dependencies": files["requirements.txt"].decode("utf-8").splitlines(),
        "fixture": {"version": FIXTURE["version"], "sha256": hashes["spike/fixture.sql"],
                    "expected_row": FIXTURE, "reset": "new_project_volume_and_seed_sql"},
        "workload": validate_config(json.loads(files["spike/config.json"].decode("utf-8-sig"))),
        "readiness": {"database": "pg_isready", "api_path": "/ready",
                      "initial_checkpoint": "empty_history_and_quiescent_pool"},
        "limits": LIMITS,
        "fidelity_gaps": GAPS,
    }, files


def resolve_images(commands):
    images = {}
    for service, tag in IMAGE_TAGS.items():
        identity = json.loads(commands.run("docker", "image", "inspect", "--format", IMAGE_FORMAT, tag))
        images[service] = {"tag": tag, **identity, "digests": sorted(identity["digests"] or [])}
    return images


def validate_manifest(path, directory, commands):
    with Path(path).open("rb") as stream:
        raw = stream.read(64 * 1024 + 1)
    require(len(raw) <= 64 * 1024, "replay manifest exceeds 64 KiB")
    manifest = json.loads(raw, object_pairs_hook=unique_keys)
    require(isinstance(manifest, dict), "manifest: expected an object")
    expected, files = local_inputs(directory)
    require("images" in manifest, "missing required field manifest.images")
    matches({key: value for key, value in manifest.items() if key != "images"}, expected)
    matches(manifest["images"], resolve_images(commands), "manifest.images")
    return raw, manifest, files
