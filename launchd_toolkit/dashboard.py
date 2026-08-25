"""Live launchd/cron status dashboard. Run with:

    launchd-dashboard        # after `pip install -e` (see pyproject.toml)
    python3 -m launchd_toolkit.dashboard

Shows every job in ~/Library/LaunchAgents (your own agents, not the
hundreds of com.apple.* system daemons) plus `crontab -l`. Any app that
uses launchd_toolkit.status.write_status(app, job, launchd_label=...) gets
its own detail (last outcome, next run, "waiting for network") merged into
the row for that launchd_label automatically -- no per-app config needed
here, see status.read_all_statuses.
"""
import curses
import os
import plistlib
import subprocess
from datetime import datetime, timedelta

from launchd_toolkit import status

LAUNCH_AGENTS_DIR = os.path.expanduser("~/Library/LaunchAgents")


def _launchctl_list() -> dict:
    """label -> (pid_or_None, last_exit_status_or_None), from the plain
    `launchctl list` table -- far cheaper than `launchctl print` per label.
    """
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    result = {}
    for line in out.splitlines()[1:]:  # skip header row
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        pid_s, status_s, label = parts
        pid = int(pid_s) if pid_s != "-" else None
        exit_status = int(status_s) if status_s != "-" else None
        result[label] = (pid, exit_status)
    return result


def _matches_day(dt: datetime, spec: dict) -> bool:
    if "Month" in spec and dt.month != spec["Month"]:
        return False
    if "Day" in spec and dt.day != spec["Day"]:
        return False
    if "Weekday" in spec and dt.isoweekday() % 7 != spec["Weekday"] % 7:
        return False
    return True


def _next_occurrence(now: datetime, spec: dict) -> datetime:
    candidate = now.replace(
        hour=spec.get("Hour", 0), minute=spec.get("Minute", 0), second=0, microsecond=0
    )
    if candidate <= now or not _matches_day(candidate, spec):
        candidate += timedelta(days=1)
    for _ in range(366):
        if _matches_day(candidate, spec):
            return candidate
        candidate += timedelta(days=1)
    return candidate  # give up gracefully after a year of no match


def _next_run_for_plist(plist: dict, now: datetime):
    interval = plist.get("StartCalendarInterval")
    if interval is None:
        return None
    specs = interval if isinstance(interval, list) else [interval]
    return min(_next_occurrence(now, s) for s in specs)


def _load_launch_agents() -> list:
    jobs = []
    if not os.path.isdir(LAUNCH_AGENTS_DIR):
        return jobs
    for name in sorted(os.listdir(LAUNCH_AGENTS_DIR)):
        if not name.endswith(".plist"):
            continue
        path = os.path.join(LAUNCH_AGENTS_DIR, name)
        try:
            with open(path, "rb") as f:
                plist = plistlib.load(f)
        except Exception:
            continue
        label = plist.get("Label", name)
        jobs.append({"label": label, "plist": plist})
    return jobs


def _load_crontab() -> list:
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if proc.returncode != 0:
        return []
    jobs = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        jobs.append(line)
    return jobs


def _entries_for_label(all_statuses: dict, label: str) -> list:
    """Every (app, job_key, entry) across every app's status.json whose
    launchd_label matches this launchd job.
    """
    matches = []
    for app, jobs in all_statuses.items():
        for job_key, entry in jobs.items():
            if entry.get("launchd_label") == label:
                matches.append((app, job_key, entry))
    return matches


def _build_rows(now: datetime) -> list:
    """Each row: (label, state_text, last_exit_text, next_text, detail)."""
    live = _launchctl_list()
    all_statuses = status.read_all_statuses()
    rows = []

    for job in _load_launch_agents():
        label = job["label"]
        plist = job["plist"]
        pid, exit_status = live.get(label, (None, None))

        next_run = _next_run_for_plist(plist, now)
        if next_run is not None:
            state = f"running (pid {pid})" if pid else "idle"
            next_text = next_run.strftime("%Y-%m-%d %H:%M")
        elif plist.get("KeepAlive"):
            state = f"alive (pid {pid})" if pid else "DEAD (should be running)"
            next_text = "always-on"
        else:
            state = f"running (pid {pid})" if pid else "on-demand (not running)"
            next_text = "-"

        exit_text = "0" if exit_status == 0 else ("-" if exit_status is None else f"FAIL {exit_status}")

        detail_parts = []
        for app, job_key, entry in _entries_for_label(all_statuses, label):
            detail_parts.append(
                f"{app}/{job_key}: {entry.get('state', '?')} - {entry.get('last_result', '')} "
                f"(upd {entry.get('updated_at', '')[:16]})"
            )
            if entry.get("state") == "waiting_for_network":
                state = "WAITING FOR NETWORK"
            if entry.get("next_run"):
                next_text = entry["next_run"][:16].replace("T", " ")
        detail = " | ".join(detail_parts)

        rows.append((label, state, exit_text, next_text, detail))

    for line in _load_crontab():
        rows.append((f"cron: {line[:40]}", "-", "-", "n/a (cron)", ""))

    return rows


def _draw(win, now: datetime) -> None:
    win.erase()
    height, width = win.getmaxyx()
    win.addstr(0, 0, f"launchd / cron status -- {now.strftime('%Y-%m-%d %H:%M:%S')} -- q to quit, r to refresh"[: width - 1])
    header = f"{'LABEL':38} {'STATE':24} {'EXIT':8} {'NEXT RUN':17} DETAIL"
    win.addstr(1, 0, header[: width - 1], curses.A_BOLD)

    rows = _build_rows(now)
    for i, (label, state, exit_text, next_text, detail) in enumerate(rows):
        y = 2 + i
        if y >= height:
            break
        attr = curses.A_NORMAL
        if "FAIL" in exit_text or "DEAD" in state or "WAITING" in state:
            attr = curses.color_pair(1)
        line = f"{label[:38]:38} {state[:24]:24} {exit_text[:8]:8} {next_text[:17]:17} {detail}"
        win.addstr(y, 0, line[: width - 1], attr)
    win.refresh()


def main(stdscr) -> None:
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_RED, -1)
    stdscr.timeout(3000)  # redraw every 3s, also how long getch() blocks

    while True:
        _draw(stdscr, datetime.now())
        ch = stdscr.getch()
        if ch in (ord("q"), ord("Q")):
            break


def run() -> None:
    """Console-script entry point (see pyproject.toml)."""
    curses.wrapper(main)


if __name__ == "__main__":
    run()
