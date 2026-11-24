import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from time import monotonic
from uuid import uuid4

from spike.workload import TrialError, Workload, assess, load_config, require, utc, write_json


ROOT = Path(__file__).resolve().parents[1]
SERVICES = ("api", "db")
SOURCE_FILES = (
    ".dockerignore", "Dockerfile", "compose.yaml", "requirements.txt", "demo/app.py",
    "demo/incident.py", "demo/telemetry.py", "spike/fixture.sql", "spike/healthy.py", "spike/healthy.compose.yaml",
    "spike/trial.py", "spike/workload.py", "spike/series.py",
)


class Commands:
    def __init__(self, output, env):
        self.output = output
        self.env = env

    def note(self, line):
        with (self.output / "commands.txt").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    def record(self, command, outcome, equivalent=False):
        prefix = "HTTP equivalent" if equivalent else "command"
        self.note(f"{utc()} {prefix} {subprocess.list2cmdline(command)} [{outcome}]")

    def run(self, *command, timeout=20, check=True):
        self.record(command, "started")
        try:
            result = subprocess.run(command, cwd=ROOT, env=self.env, timeout=timeout,
                                    capture_output=True, text=True, encoding="utf-8", errors="replace")
        except BaseException as error:
            self.record(command, type(error).__name__)
            raise
        self.record(command, f"exit {result.returncode}")
        if result.returncode and check:
            detail = result.stderr.strip() or result.stdout.strip()
            raise TrialError(f"{subprocess.list2cmdline(command)}: exit {result.returncode}: {detail}")
        return result.stdout.strip()


class Project:
    def __init__(self, name, variant, commands):
        self.name = name
        self.commands = commands
        self.label = "label=com.docker.compose.project=" + name
        self.compose_args = ["docker", "compose", "-p", name, "-f", "compose.yaml"]
        if variant == "healthy":
            self.compose_args += ["-f", "spike/healthy.compose.yaml"]

    def compose(self, *args, **kwargs):
        return self.commands.run(*self.compose_args, *args, **kwargs)

    def resources(self):
        return {
            "containers": self.commands.run(
                "docker", "ps", "-aq", "--no-trunc", "--filter", self.label
            ).splitlines(),
            "networks": self.commands.run(
                "docker", "network", "ls", "-q", "--no-trunc", "--filter", self.label
            ).splitlines(),
            "volumes": self.commands.run(
                "docker", "volume", "ls", "-q", "--filter", self.label
            ).splitlines(),
        }

    def service_id(self, service):
        container = self.compose("ps", "-q", service)
        require(container and "\n" not in container, f"expected one running {service} container")
        return container

    def remove(self):
        self.compose("down", "--volumes", "--remove-orphans", "--timeout", "5", timeout=30)


def source_fingerprint():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def sample_resources(commands, containers, phase, origin):
    started = monotonic()
    output = commands.run("docker", "stats", "--no-stream", "--no-trunc", "--format", "{{json .}}",
                          *containers.values(), timeout=12)
    rows = {row["ID"]: row for row in map(json.loads, output.splitlines())}
    usage = {}
    for service, container in containers.items():
        require(container in rows, f"{service} resource sample is missing")
        row = rows[container]
        cpu = float(row["CPUPerc"].removesuffix("%"))
        memory = row["MemUsage"].split(" / ")[0]
        require(row["CPUPerc"].endswith("%") and 0 <= cpu < float("inf")
                and memory and memory != "--", f"{service} resource sample is invalid")
        usage[service] = {"cpu_percent": cpu, "memory_usage": memory, "raw": row}
    return {"phase": phase, "timestamp": utc(), "elapsed_seconds": monotonic() - origin,
            "capture_seconds": monotonic() - started, "services": usage}


def run_trial(variant, config_path, output, series_id=None, project_name=None, frozen=None, pair=None):
    output = Path(output).resolve()
    project_name = project_name or "sentinel-m1-" + uuid4().hex[:16]
    require(re.fullmatch(r"sentinel-m1-[a-z0-9][a-z0-9-]{0,39}", project_name),
            "project name must use the sentinel-m1- prefix followed by 1–40 lowercase letters, digits or hyphens")
    output.mkdir(parents=True, exist_ok=True)
    require(not any(output.iterdir()), f"output directory is populated: {output}")
    with (output / "commands.txt").open("x", encoding="utf-8"):
        pass

    origin = monotonic()
    fingerprint = {
        "attempt_id": output.name, "series_id": series_id, "pair": pair, "variant": variant,
        "compose_project": project_name, "host_os": platform.platform(), "python": platform.python_version(),
    }
    result = {
        "status": "setup_error", "started_at": utc(), "valid": False, "expectation_met": False,
        "last_completed_step": None, "setup_elapsed_seconds": None, "workload_elapsed_seconds": None,
        "resource_samples": [], "cleanup": {"status": "not_owned"},
    }
    commands = Commands(output, os.environ.copy())
    project = Project(project_name, variant, commands)
    owns_project = False
    workload_start = None
    clean_start = False

    def save():
        write_json(output / "config.json", fingerprint)
        write_json(output / "trial.json", result)

    def completed(step):
        result["last_completed_step"] = step
        save()

    variant_args = ["-f", "spike/healthy.compose.yaml"] if variant == "healthy" else []
    commands.note(f"Working directory: {ROOT}")
    commands.note("Image preparation: docker compose pull db")
    commands.note("Image preparation: docker compose build api")
    invocation = ["python", "-m", "spike.trial", "--variant", variant, "--config",
                  str(config_path), "--output", str(output), "--project", project_name]
    if series_id:
        invocation += ["--series-id", series_id]
    commands.record(invocation, "started")
    write_json(output / "observations.json", {"requests": [], "checkpoints": {}, "snapshots": []})
    save()
    try:
        config = load_config(config_path)
        fingerprint["settings"] = config
        fingerprint["workload_order"] = ["GET /ordinary"] + ["POST /trigger"] * config["trigger_count"] + ["GET /ordinary"]
        fingerprint["source_hashes"] = source_fingerprint()
        fingerprint["fixture_hash"] = fingerprint["source_hashes"]["spike/fixture.sql"]
        fingerprint["git_revision"] = commands.run("git", "rev-parse", "HEAD", check=False) or None
        settings = {
            "M1_POOL_CAPACITY": str(config["pool_capacity"]),
            "M1_ACQUISITION_TIMEOUT_MS": str(config["acquisition_timeout_ms"]),
        }
        commands.env.update(settings)
        fingerprint["compose_environment"] = settings
        for key, value in settings.items():
            commands.note(f"PowerShell environment: $env:{key} = '{value}'")
        fingerprint["docker_client"] = commands.run("docker", "--version")
        fingerprint["compose_version"] = commands.run("docker", "compose", "version", "--short")
        save()
        result["resources_before"] = project.resources()
        require(not any(result["resources_before"].values()), "dirty start: project resources already exist")
        info = json.loads(commands.run(
            "docker", "info", "--format",
            '{"version":{{json .ServerVersion}},"cpus":{{json .NCPU}},"memory_bytes":{{json .MemTotal}},"os":{{json .OperatingSystem}},"architecture":{{json .Architecture}}}',
        ))
        fingerprint["docker_engine"] = info
        fingerprint["images"] = {}
        compose_services = json.loads(project.compose("config", "--format", "json"))["services"]
        for service in SERVICES:
            tag = compose_services[service]["image"]
            identity = json.loads(commands.run(
                "docker", "image", "inspect", tag,
                "--format", '{"id":{{json .Id}},"digests":{{json .RepoDigests}}}',
            ))
            fingerprint["images"][service] = {"tag": tag, **identity}
        fingerprint["dependencies"] = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if frozen is not None:
            require(all(fingerprint[key] == value for key, value in frozen.items()), "frozen inputs changed")
        completed("verified_absent")

        readiness_deadline = monotonic() + config["readiness_timeout_seconds"]
        owns_project = True
        project.compose("up", "-d", "--no-build", "--pull", "never",
                        timeout=config["readiness_timeout_seconds"])
        result["owned_resources"] = project.resources()
        containers = {service: project.service_id(service) for service in SERVICES}
        require(set(containers.values()) == set(result["owned_resources"]["containers"])
                and len(result["owned_resources"]["networks"]) == 1
                and result["owned_resources"]["volumes"] == [f"{project_name}_data"],
                "created resource identities differ from the expected trial topology")
        for service, container in containers.items():
            image = commands.run("docker", "inspect", "--format", "{{.Image}}", container)
            require(image == fingerprint["images"][service]["id"], f"{service} started with a different image")
        volume = f"{project_name}_data"
        result["storage"] = json.loads(commands.run(
            "docker", "volume", "inspect", volume,
            "--format", '{"name":{{json .Name}},"created_at":{{json .CreatedAt}}}',
        ))
        mount = commands.run(
            "docker", "inspect", "--format",
            '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}',
            containers["db"],
        )
        require(mount == volume and result["storage"]["created_at"], "database is missing its fresh project volume")
        binding = project.compose("port", "api", "8000")
        require(re.fullmatch(r"127\.0\.0\.1:\d+", binding), f"unexpected API binding: {binding}")
        workload = Workload("http://" + binding, config, output, commands.record)
        result["readiness"] = workload.ready(readiness_deadline)
        cleanup_source = "spike/healthy.py" if variant == "healthy" else "demo/incident.py"
        require(result["readiness"]["source_hashes"] == {
            "api": fingerprint["source_hashes"]["demo/app.py"],
            "cleanup": fingerprint["source_hashes"][cleanup_source],
        }, "running API source differs from the recorded files; rebuild the image")
        fingerprint["effective"] = {
            key: result["readiness"][key]
            for key in ("pool_capacity", "pool_min_size", "acquisition_timeout_ms")
        }
        result["setup_elapsed_seconds"] = monotonic() - origin
        clean_start = True
        result["clean_start_verified"] = clean_start
        completed("ready")

        result["status"] = "invalid"
        result["resource_samples"].append(sample_resources(commands, containers, "ready", origin))
        save()
        workload_start = monotonic()
        workload.run(completed)
        result["workload_elapsed_seconds"] = monotonic() - workload_start
        assessment = assess(workload.data, result["readiness"], config, variant)
        result["observed_final_outcome"] = assessment["observed_final_outcome"]
        result["expected_final_outcome"] = assessment["expected_final_outcome"]
        result["resource_samples"].append(sample_resources(commands, containers, "final", origin))
        result["expectation_met"] = assessment["expectation_met"]
        result["valid"] = True
        result["status"] = "pass" if result["expectation_met"] else "mismatch"
        if not result["expectation_met"]:
            result["failure_reason"] = "final outcome or quiescent pool count differs from expectation"
    except KeyboardInterrupt:
        result.update(status="interrupted", failure_reason="operator interrupted the attempt")
    except (TrialError, subprocess.SubprocessError, OSError, ValueError, TypeError, KeyError) as error:
        result["failure_reason"] = f"{type(error).__name__}: {error}"
    finally:
        if result["setup_elapsed_seconds"] is None:
            result["setup_elapsed_seconds"] = monotonic() - origin
        if workload_start is not None and result["workload_elapsed_seconds"] is None:
            result["workload_elapsed_seconds"] = monotonic() - workload_start
        samples = result["resource_samples"]
        result["resource_sampling"] = {
            "method": "Docker stats, one sample after readiness and one after the final checkpoint",
            "sample_count": len(samples),
            "interval_seconds": (samples[1]["elapsed_seconds"] - samples[0]["elapsed_seconds"]
                                 if len(samples) == 2 else None),
        }
        result["clean_start_verified"] = clean_start
        save()
        if owns_project:
            finish_project(project, output, result, save)
        result["finished_at"] = utc()
        save()
    exit_code = int(result["status"] != "pass" or result["cleanup"]["status"] != "verified")
    commands.record(invocation, f"exit {exit_code}")
    print(f"{output} [{result['status']}, cleanup={result['cleanup']['status']}]", flush=True)
    return exit_code, fingerprint, result


def finish_project(project, output, result, save):
    cleanup = result["cleanup"] = {"status": "unverified"}
    try:
        result["owned_resources"] = project.resources()
        try:
            logs = project.compose("logs", "--no-color", "--timestamps", "--tail", "200")
            (output / "compose.log").write_text(logs + "\n", encoding="utf-8")
        except (TrialError, OSError, subprocess.SubprocessError) as error:
            result["log_capture_error"] = str(error)
        save()
    except (TrialError, OSError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        cleanup["capture_error"] = str(error) or type(error).__name__
    try:
        project.remove()
    except (TrialError, OSError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        cleanup["teardown_error"] = str(error) or type(error).__name__
    try:
        cleanup["remaining"] = project.resources()
        if not any(cleanup["remaining"].values()) and "teardown_error" not in cleanup:
            cleanup["status"] = "verified"
    except (TrialError, OSError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        cleanup["verification_error"] = str(error) or type(error).__name__


def main():
    parser = argparse.ArgumentParser(description="Run one clean Sentinel M1 Compose trial.")
    parser.add_argument("--variant", required=True, choices=("healthy", "incident"))
    parser.add_argument("--config", default="spike/config.json", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--series-id")
    parser.add_argument("--project", help="optional explicit project name for an operator-owned attempt")
    args = parser.parse_args()
    try:
        code, _, _ = run_trial(args.variant, args.config, args.output, args.series_id, args.project)
        return code
    except (OSError, TrialError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
