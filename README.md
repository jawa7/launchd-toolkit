# launchd-toolkit

Shared helpers for personal macOS launchd/cron jobs:

- **`launchd_toolkit.status`** -- `wait_for_network(app, job)` blocks until
  network's back instead of failing a run outright; `job_lock(app)`
  serializes an app's jobs so two that come off a network wait at once
  don't hit the network concurrently; `write_status`/`read_status` persist
  each job's last outcome to `~/Library/Logs/<app>/status.json`.
- **`launchd_toolkit.dashboard`** -- a curses TUI (`launchd-dashboard`)
  listing every job in `~/Library/LaunchAgents` plus `crontab -l`, with
  alive/dead, last exit code, computed next run, and any app's own status
  detail merged in automatically via the `launchd_label` tag on its status
  entries.

## Install

```sh
pip install -e /path/to/launchd-toolkit
```

## Usage

```python
from launchd_toolkit import status

status.wait_for_network("myapp", "myjob")
with status.job_lock("myapp"):
    ...  # do the network-dependent work
    status.write_status(
        "myapp", "myjob",
        launchd_label="com.example.myapp",
        state="success", last_result="...",
    )
```

```sh
launchd-dashboard
```
