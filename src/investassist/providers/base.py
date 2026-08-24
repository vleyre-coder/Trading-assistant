"""Socle commun aux fournisseurs : limitation de debit, cache disque, HTTP."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

import requests

log = logging.getLogger(__name__)

# Yahoo refuse les requetes sans User-Agent de navigateur (HTTP 429).
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


class RateLimiter:
    """Limiteur simple, partage entre threads : N requetes par seconde max."""

    def __init__(self, per_second: float) -> None:
        self.min_interval = 1.0 / per_second if per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            sleep_for = self._next_allowed - now
            if sleep_for > 0:
                time.sleep(sleep_for)
                now = time.monotonic()
            self._next_allowed = now + self.min_interval


class DiskCache:
    """Cache JSON sur disque, avec duree de vie.

    Indispensable : les quotas gratuits sont la contrainte principale du
    projet, et un ecran Streamlit peut etre rafraichi plusieurs fois de
    suite. Le cache est explicitement contournable ("relancer l'analyse").
    """

    def __init__(self, directory: Path, ttl_hours: float) -> None:
        self.dir = Path(directory)
        self.ttl = ttl_hours * 3600
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, namespace: str, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        sub = self.dir / namespace
        sub.mkdir(parents=True, exist_ok=True)
        return sub / f"{digest}.json"

    def get(self, namespace: str, key: str) -> Any | None:
        if self.ttl <= 0:
            return None
        p = self._path(namespace, key)
        if not p.exists():
            return None
        if time.time() - p.stat().st_mtime > self.ttl:
            return None
        try:
            with p.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None

    def set(self, namespace: str, key: str, value: Any) -> None:
        if self.ttl <= 0:
            return
        p = self._path(namespace, key)
        try:
            tmp = p.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(value, fh, default=str)
            tmp.replace(p)
        except (OSError, TypeError) as exc:
            log.debug("Ecriture cache impossible pour %s/%s : %s", namespace, key, exc)

    def clear(self, namespace: str | None = None) -> int:
        target = self.dir / namespace if namespace else self.dir
        removed = 0
        if not target.exists():
            return 0
        for p in target.rglob("*.json"):
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
        return removed


def make_session(user_agent: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
    return s


def get_json(
    session: requests.Session,
    url: str,
    *,
    limiter: RateLimiter | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 30.0,
    retries: int = 3,
    backoff: float = 2.0,
) -> Any | None:
    """GET JSON avec reprise exponentielle. Renvoie None en cas d'echec.

    Un echec de source ne doit jamais interrompre un screening complet : on
    journalise et le titre sera signale comme incomplet.
    """
    last_error: str = ""
    for attempt in range(retries):
        if limiter:
            limiter.wait()
        try:
            resp = session.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    last_error = "reponse non-JSON"
            elif resp.status_code in (429, 503, 502, 504):
                last_error = f"HTTP {resp.status_code} (debit limite)"
            else:
                log.warning("GET %s -> HTTP %s", url, resp.status_code)
                return None
        if attempt < retries - 1:
            time.sleep(backoff * (2**attempt))
    log.warning("GET %s abandonne apres %s tentatives : %s", url, retries, last_error)
    return None


def safe_call(fn: Callable[[], Any], default: Any = None, label: str = "") -> Any:
    """Execute fn en absorbant toute exception (sources tierces instables)."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - la robustesse primalise ici
        log.debug("Appel %s echoue : %s: %s", label or fn, type(exc).__name__, exc)
        return default
