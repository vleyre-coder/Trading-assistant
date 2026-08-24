"""Financial Modeling Prep — source d'appoint, optionnelle.

Limites du plan gratuit verifiees le 23 aout 2026 :
  - 250 requetes par jour, bande passante 500 Mo sur 30 jours glissants ;
  - actions des places AMERICAINES uniquement (les places europeennes
    exigent un plan payant) ;
  - 5 ans d'historique de cours et seulement 5 TRIMESTRES d'etats financiers.

Consequence directe : FMP ne peut PAS servir de source primaire pour un
score sur 5 ans. SEC EDGAR fait mieux, gratuitement, sans cle et sans quota.
Ce module sert donc au CONTROLE CROISE des ratios que nous calculons
nous-memes (detection d'une anomalie de parsing), jamais de source de verite.

Points d'entree : base "stable" (les anciens /api/v3/ sont en fin de vie).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from ..config import Settings
from .base import DiskCache, RateLimiter, get_json, make_session

log = logging.getLogger(__name__)

FREE_PLAN_NOTES = (
    "Plan gratuit FMP : 250 requetes/jour, actions US uniquement, "
    "5 trimestres d'etats financiers — insuffisant pour un historique 5 ans."
)


class FmpClient:
    """Client minimal, avec compteur de requetes journalier persistant."""

    def __init__(self, settings: Settings, cache: DiskCache | None = None) -> None:
        self.settings = settings
        self.enabled = settings.fmp_enabled and bool(settings.fmp_api_key)
        self.session = make_session("Investassist personnel")
        self.limiter = RateLimiter(4)
        self.cache = cache or DiskCache(settings.cache_dir, settings.cache_ttl_hours)

    # ------------------------------------------------------- quota du jour
    def _quota_key(self) -> str:
        return f"fmp_calls_{date.today().isoformat()}"

    def calls_today(self) -> int:
        return int(self.cache.get("fmp_quota", self._quota_key()) or 0)

    def _increment(self) -> None:
        self.cache.set("fmp_quota", self._quota_key(), self.calls_today() + 1)

    def budget_left(self) -> int:
        return max(0, self.settings.fmp_daily_budget - self.calls_today())

    # ---------------------------------------------------------------- appel
    def _get(self, path: str, **params: Any) -> Any | None:
        if not self.enabled:
            return None
        if self.budget_left() <= 0:
            log.warning(
                "Budget FMP du jour epuise (%s requetes) — appel %s ignore.",
                self.settings.fmp_daily_budget,
                path,
            )
            return None
        url = f"{self.settings.fmp_base_url.rstrip('/')}/{path.lstrip('/')}"
        payload = get_json(
            self.session,
            url,
            limiter=self.limiter,
            params={**params, "apikey": self.settings.fmp_api_key},
        )
        self._increment()
        if isinstance(payload, dict) and payload.get("Error Message"):
            log.warning("FMP a repondu par une erreur : %s", payload["Error Message"])
            return None
        return payload

    # --------------------------------------------------------------- public
    def key_metrics(self, ticker: str) -> dict[str, Any] | None:
        """Ratios pre-calcules du dernier exercice (controle croise)."""
        cached = self.cache.get("fmp_metrics", ticker)
        if cached is not None:
            return cached
        payload = self._get("key-metrics", symbol=ticker, limit=1)
        if isinstance(payload, list) and payload:
            self.cache.set("fmp_metrics", ticker, payload[0])
            return payload[0]
        return None

    def ratios(self, ticker: str) -> dict[str, Any] | None:
        cached = self.cache.get("fmp_ratios", ticker)
        if cached is not None:
            return cached
        payload = self._get("ratios", symbol=ticker, limit=1)
        if isinstance(payload, list) and payload:
            self.cache.set("fmp_ratios", ticker, payload[0])
            return payload[0]
        return None

    def cross_check(self, ticker: str, computed: dict[str, float | None]) -> list[str]:
        """Compare nos calculs aux ratios FMP et signale les ecarts notables.

        Renvoie une liste de messages destines a l'utilisateur. Un ecart
        n'invalide pas notre calcul : les definitions diffferent souvent
        (EBITDA, dette nette). Il signale un point a verifier.
        """
        if not self.enabled:
            return []
        metrics = self.key_metrics(ticker) or {}
        ratios = self.ratios(ticker) or {}
        if not metrics and not ratios:
            return []

        comparisons = [
            ("roe_avg", ratios.get("returnOnEquity") or metrics.get("roe"), "ROE"),
            ("current_ratio", ratios.get("currentRatio") or metrics.get("currentRatio"), "ratio de liquidite"),
            ("net_debt_to_ebitda", metrics.get("netDebtToEBITDA"), "dette nette / EBITDA"),
            ("price_to_book", ratios.get("priceToBookRatio") or metrics.get("pbRatio"), "P/B"),
        ]
        messages: list[str] = []
        for key, reference, label in comparisons:
            ours = computed.get(key)
            if ours is None or reference is None:
                continue
            try:
                reference = float(reference)
            except (TypeError, ValueError):
                continue
            scale = max(abs(ours), abs(reference), 1e-9)
            if abs(ours - reference) / scale > 0.25:
                messages.append(
                    f"Ecart de plus de 25 % sur le {label} : calcul interne {ours:.2f} "
                    f"vs FMP {reference:.2f} — definitions possiblement differentes."
                )
        return messages
