"""A5/B4 desktop toasts: best-effort OS notifications (P4.4).

Linux uses notify-send, macOS tries terminal-notifier (brew) then
osascript, Windows uses a dependency-free WinRT toast via powershell.
Every backend is guarded: notify() never raises, it reports delivery.
"""

import shutil
import subprocess


def _run(argv: list[str], timeout: int = 5) -> bool:
    try:
        completed = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _notify_linux(title: str, message: str) -> bool:
    if shutil.which("notify-send") is None:
        return False
    return _run(["notify-send", "-t", "5000", title, message])


def _notify_macos(title: str, message: str) -> bool:
    if shutil.which("terminal-notifier") is not None:
        if _run(["terminal-notifier", "-title", title, "-message", message]):
            return True
    if shutil.which("osascript") is None:
        return False
    safe_title = title.replace('"', "")
    safe_message = message.replace('"', "")
    script = f'display notification "{safe_message}" with title "{safe_title}"'
    return _run(["osascript", "-e", script])


def _notify_windows(title: str, message: str) -> bool:
    if shutil.which("powershell") is None:
        return False
    safe_title = title.replace("'", "")
    safe_message = message.replace("'", "")
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, "
        "Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
        "$xml = \"<toast><visual><binding template='ToastText02'>"
        f"<text id='1'>{safe_title}</text><text id='2'>{safe_message}</text>"
        "</binding></visual></toast>\"; "
        "$doc = New-Object Windows.Data.Xml.Dom.XmlDocument; "
        "$doc.LoadXml($xml); "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'Auto System Agent').Show($doc)"
    )
    return _run(["powershell", "-NoProfile", "-Command", script])


def notify(title: str, message: str, *, os_name: str | None = None) -> bool:
    """Show one desktop toast; True when a backend accepted it."""
    if os_name is None:
        try:
            from auto_system_agent.os_utils import detect_os

            os_name = detect_os()
        except Exception:
            os_name = "linux"
    heading = (title or "Auto System Agent").strip() or "Auto System Agent"
    body = (message or "").strip()
    if not body:
        return False
    try:
        if os_name == "macos":
            return _notify_macos(heading, body)
        if os_name == "windows":
            return _notify_windows(heading, body)
        return _notify_linux(heading, body)
    except Exception:
        return False
