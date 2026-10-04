"""Windows notifications (toasts), with no extra package.

Windows PowerShell 5.1 (powershell.exe, signed by Microsoft) can reach the WinRT
notification API. OwnLife passes it a short, fixed script on its standard input;
the toast's text travels in an environment variable, as XML, so nothing in it is
ever interpreted as a command. Windows shows the toast in the corner of the
screen and keeps it in the notification centre; clicking it opens OwnLife in
the browser at the right page.

The toasts appear under the name "OwnLife": the app registers that name once,
for the current user only (HKCU/Software/Classes/AppUserModelId/OwnLife — no
administrator rights; deleting the key undoes it).
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from app.config import PROJECT_DIR

log = logging.getLogger("ownlife.notify")

APP_ID = "OwnLife"
# Windows PowerShell's own id: always registered, used if ours cannot be.
FALLBACK_APP_ID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}/WindowsPowerShell/v1.0/powershell.exe".replace("/", os.sep)
ICON = PROJECT_DIR / "frontend" / "public" / "icon-192.png"

# Read from the environment, never from the command line.
SCRIPT = "\n".join([
    "$ErrorActionPreference = 'Stop'",
    "$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]",
    "$null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]",
    "$doc = New-Object Windows.Data.Xml.Dom.XmlDocument",
    "$doc.LoadXml($env:OWNLIFE_TOAST_XML)",
    "$toast = New-Object Windows.UI.Notifications.ToastNotification $doc",
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:OWNLIFE_TOAST_APP).Show($toast)",
    "",
])

_registered: bool | None = None


def available() -> bool:
    return os.name == "nt"


def _register_app_id() -> bool:
    global _registered
    if _registered is not None:
        return _registered
    try:
        import winreg

        path = "\\".join(("Software", "Classes", "AppUserModelId", APP_ID))
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "OwnLife")
            if ICON.exists():
                winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(ICON))
        _registered = True
    except OSError as e:
        log.warning("Could not register the OwnLife notification name: %s", e)
        _registered = False
    return _registered


def toast_xml(title: str, body: str, url: str | None = None, button: str | None = None) -> str:
    launch = f" activationType=\"protocol\" launch={quoteattr(url)}" if url else ""
    actions = ""
    if url and button:
        actions = (f"<actions><action content={quoteattr(button)} activationType=\"protocol\" "
                   f"arguments={quoteattr(url)}/></actions>")
    return (
        f"<toast{launch}><visual><binding template=\"ToastGeneric\">"
        f"<text>{escape(title)}</text><text>{escape(body)}</text>"
        f"</binding></visual>{actions}</toast>"
    )


def _powershell() -> str:
    exe = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(exe) if exe.exists() else "powershell.exe"


def send(title: str, body: str, url: str | None = None, button: str | None = None) -> tuple[bool, str]:
    """Shows a toast. Returns (shown, detail)."""
    if not available():
        return False, "Notifications are only implemented for Windows"
    xml = toast_xml(title, body, url, button)
    ids = [APP_ID, FALLBACK_APP_ID] if _register_app_id() else [FALLBACK_APP_ID]
    last = ""
    for app_id in ids:
        try:
            r = subprocess.run(
                [_powershell(), "-NoProfile", "-NonInteractive", "-Command", "-"],
                input=SCRIPT, capture_output=True, text=True, timeout=30,
                env={**os.environ, "OWNLIFE_TOAST_XML": xml, "OWNLIFE_TOAST_APP": app_id},
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            last = f"{e.__class__.__name__}: {e}"
            continue
        if r.returncode == 0 and not r.stderr.strip():
            return True, "shown as OwnLife" if app_id == APP_ID else "shown as Windows PowerShell"
        last = (r.stderr or r.stdout or f"exit {r.returncode}").strip()[:300]
    log.warning("Toast not shown: %s", last)
    return False, last
