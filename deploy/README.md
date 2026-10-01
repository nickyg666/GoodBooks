# Deploy

Everything needed to install GoodBooks on a fresh host.

    sudo ./deploy/install.sh     # deps, unit, runtime dirs
    ./deploy/verify.sh           # prove it works

## What gets installed

| thing | where |
|---|---|
| Python deps | `.venv/` (created, or system pip with `--break-system-packages`) |
| systemd unit | `/etc/systemd/system/GoodBooks.service` |
| runtime dirs | `data/`, `data/covers`, `data/temp`, `data/uploads`, `logs/` |

## What is never touched

`install.sh` does not read, move or write your books or settings. The
library location comes from `library_root` in `data/settings.json`, and the
data directory is only created if absent. Re-running the installer is safe.

## Layout note

The project is a **flat module tree**, not a `src/` layout. `pyproject.toml`
lists every module explicitly in `[tool.setuptools] py-modules`, because
`packages.find` cannot discover a flat tree. An earlier version pointed at
`where = ["src"]` and declared a console script; `src/` was archived, so
`pip install .` had been broken.

There is deliberately no console script: the app is a service, not a CLI.

## Service

`GoodBooks.service` runs `xvfb-run -a python3 app.py`. The `-a` matters: it
picks a free display instead of demanding `:99`, which is what caused a
startup failure when a stale Xvfb held the lock.

`KillSignal=SIGINT` lets the app shut its background executor down cleanly
rather than leaving a half-written state file.

## Verify a fresh host

    git clone <repo> && cd GoodBooks
    sudo ./deploy/install.sh
    ./deploy/verify.sh
    systemctl enable --now GoodBooks.service
