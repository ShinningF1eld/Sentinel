import json
import logging
import os
from datetime import datetime, timezone
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import import_module
from pathlib import Path
from threading import Lock
from time import perf_counter
from uuid import uuid4

from psycopg_pool import ConnectionPool, PoolTimeout


class DesignatedException(Exception):
    pass


class Demo:
    def __init__(self, pool, cleanup):
        self.pool = pool
        self.on_exception = cleanup.on_designated_exception
        self.source_hashes = {
            "api": sha256(Path(__file__).read_bytes()).hexdigest(),
            "cleanup": sha256(Path(cleanup.__file__).read_bytes()).hexdigest(),
        }
        self.boot_id = str(uuid4())
        self.lock = Lock()
        self.active = 0
        self.checked_out = set()
        self.retained = []
        self.events = []
        self.sequence = 0
        with pool.connection() as connection:
            rows = connection.execute("SELECT id, value, version, instance FROM fixture").fetchall()
        if len(rows) != 1 or rows[0][:3] != (1, "ready", 1) or not rows[0][3]:
            raise RuntimeError("fixture key, value, version, or identity failed verification")
        self.fixture = dict(zip(("key", "value", "version", "instance"), rows[0]))
        if pool.get_stats()["pool_available"] != pool.max_size:
            raise RuntimeError("pool is not at full capacity after fixture verification")

    def stamp(self):
        self.sequence += 1
        return {"sequence": self.sequence, "timestamp": datetime.now(timezone.utc).isoformat()}

    def event(self, kind, request_id, **fields):
        self.events.append({**self.stamp(), "kind": kind, "request_id": request_id, **fields})

    def readiness(self):
        return {
            "boot_id": self.boot_id,
            "fixture": self.fixture,
            "pool_capacity": self.pool.max_size,
            "pool_min_size": self.pool.min_size,
            "acquisition_timeout_ms": self.pool.timeout * 1000,
            "source_hashes": self.source_hashes,
        }

    def snapshot(self):
        with self.lock:
            return {
                **self.stamp(),
                "boot_id": self.boot_id,
                "active_requests": self.active,
                "checked_out": len(self.checked_out),
                "events": list(self.events),
            }

    def release(self, connection, request_id):
        pid = connection.info.backend_pid
        with self.lock:
            self.pool.putconn(connection)
            self.checked_out.remove(pid)
            self.event("return", request_id, connection_id=pid)

    def execute(self, request_id, operation):
        with self.lock:
            self.active += 1
            self.event("start", request_id, operation=operation)
        status, outcome, value = 500, "internal_error", None
        measurement = {
            "acquisition_outcome": "error",
            "acquisition_wait_ms": None,
            "pool_error_type": None,
            "query_started": False,
            "query_duration_ms": None,
            "query_duration_reason": "acquisition_incomplete",
        }
        try:
            start = perf_counter()
            try:
                connection = self.pool.getconn()
            except PoolTimeout as error:
                status, outcome = 503, "acquisition_timeout"
                measurement.update(
                    acquisition_outcome="timeout",
                    pool_error_type=type(error).__name__,
                    query_duration_reason="acquisition_timeout",
                )
                connection = None
            finally:
                measurement["acquisition_wait_ms"] = (perf_counter() - start) * 1000

            if connection is not None:
                pid = connection.info.backend_pid
                with self.lock:
                    self.checked_out.add(pid)
                    self.event("checkout", request_id, connection_id=pid)
                measurement.update(
                    acquisition_outcome="acquired", query_started=True, query_duration_reason=None
                )
                try:
                    query_start = perf_counter()
                    try:
                        row = connection.execute(
                            "SELECT value FROM fixture WHERE id = %s", (1,)
                        ).fetchone()
                    finally:
                        measurement["query_duration_ms"] = (perf_counter() - query_start) * 1000
                    if row is None or row[0] != "ready":
                        raise RuntimeError("ordinary read returned an unexpected fixture value")
                    value = row[0]
                    if operation == "trigger":
                        raise DesignatedException()
                except DesignatedException:
                    self.on_exception(
                        connection, lambda conn: self.release(conn, request_id), self.retained
                    )
                    status, outcome = 500, "designated_exception"
                except BaseException:
                    self.release(connection, request_id)
                    raise
                else:
                    self.release(connection, request_id)
                    status, outcome = 200, "success"
        except Exception:
            logging.exception("Unexpected error in workload request %s", request_id)
        finally:
            with self.lock:
                self.active -= 1
                self.event("terminal", request_id, operation=operation, outcome=outcome,
                           http_status=status, value=value, **measurement)
        body = {"request_id": request_id, "outcome": outcome}
        if outcome == "success":
            body["value"] = value
        return status, body


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, data):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def dispatch(self):
            if self.command == "GET" and self.path == "/ready":
                self.respond(200, app.readiness())
                return
            if self.command == "GET" and self.path == "/diagnostics":
                self.respond(200, app.snapshot())
                return
            operation = {("GET", "/ordinary"): "ordinary", ("POST", "/trigger"): "trigger"}.get(
                (self.command, self.path)
            )
            if operation is None:
                self.respond(404, {"outcome": "unknown_route"})
                return
            request_id = self.headers.get("X-Request-ID") or str(uuid4())
            self.respond(*app.execute(request_id, operation))

        do_GET = dispatch
        do_POST = dispatch

    return Handler


def main():
    size = int(os.environ.get("POOL_CAPACITY", "2"))
    timeout = int(os.environ.get("ACQUISITION_TIMEOUT_MS", "500")) / 1000
    cleanup = import_module(os.environ.get("CLEANUP_MODULE", "demo.incident"))
    with ConnectionPool(
        min_size=size, max_size=size, timeout=timeout, kwargs={"autocommit": True}, open=False
    ) as pool:
        pool.wait(timeout=15)
        app = Demo(pool, cleanup)
        with ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8000"))), handler_for(app)) as server:
            server.serve_forever()


if __name__ == "__main__":
    main()
