"""Envoi des alertes : notification locale et/ou email SMTP.

Aucun service tiers payant. L'email utilise le serveur SMTP que vous
configurez (par exemple celui de votre fournisseur de messagerie, avec un
mot de passe d'application).
"""
from __future__ import annotations

import logging
import platform
import shutil
import smtplib
import subprocess
from email.message import EmailMessage
from pathlib import Path

from ..config import Settings
from ..disclaimers import ALERT_FOOTER, MAIN
from .rules import AlertEvent

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.log_path = settings.database_path.parent / "notifications.log"

    # ------------------------------------------------------------- local
    def _write_log(self, events: list[AlertEvent]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            for e in events:
                fh.write(f"{e.triggered_at:%Y-%m-%d %H:%M:%S}\t{e.kind}\t{e.message}\n")

    @staticmethod
    def _desktop_notification(title: str, body: str) -> bool:
        """Notification de bureau, selon les outils presents sur le systeme."""
        system = platform.system()
        try:
            if system == "Linux" and shutil.which("notify-send"):
                subprocess.run(["notify-send", title, body], check=False, timeout=10)
                return True
            if system == "Darwin" and shutil.which("osascript"):
                script = f'display notification {body!r} with title {title!r}'
                subprocess.run(["osascript", "-e", script], check=False, timeout=10)
                return True
            if system == "Windows":
                try:
                    from winotify import Notification  # type: ignore

                    Notification(app_id="Investassist", title=title, msg=body).show()
                    return True
                except ImportError:
                    # Repli PowerShell, sans dependance supplementaire.
                    ps = (
                        "[reflection.assembly]::LoadWithPartialName('System.Windows.Forms')"
                        ">$null; $n=New-Object System.Windows.Forms.NotifyIcon;"
                        "$n.Icon=[System.Drawing.SystemIcons]::Information;"
                        "$n.Visible=$true;"
                        f"$n.ShowBalloonTip(10000,'{title}','{body[:200]}',"
                        "[System.Windows.Forms.ToolTipIcon]::Info)"
                    )
                    subprocess.run(
                        ["powershell", "-NoProfile", "-Command", ps], check=False, timeout=15
                    )
                    return True
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("Notification de bureau indisponible : %s", exc)
        return False

    # -------------------------------------------------------------- email
    def _send_email(self, events: list[AlertEvent]) -> bool:
        conf = self.settings.email
        recipients = [r for r in (conf.get("recipients") or []) if r]
        host = conf.get("smtp_host")
        if not host or not recipients:
            log.warning("Envoi email demande mais SMTP ou destinataires non configures.")
            return False

        body_lines = [
            f"{len(events)} alerte(s) déclenchée(s) sur vos seuils personnels :",
            "",
        ]
        for e in events:
            body_lines.append(f"• [{e.triggered_at:%d/%m/%Y %H:%M}] {e.message}")
        body_lines += ["", ALERT_FOOTER]

        message = EmailMessage()
        message["Subject"] = f"[Investassist] {len(events)} alerte(s) sur vos seuils"
        message["From"] = conf.get("sender") or conf.get("username") or "investassist@localhost"
        message["To"] = ", ".join(recipients)
        message.set_content("\n".join(body_lines))

        try:
            port = int(conf.get("smtp_port", 587))
            if port == 465:
                server = smtplib.SMTP_SSL(host, port, timeout=30)
            else:
                server = smtplib.SMTP(host, port, timeout=30)
            with server:
                if port != 465 and conf.get("use_starttls", True):
                    server.starttls()
                if conf.get("username"):
                    server.login(conf["username"], conf.get("password") or "")
                server.send_message(message)
            return True
        except (smtplib.SMTPException, OSError) as exc:
            log.error("Envoi email echoue : %s: %s", type(exc).__name__, exc)
            return False

    # ------------------------------------------------------------- public
    def dispatch(self, events: list[AlertEvent]) -> dict[str, bool]:
        """Envoie les alertes sur les canaux actives. Renvoie leur statut."""
        if not events:
            return {}
        status: dict[str, bool] = {}
        if self.settings.alerts_local_enabled:
            self._write_log(events)
            title = f"Investassist — {len(events)} alerte(s)"
            body = events[0].message if len(events) == 1 else (
                f"{len(events)} seuils franchis. Detail : {self.log_path}"
            )
            self._desktop_notification(title, f"{body}\n\n{MAIN}")
            status["local"] = True
        if self.settings.alerts_email_enabled:
            status["email"] = self._send_email(events)
        return status
