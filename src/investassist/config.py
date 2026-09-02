"""Chargement de la configuration (settings / scoring / univers)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .chemins import dossier_configuration, racine_installation

# Dans un executable, le code vit dans un dossier temporaire : un chemin
# deduit de l'emplacement du fichier source pointerait vers /tmp/config, qui
# n'existe pas. La resolution passe donc par le module chemins, seul a
# connaitre les trois modes d'execution.
PROJECT_ROOT = racine_installation()
CONFIG_DIR = dossier_configuration()


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass(frozen=True)
class Criterion:
    """Un critere fondamental et sa conversion en sous-score 0-100."""

    key: str
    pillar: str
    weight: float
    label: str
    unit: str
    higher_is_better: bool
    points: list[tuple[float, float]]
    enabled: bool = True
    relative_to_peers: bool = False

    def score(self, value: float | None) -> float | None:
        """Interpolation lineaire par morceaux, bornee aux extremites."""
        if value is None:
            return None
        try:
            x = float(value)
        except (TypeError, ValueError):
            return None
        if x != x or x in (float("inf"), float("-inf")):  # NaN / inf
            return None

        pts = self.points
        if x <= pts[0][0]:
            return float(pts[0][1])
        if x >= pts[-1][0]:
            return float(pts[-1][1])
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= x <= x1:
                if x1 == x0:
                    return float(y1)
                return float(y0 + (y1 - y0) * (x - x0) / (x1 - x0))
        return float(pts[-1][1])


@dataclass(frozen=True)
class ScoringConfig:
    target_years: int
    min_years: int
    min_weight_coverage: float
    min_pillar_coverage: float
    pillar_weights: dict[str, float]
    criteria: dict[str, Criterion]
    no_dividend_score: float
    score_change_threshold: float
    top_n: int
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def criteria_for(self, pillar: str) -> list[Criterion]:
        return [c for c in self.criteria.values() if c.pillar == pillar and c.enabled]

    @property
    def active_pillars(self) -> list[str]:
        return [p for p, w in self.pillar_weights.items() if w > 0 and self.criteria_for(p)]


def load_scoring(path: Path | None = None) -> ScoringConfig:
    raw = _read_yaml(path or dossier_configuration() / "scoring.yaml")
    criteria: dict[str, Criterion] = {}
    for key, spec in (raw.get("criteria") or {}).items():
        if not isinstance(spec, dict):  # p.ex. no_dividend_score
            continue
        criteria[key] = Criterion(
            key=key,
            pillar=spec["pillar"],
            weight=float(spec["weight"]),
            label=spec.get("label", key),
            unit=spec.get("unit", "ratio"),
            higher_is_better=bool(spec.get("higher_is_better", True)),
            points=[(float(a), float(b)) for a, b in spec["points"]],
            enabled=bool(spec.get("enabled", True)),
            relative_to_peers=bool(spec.get("relative_to_peers", False)),
        )

    window = raw.get("window") or {}
    dq = raw.get("data_quality") or {}
    al = raw.get("alerts") or {}
    weights = {k: float(v) for k, v in (raw.get("pillar_weights") or {}).items()}
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("config/scoring.yaml : la somme des poids des piliers est nulle.")
    if abs(total - 1.0) > 1e-6:
        # On normalise plutot que d'echouer : l'utilisateur doit pouvoir
        # ajuster un poids sans avoir a rebalancer tous les autres.
        weights = {k: v / total for k, v in weights.items()}

    return ScoringConfig(
        target_years=int(window.get("target_years", 5)),
        min_years=int(window.get("min_years", 3)),
        min_weight_coverage=float(dq.get("min_weight_coverage", 0.7)),
        min_pillar_coverage=float(dq.get("min_pillar_coverage", 0.5)),
        pillar_weights=weights,
        criteria=criteria,
        no_dividend_score=float((raw.get("criteria") or {}).get("no_dividend_score", 50)),
        score_change_threshold=float(al.get("score_change_threshold", 5.0)),
        top_n=int(al.get("top_n", 20)),
        raw=raw,
    )


@dataclass(frozen=True)
class Settings:
    sec_user_agent: str
    sec_rate_limit: float
    yahoo_force_requests_session: bool
    yahoo_rate_limit: float
    yahoo_max_workers: int
    fmp_enabled: bool
    fmp_api_key: str
    fmp_base_url: str
    fmp_daily_budget: int
    database_path: Path
    cache_dir: Path
    cache_ttl_hours: float
    alerts_local_enabled: bool
    alerts_email_enabled: bool
    email: dict[str, Any] = field(default_factory=dict)


def load_settings(path: Path | None = None) -> Settings:
    """Charge settings.yaml, avec repli sur settings.example.yaml.

    Les variables d'environnement ont priorite (utile pour un cron ou pour
    eviter d'ecrire une cle API dans un fichier).
    """
    dossier = dossier_configuration()
    path = path or dossier / "settings.yaml"
    if not path.exists():
        path = dossier / "settings.example.yaml"
    raw = _read_yaml(path)

    sec = raw.get("sec") or {}
    yahoo = raw.get("yahoo") or {}
    fmp = raw.get("fmp") or {}
    storage = raw.get("storage") or {}
    alerts = raw.get("alerts") or {}

    fmp_key = os.environ.get("FMP_API_KEY", "") or str(fmp.get("api_key") or "")
    fmp_enabled = bool(fmp.get("enabled", False)) or bool(os.environ.get("FMP_API_KEY"))

    def _abs(p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else racine_installation() / q

    # Configuration SMTP par variables d'environnement : indispensable pour une
    # execution automatisee (GitHub Actions), ou les identifiants proviennent
    # de secrets et ne doivent jamais figurer dans un fichier du depot.
    email_config = dict(alerts.get("email") or {})
    correspondances = {
        "smtp_host": "INVESTASSIST_SMTP_HOST",
        "smtp_port": "INVESTASSIST_SMTP_PORT",
        "username": "INVESTASSIST_SMTP_USER",
        "password": "INVESTASSIST_SMTP_PASSWORD",
        "sender": "INVESTASSIST_SMTP_SENDER",
    }
    for cle, variable in correspondances.items():
        valeur = os.environ.get(variable)
        if valeur:
            email_config[cle] = int(valeur) if cle == "smtp_port" else valeur
    destinataires = os.environ.get("INVESTASSIST_EMAIL_RECIPIENTS")
    if destinataires:
        email_config["recipients"] = [
            adresse.strip() for adresse in destinataires.split(",") if adresse.strip()
        ]
    email_active = bool(alerts.get("email_enabled", False))
    if os.environ.get("INVESTASSIST_EMAIL_ENABLED"):
        email_active = os.environ["INVESTASSIST_EMAIL_ENABLED"].lower() in ("1", "true", "oui", "yes")

    return Settings(
        sec_user_agent=os.environ.get("SEC_USER_AGENT")
        or str(sec.get("user_agent") or "Investassist personnel - contact non renseigne"),
        sec_rate_limit=float(sec.get("rate_limit_per_second", 8)),
        yahoo_force_requests_session=bool(yahoo.get("force_requests_session", False)),
        yahoo_rate_limit=float(yahoo.get("rate_limit_per_second", 2)),
        yahoo_max_workers=int(yahoo.get("max_workers", 4)),
        fmp_enabled=fmp_enabled and bool(fmp_key),
        fmp_api_key=fmp_key,
        fmp_base_url=str(fmp.get("base_url") or "https://financialmodelingprep.com/stable"),
        fmp_daily_budget=int(fmp.get("daily_request_budget", 200)),
        # Surcharges par variables d'environnement : pratique pour une tache
        # planifiee, un second jeu de donnees, ou des tests hors ligne.
        database_path=_abs(
            os.environ.get("INVESTASSIST_DB")
            or str(storage.get("database_path") or "data/investassist.sqlite")
        ),
        cache_dir=_abs(
            os.environ.get("INVESTASSIST_CACHE_DIR")
            or str(storage.get("cache_dir") or "data/cache")
        ),
        cache_ttl_hours=float(storage.get("cache_ttl_hours", 12)),
        alerts_local_enabled=bool(alerts.get("local_enabled", True)),
        alerts_email_enabled=email_active,
        email=email_config,
    )


def load_universes(path: Path | None = None) -> dict[str, Any]:
    return _read_yaml(path or dossier_configuration() / "universes.yaml")


@lru_cache(maxsize=1)
def cached_settings() -> Settings:
    return load_settings()


@lru_cache(maxsize=1)
def cached_scoring() -> ScoringConfig:
    return load_scoring()
