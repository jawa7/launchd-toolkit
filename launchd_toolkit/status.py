"""Cross-process network-wait gate, job lock, and status.json writer shared
by every app's launchd/cron jobs on this Mac.

Each app gets its own state file at ~/Library/Logs/<app>/status.json.
launchd_toolkit.dashboard discovers every such file automatically and merges
each entry into the row for the launchd job named by its `launchd_label`.
"""
import contextlib
import fcntl
import json
import os
import socket
import time
from datetime import datetime

LOGS_ROOT = os.path.expanduser("~/Library/Logs")

_PROBE_HOST = "api.telegram.org"
_PROBE_PORT = 443


def _state_dir(app: str) -> str:
    return os.path.join(LOGS_ROOT, app)


def _status_path(app: str) -> str:
    return os.path.join(_state_dir(app), "status.json")


def _lock_path(app: str) -> str:
    return os.path.join(_state_dir(app), ".job.lock")


def _network_up(host: str, port: int) -> bool:
    try:
        socket.create_connection((host, port), timeout=5).close()
        return True
    except OSError:
        return False


def wait_for_network(app: str, job: str, *, host: str = _PROBE_HOST,
                      port: int = _PROBE_PORT, poll_interval: int = 30) -> None:
    """Block until a TCP connection to `host` succeeds. A transient outage
    right at a job's scheduled time (e.g. wake-from-sleep) should stall the
    run, not fail it -- reports 'waiting_for_network' in status.json so a
    dashboard can show that instead of a bare failure.
    """
    if _network_up(host, port):
        return
    print(f"[{app}/{job}] network unreachable, waiting for it to come back")
    write_status(app, job, launchd_label=None, state="waiting_for_network")
    while not _network_up(host, port):
        time.sleep(poll_interval)
    print(f"[{app}/{job}] network back up")


@contextlib.contextmanager
def job_lock(app: str):
    """Serialize an app's jobs: if two come off a network wait at once, one
    runs to completion before the other starts rather than both hammering
    the network in parallel right as it recovers.
    """
    os.makedirs(_state_dir(app), exist_ok=True)
    with open(_lock_path(app), "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def write_status(app: str, job: str, *, launchd_label: str | None = None, **fields) -> None:
    """Read-modify-write status.json's entry for `job` under `app`.
    `launchd_label` is the launchd Label the dashboard should attach this
    job's detail to (e.g. "com.reddit-pipeline.daemon"); pass None only for
    an interim update (e.g. mid-wait) where the caller already set it once.
    Not lock-protected -- a lost write here just means a stale dashboard
    field for one refresh cycle, not worth guarding a status display.
    """
    os.makedirs(_state_dir(app), exist_ok=True)
    path = _status_path(app)
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    entry = data.setdefault(job, {})
    if launchd_label is not None:
        entry["launchd_label"] = launchd_label
    entry.update(fields)
    entry["updated_at"] = datetime.now().isoformat()
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def read_status(app: str) -> dict:
    try:
        with open(_status_path(app)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def read_all_statuses() -> dict:
    """app -> its status.json contents, for every app under ~/Library/Logs
    that has ever written one. Used by the dashboard to discover jobs
    without any app needing to register itself anywhere first.
    """
    result = {}
    if not os.path.isdir(LOGS_ROOT):
        return result
    for app in os.listdir(LOGS_ROOT):
        path = os.path.join(LOGS_ROOT, app, "status.json")
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    result[app] = json.load(f)
            except json.JSONDecodeError:
                continue
    return result
