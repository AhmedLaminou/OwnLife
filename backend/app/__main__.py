"""`python -m app` — starts OwnLife on http://127.0.0.1:8000 (see HOST/PORT in .env).

    python -m app                 the server, in this console
    pythonw -m app --background   no window at all: production mode, the output
                                  goes to data/logs/ownlife.log, and nothing happens
                                  if OwnLife is already running (used at login by
                                  scripts/autostart.ps1, so the reminders can fire)
"""

import os
import socket
import sys

import uvicorn

from app.config import get_settings


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1" if host in ("0.0.0.0", "") else host, port)) == 0


def main() -> None:
    background = "--background" in sys.argv[1:]
    if background:
        os.environ["ENVIRONMENT"] = "production"
        get_settings.cache_clear()
    s = get_settings()
    if background:
        if _port_in_use(s.host, s.port):
            return  # already running (started by hand, or a second login)
        logs = s.data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        # pythonw has no console: give uvicorn's log handlers somewhere to write.
        log = open(logs / "ownlife.log", "a", buffering=1, encoding="utf-8")  # noqa: SIM115
        sys.stdout = sys.stderr = log
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=s.host,
        port=s.port,
        reload=s.environment == "development",
        log_level="info",
    )


if __name__ == "__main__":
    main()
