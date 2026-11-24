"""Trusted local Compose execution for fixed baseline operations."""

import json
import os
import platform
import shutil
import signal
import subprocess
from pathlib import Path
from threading import Lock, Thread
from time import monotonic

from sentinel.evidence import write_json
from sentinel.replay import IMAGE_TAGS, LIMITS, matches
from spike.trial import Commands, Project, SERVICES, sample_resources
from spike.workload import TrialError, Workload, assess, require, utc


OWNER = "org.sentinel.trial"
FIXTURE_TARGET = "/docker-entrypoint-initdb.d/01-fixture.sql"
CONTROL_TARGET = "/control/healthy.py"
DATA_TARGET = "/var/lib/postgresql/data"
HTTP_CLIENT = """
import http.client, json, sys
method, path, timeout, request_id, cap = sys.argv[1:]
connection = http.client.HTTPConnection("127.0.0.1", 8000, timeout=float(timeout))
try:
    headers = {"X-Request-ID": request_id} if request_id else {}
    connection.request(method, path, headers=headers)
    with connection.getresponse() as response:
        body = response.read(int(cap) + 1)
        if len(body) > int(cap) or response.length not in (0, None):
            raise RuntimeError("oversized or incomplete replay HTTP body")
        print(json.dumps([response.status, body.decode("utf-8")], ensure_ascii=False))
except OSError:
    if path != "/ready":
        raise
    print(json.dumps([0, "{}"]))
finally:
    connection.close()
"""
RUNTIME_CODE = """
import hashlib, importlib.metadata, json
from pathlib import Path
print(json.dumps({
    "telemetry_sha256": hashlib.sha256(Path("/app/demo/telemetry.py").read_bytes()).hexdigest(),
    "packages": {name: importlib.metadata.version(name) for name in
                 ("psycopg", "psycopg-binary", "psycopg-pool", "typing-extensions")},
}))
"""
POOL_QUERY = ("SELECT json_agg(json_build_array(pid, backend_start) ORDER BY pid) "
              "FROM pg_stat_activity WHERE client_addr IS NOT NULL AND datname = 'sentinel'")
IMAGE_ENV = {
    "api": {"PATH", "LANG", "GPG_KEY", "PYTHON_VERSION", "PYTHON_SHA256",
            "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED"},
    "db": {"PATH", "LANG", "PG_MAJOR", "PG_VERSION", "PGDATA", "GOSU_VERSION", "PG_SHA256",
           "DOCKER_PG_LLVM_DEPS", "PG_OSES", "PG_GID", "PG_UID"},
}
RUN_ERRORS = (TrialError, OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError,
              AttributeError, IndexError)


def prepare_client(directory):
    directory.mkdir()
    executable = shutil.which("docker")
    require(executable, "Docker CLI was not found")
    # Docker Desktop bundles Compose beside the CLI. Use that installation without loading
    # the operator's Docker contexts, credential helpers, proxies, or registry configuration.
    plugins = Path(executable).resolve().parent.parent / "cli-plugins"
    write_json(directory / "config.json",
               {"cliPluginsExtraDirs": [str(plugins)]} if plugins.is_dir() else {})


class BoundedCommands(Commands):
    def __init__(self, output, client_config, seconds):
        env = {key: os.environ[key] for key in (
            "PATH", "SystemRoot", "WINDIR", "SYSTEMDRIVE", "ProgramFiles", "ProgramData",
        ) if key in os.environ}
        env.update(DOCKER_CONFIG=str(client_config), COMPOSE_DISABLE_ENV_FILE="1")
        super().__init__(output, env)
        self.deadline = monotonic() + seconds
        config = json.loads((client_config / "config.json").read_bytes())
        self.compose_plugin = None
        if os.name == "nt":
            for folder in config.get("cliPluginsExtraDirs", []):
                candidate = Path(folder) / "docker-compose.exe"
                if candidate.is_file():
                    self.compose_plugin = str(candidate)
                    break

    def remaining(self):
        remaining = self.deadline - monotonic()
        require(remaining > 0, "execution deadline expired")
        return remaining

    def run(self, *command, timeout=20):
        require(command[0] == "docker", "unsupported runner executable")
        timeout = min(timeout, LIMITS["command_seconds"], self.remaining())
        require(timeout > 0, "command deadline expired before launch")
        self.record(command, "started")
        buffers = [bytearray(), bytearray()]
        lock = Lock()
        overflow = False
        cap = LIMITS["command_output_bytes"]
        # Invoke the bundled Windows plugin directly so killing the client also closes its pipes.
        argv = (self.compose_plugin, *command[2:]) if self.compose_plugin and command[1] == "compose" else command

        with subprocess.Popen(argv, cwd=self.output, env=self.env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              start_new_session=os.name != "nt") as process:
            def stop():
                try:
                    if os.name != "nt":
                        os.killpg(process.pid, signal.SIGKILL)
                    elif process.poll() is None:
                        process.kill()
                except ProcessLookupError:
                    pass

            def collect(stream, buffer):
                nonlocal overflow
                while chunk := stream.read1(8192):
                    with lock:
                        available = cap - sum(map(len, buffers))
                        buffer.extend(chunk[:available])
                        if len(chunk) > available:
                            overflow = True
                            stop()
                            return

            readers = [Thread(target=collect, args=(stream, buffer), daemon=True)
                       for stream, buffer in zip((process.stdout, process.stderr), buffers)]
            for reader in readers:
                reader.start()
            try:
                until = monotonic() + timeout
                process.wait(timeout=max(0, until - monotonic()))
                require(not overflow, f"command output exceeded {cap} bytes")
                for reader in readers:
                    reader.join(max(0, until - monotonic()))
                require(not overflow, f"command output exceeded {cap} bytes")
                require(not any(reader.is_alive() for reader in readers), "command output deadline expired")
            except (TrialError, subprocess.TimeoutExpired, KeyboardInterrupt) as error:
                stop()
                self.record(command, str(error) if isinstance(error, TrialError) else type(error).__name__)
                with lock:
                    self.save_command_failure(buffers)
                raise
        stdout, stderr = (buffer.decode("utf-8", errors="replace").strip() for buffer in buffers)
        self.record(command, f"exit {process.returncode}")
        if process.returncode:
            self.save_command_failure(buffers)
            raise TrialError(f"Docker {command[1]} exited with {process.returncode}: {(stderr or stdout)[:1500]}")
        return stdout

    def save_command_failure(self, buffers):
        with (self.output / "command-failures.txt").open("ab") as stream:
            stream.write(b"stdout\n" + buffers[0] + b"\nstderr\n" + buffers[1] + b"\n")


class BaselineProject(Project):
    def __init__(self, name, commands, plan, owner):
        super().__init__(name, "incident", commands)
        self.compose_args = ["docker", "compose", "-p", name, "-f", str(plan)]
        self.owner = owner

    def inspect(self, kind, identity, template):
        return json.loads(self.commands.run("docker", kind, "inspect", "--format", template, identity))

    def owned(self, resources):
        approved = {kind: [] for kind in resources}
        for kind, identities in resources.items():
            singular = {"containers": "container", "networks": "network", "volumes": "volume"}[kind]
            template = "{{json .Config.Labels}}" if kind == "containers" else "{{json .Labels}}"
            for identity in identities:
                labels = self.inspect(singular, identity, template) or {}
                if labels.get(OWNER) == self.owner and labels.get("com.docker.compose.project") == self.name:
                    approved[kind].append(identity)
        return approved

    def prepare(self):
        found = self.resources()
        require(not any(found.values()), "dirty start: project resources already exist")
        for kind, name in (("volume", self.name + "_data"), ("network", self.name + "_default")):
            names = self.commands.run("docker", kind, "ls", "--filter", f"name={name}",
                                      "--format", "{{.Name}}").splitlines()
            require(name not in names, f"dirty start: {kind} {name} already exists")
        return found

    def exec(self, service, *args, timeout=20):
        return self.compose("exec", "-T", "--interactive=false", service, *args, timeout=timeout)

    def transport(self, method, path, timeout, request_id):
        require((method, path) in {("GET", "/ready"), ("GET", "/diagnostics"),
                                   ("GET", "/ordinary"), ("POST", "/trigger")}, "unsupported workload request")
        timeout = min(timeout, self.commands.remaining())
        status, body = json.loads(self.exec(
            "api", "python", "-c", HTTP_CLIENT, method, path, str(timeout), request_id or "",
            str(LIMITS["http_body_bytes"]), timeout=timeout,
        ))
        return status, body

    def cleanup(self, result):
        cleanup = result["cleanup"] = {"status": "unverified", "errors": []}
        try:
            resources = self.resources()
            result["owned_resources"] = self.owned(resources)
            require(resources == result["owned_resources"], "project inventory contains foreign resources")
            try:
                logs = self.compose("logs", "--no-color", "--timestamps", "--tail", "100", timeout=5)
                (self.commands.output / "compose.log").write_text(logs + "\n", encoding="utf-8")
            except (TrialError, OSError, subprocess.SubprocessError) as error:
                cleanup["errors"].append(f"log capture: {error}")
        except (TrialError, OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as error:
            cleanup["errors"].append(str(error) or type(error).__name__)
        for kind, args in (("containers", ("container", "rm", "-f")),
                           ("networks", ("network", "rm")), ("volumes", ("volume", "rm"))):
            try:
                current = self.owned({kind: self.resources()[kind]})[kind]
                if current:
                    self.commands.run("docker", *args, *current, timeout=8)
            except (TrialError, OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as error:
                cleanup["errors"].append(str(error) or type(error).__name__)
        try:
            cleanup["remaining"] = self.resources()
            if not any(cleanup["remaining"].values()) and not cleanup["errors"]:
                cleanup["status"] = "verified"
        except (TrialError, OSError, subprocess.SubprocessError, KeyboardInterrupt) as error:
            cleanup["errors"].append(str(error) or type(error).__name__)


def stage_plans(directory, commands, files, manifest, slots):
    stage = directory / "inputs"
    (stage / "spike").mkdir(parents=True)
    for name in ("compose.yaml", "spike/healthy.compose.yaml", "spike/healthy.py", "spike/fixture.sql"):
        (stage / name).write_bytes(files[name])
    config = manifest["workload"]
    commands.env.update(M1_POOL_CAPACITY=str(config["pool_capacity"]),
                        M1_ACQUISITION_TIMEOUT_MS=str(config["acquisition_timeout_ms"]))
    projects = {}
    plans = {}
    for variant, slot in slots.items():
        name, owner = slot["project"], slot["owner"]
        args = ["-f", str(stage / "compose.yaml")]
        if variant == "healthy":
            args += ["-f", str(stage / "spike/healthy.compose.yaml")]
        base = json.loads(commands.run("docker", "compose", "-p", name, *args, "config", "--format", "json"))
        require(set(base) == {"name", "services", "volumes", "networks"}
                and set(base["services"]) == set(SERVICES), "unexpected base Compose topology")
        services = base["services"]
        for service, spec in services.items():
            require(spec["image"] == IMAGE_TAGS[service], f"unexpected {service} Compose image")
            spec.pop("build", None)
            spec.pop("ports", None)
            for key in ("command", "entrypoint"):
                require(spec.pop(key, None) is None, f"unexpected {service} {key}")
            require(set(spec) <= {"image", "environment", "volumes", "healthcheck", "depends_on",
                                  "cpus", "mem_limit", "pids_limit", "networks", "security_opt"},
                    f"unsupported Compose settings for {service}")
            spec.update(image=manifest["images"][service]["id"], networks={"default": None},
                        labels={OWNER: owner}, read_only=True, dns=["127.0.0.1"],
                        memswap_limit=spec["mem_limit"],
                        security_opt=["no-new-privileges:true"],
                        logging={"driver": "local", "options": {"max-size": "1m", "max-file": "2"}})
        env = {"PGHOST": "db", "PGDATABASE": "sentinel", "PGUSER": "sentinel", "PGCONNECT_TIMEOUT": "3",
               "POOL_CAPACITY": str(config["pool_capacity"]),
               "ACQUISITION_TIMEOUT_MS": str(config["acquisition_timeout_ms"])}
        if variant == "healthy":
            env.update(CLEANUP_MODULE="healthy", PYTHONPATH="/control")
        matches(services["api"]["environment"], env, "Compose API environment")
        matches(services["db"]["environment"],
                {"POSTGRES_DB": "sentinel", "POSTGRES_USER": "sentinel", "POSTGRES_HOST_AUTH_METHOD": "trust"},
                "Compose database environment")
        for service in SERVICES:
            expected = [(FIXTURE_TARGET, "bind", str(stage / "spike/fixture.sql"), True),
                        (DATA_TARGET, "volume", "data", False)] if service == "db" else []
            if variant == "healthy" and service == "api":
                expected.append((CONTROL_TARGET, "bind", str(stage / "spike/healthy.py"), True))
            mounts = services[service].get("volumes", [])
            actual = [(m["target"], m["type"], os.path.normcase(os.path.normpath(m["source"])),
                       m.get("read_only", False)) for m in mounts]
            require(sorted(actual) == sorted((target, kind, os.path.normcase(os.path.normpath(source)), ro)
                                             for target, kind, source, ro in expected),
                    f"unexpected Compose mounts for {service}")
        services["api"]["cap_drop"] = ["ALL"]
        services["db"]["tmpfs"] = ["/tmp:size=32m,mode=1777", "/var/run/postgresql:size=16m,mode=3777"]
        plans[variant] = {"services": services,
                          "networks": {"default": {"internal": True, "driver": "bridge", "enable_ipv6": False,
                                                  "driver_opts": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
                                                  "labels": {OWNER: owner}}},
                          "volumes": {"data": {"labels": {OWNER: owner}}}}
        plan = stage / f"{variant}.json"
        write_json(plan, plans[variant])
        projects[variant] = BaselineProject(name, commands, plan, owner)
        projects[variant].compose("config", "--quiet")
    # The stock override may add only the cleanup selector and its read-only source mount.
    healthy = plans["healthy"]["services"]["api"]
    incident = plans["incident"]["services"]["api"]
    matches(plans["healthy"]["services"]["db"] | {"labels": {}},
            plans["incident"]["services"]["db"] | {"labels": {}}, "paired database configuration")
    healthy.pop("volumes")
    require(healthy["environment"].pop("CLEANUP_MODULE") == "healthy"
            and healthy["environment"].pop("PYTHONPATH") == "/control", "unexpected control selector")
    matches(healthy | {"labels": {}}, incident | {"labels": {}}, "paired application configuration")
    for variant, project in projects.items():
        slots[variant]["resources_before"] = project.prepare()
    return projects


def inspect_runtime(project, images, variant, resources):
    require(resources == project.owned(resources) and len(resources["containers"]) == 2
            and len(resources["networks"]) == 1 and resources["volumes"] == [project.name + "_data"],
            "created resources do not match the owned trial topology")
    network = project.inspect("network", resources["networks"][0],
                              '{"name":{{json .Name}},"internal":{{json .Internal}},'
                              '"driver":{{json .Driver}},"options":{{json .Options}},"ipv6":{{json .EnableIPv6}}}')
    require(network["internal"] is True and network["driver"] == "bridge"
            and network["ipv6"] is False
            and network["name"] == project.name + "_default"
            and network["options"].get("com.docker.network.bridge.gateway_mode_ipv4") == "isolated",
            "trial network does not enforce isolation")
    runtime = {}
    planned_services = json.loads(project.compose("config", "--format", "json"))["services"]
    for container in resources["containers"]:
        data = project.inspect("container", container, '{{json .}}')
        service = data["Config"]["Labels"].get("com.docker.compose.service")
        require(service in SERVICES and service not in runtime, "unexpected trial service")
        host = data["HostConfig"]
        limits = LIMITS["services"][service]
        require(host["NanoCpus"] == limits["cpus"] * 10**9
                and host["Memory"] == host["MemorySwap"] == limits["memory_bytes"]
                and host["PidsLimit"] == limits["pids"], f"{service} execution limits differ")
        require(data["Image"] == images[service]["id"] and host["ReadonlyRootfs"]
                and not host["Privileged"] and {"no-new-privileges:true"} <= set(host["SecurityOpt"])
                and not host["PortBindings"] and host["NetworkMode"] == network["name"]
                and set(data["NetworkSettings"]["Networks"]) == {network["name"]}
                and host["Dns"] == ["127.0.0.1"] and not host["CapAdd"] and not host["Devices"]
                and host["PidMode"] == "" and host["RestartPolicy"]["Name"] in ("no", "")
                and host["LogConfig"] == {"Type": "local", "Config": {"max-size": "1m", "max-file": "2"}},
                f"{service} runtime isolation differs")
        if service == "api":
            require(data["Config"]["User"] == "65534:65534" and host["CapDrop"] == ["ALL"],
                    "API user or capabilities differ")
        planned = planned_services[service]
        image_env = project.inspect("image", images[service]["id"], "{{json .Config.Env}}")
        defaults = dict(entry.split("=", 1) for entry in image_env)
        require(defaults.keys() <= IMAGE_ENV[service], f"{service} image contains undeclared environment keys")
        actual_env = dict(entry.split("=", 1) for entry in data["Config"]["Env"])
        require(actual_env == defaults | planned["environment"], f"{service} effective environment differs")
        mounts = {mount["Destination"]: mount for mount in data["Mounts"]}
        expected = {DATA_TARGET, FIXTURE_TARGET} if service == "db" else set()
        if service == "api" and variant == "healthy":
            expected.add(CONTROL_TARGET)
        tmpfs = host.get("Tmpfs") or {}
        require(set(tmpfs) == ({"/tmp", "/var/run/postgresql"} if service == "db" else set())
                and set(mounts) - set(tmpfs) == expected, f"{service} has unexpected mounts")
        for target in expected:
            mount = mounts[target]
            if target == DATA_TARGET:
                require(mount["Type"] == "volume" and mount["RW"] and mount["Name"] == project.name + "_data",
                        "database data volume differs")
            else:
                require(mount["Type"] == "bind" and not mount["RW"], f"{target} mount is not read-only")
        runtime[service] = {"container": container, "image": data["Image"], "limits": limits,
                            "read_only_root": True, "swap_bytes": 0, "network": network, "published_ports": [],
                            "mounts": list(mounts.values()), "tmpfs": tmpfs,
                            "environment": actual_env, "user": data["Config"]["User"]}
    require(set(runtime) == set(SERVICES), "trial services missing")
    return runtime


def run_trial(project, slot, manifest, manifest_hash, client_config):
    origin = monotonic()
    directory = client_config.parent / slot["variant"]
    directory.mkdir()
    result = slot | {"status": "setup_error", "started_at": utc(), "manifest_sha256": manifest_hash,
                     "valid": False, "reproduction": "unassessed", "last_completed_step": None,
                     "cleanup": {"status": "not_owned"}, "resource_samples": []}
    project.commands = BoundedCommands(directory, client_config, LIMITS["trial_seconds"])
    config = manifest["workload"]
    owns_project = False

    def save(step=None):
        if step:
            result["last_completed_step"] = step
        write_json(directory / "trial.json", result)

    save()
    try:
        result["resources_before"] = project.prepare()
        result["environment"] = {
            "workload": config, "limits": LIMITS, "images": manifest["images"],
            "host": platform.platform(), "python": platform.python_version(),
            "docker": project.commands.run("docker", "version", "--format", "{{json .}}"),
            "compose": project.commands.run("docker", "compose", "version", "--short"),
        }
        save("verified_absent")
        ready_by = monotonic() + config["readiness_timeout_seconds"]
        owns_project = True
        project.compose("create", "--no-build", "--pull", "never")
        result["owned_resources"] = resources = project.resources()
        result["runtime"] = runtime = inspect_runtime(project, manifest["images"], slot["variant"], resources)
        save("created_and_isolated")
        project.compose("start", timeout=max(0, ready_by - monotonic()))
        workload = Workload("http://127.0.0.1:8000", config, directory, project.commands.record,
                            transport=project.transport)
        ready = result["readiness"] = workload.ready(min(ready_by, project.commands.deadline))
        cleanup_source = "spike/healthy.py" if slot["variant"] == "healthy" else "demo/incident.py"
        matches(ready["source_hashes"], {"api": manifest["source_hashes"]["demo/app.py"],
                                        "cleanup": manifest["source_hashes"][cleanup_source]}, "running API source")
        require(ready["boot_id"] != manifest["capture"]["boot_id"], "trial boot ID equals observed boot ID")
        installed = json.loads(project.exec("api", "python", "-c", RUNTIME_CODE))
        pins = dict(line.split("==") for line in manifest["dependencies"] if ";" not in line)
        matches(installed, {"telemetry_sha256": manifest["source_hashes"]["demo/telemetry.py"], "packages": pins},
                "running API dependencies and telemetry")
        pool = json.loads(project.exec("db", "psql", "-XAt", "-U", "sentinel", "-d", "sentinel", "-c", POOL_QUERY))
        require(isinstance(pool, list) and len(pool) == config["pool_capacity"]
                and len({entry[0] for entry in pool}) == len(pool) and all(entry[1] for entry in pool),
                "database did not report a newly initialized fixed pool")
        volume = project.name + "_data"
        storage = project.inspect("volume", volume, '{"name":{{json .Name}},"created_at":{{json .CreatedAt}}}')
        require(storage["name"] == volume and storage["created_at"], "fresh volume identity unavailable")
        result["reset"] = {"boot_id": ready["boot_id"], "api_container": runtime["api"]["container"],
                           "pool_backends": pool, "fixture_instance": ready["fixture"]["instance"], "volume": storage}
        result["environment"]["installed"] = installed
        result["setup_seconds"] = monotonic() - origin
        save("ready")
        result["status"] = "invalid"
        containers = {service: value["container"] for service, value in runtime.items()}
        result["resource_samples"].append(sample_resources(project.commands, containers, "ready", origin))
        workload.run(save)
        verdict = assess(workload.data, ready, config, slot["variant"])
        result["resource_samples"].append(sample_resources(project.commands, containers, "final", origin))
        project.commands.remaining()
        reproduced = verdict["observed_final_outcome"] == verdict["expected_final_outcome"]
        final = workload.data["requests"][-1]
        result.update(status="complete", valid=True, assessment=verdict,
                      final_request_id=final["request_id"], final_event_sequence=final["server_events"][-1]["sequence"],
                      reproduction="matched" if reproduced else "not_matched")
    except KeyboardInterrupt:
        result.update(status="cancelled", reason="operator cancelled the baseline")
    except RUN_ERRORS as error:
        result["reason"] = f"{type(error).__name__}: {error}"
    finally:
        try:
            save()
        finally:
            if owns_project:
                project.commands.deadline = monotonic() + LIMITS["cleanup_seconds"]
                project.cleanup(result)
        result.update(finished_at=utc(), elapsed_seconds=monotonic() - origin)
        save()
    return result
