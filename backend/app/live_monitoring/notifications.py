from __future__ import annotations

import logging
import os
import subprocess

# Windows PowerShell is registered as a toast sender on every Windows install, so no
# app shortcut / AppUserModelID registration is needed for the notification to show.
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

# Title and body are passed through environment variables and inserted as XML text nodes,
# so room names cannot inject markup or PowerShell code.
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $xml.GetElementsByTagName('text')
$texts.Item(0).AppendChild($xml.CreateTextNode($env:DOUYINCUT_TOAST_TITLE)) | Out-Null
$texts.Item(1).AppendChild($xml.CreateTextNode($env:DOUYINCUT_TOAST_BODY)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:DOUYINCUT_TOAST_APP).Show($toast)
"""


def show_toast(title: str, body: str) -> bool:
    """Show a Windows notification-center toast without blocking the caller."""
    if os.name != "nt":
        return False
    env = {**os.environ, "DOUYINCUT_TOAST_TITLE": title[:120], "DOUYINCUT_TOAST_BODY": body[:240],
           "DOUYINCUT_TOAST_APP": _APP_ID}
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _SCRIPT],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        logging.getLogger(__name__).warning("无法发送 Windows 开播提醒", exc_info=True)
        return False
    return True
