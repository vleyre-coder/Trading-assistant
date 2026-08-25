"""Evaluation des regles d'alerte.

Une alerte constate le franchissement d'un seuil DEFINI PAR L'UTILISATEUR.
Elle n'emet aucune recommandation : la formulation reste factuelle
("seuil franchi", "rang modifie"), jamais prescriptive.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from ..config import ScoringConfig
from ..models import StockScore
from ..storage import Database

log = logging.getLogger(__name__)


@dataclass
class AlertEvent:
    ticker: str
    kind: str
    message: str
    rule_id: int | None = None
    triggered_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.triggered_at is None:
            self.triggered_at = datetime.now()


def _fmt(value: float | None, digits: int = 2) -> str:
    return "n/d" if value is None else f"{value:,.{digits}f}".replace(",", " ")


def evaluate_rules(
    db: Database,
    scores: list[StockScore],
    cfg: ScoringConfig,
    *,
    previous: dict[str, dict] | None = None,
    ranks: dict[str, int] | None = None,
    previous_ranks: dict[str, int] | None = None,
) -> list[AlertEvent]:
    """Confronte les regles actives aux resultats de l'analyse courante."""
    by_ticker = {s.ticker.upper(): s for s in scores}
    previous = previous or {}
    ranks = ranks or {}
    previous_ranks = previous_ranks or {}
    events: list[AlertEvent] = []

    for rule in db.alert_rules(enabled_only=True):
        ticker = rule["ticker"].upper()
        kind = rule["kind"]
        params = rule["params"] or {}
        score = by_ticker.get(ticker)
        if score is None:
            continue

        if kind in ("price_above", "price_below"):
            threshold = params.get("threshold")
            if threshold is None or score.price is None:
                continue
            crossed = (
                score.price >= float(threshold)
                if kind == "price_above"
                else score.price <= float(threshold)
            )
            # Une alerte signale un FRANCHISSEMENT, pas un etat. Tant que le
            # cours reste du meme cote du seuil, la regle ne se redeclenche
            # pas : sinon la meme alerte reviendrait a chaque execution et
            # deviendrait du bruit qu'on finit par ignorer.
            etat = "crossed" if crossed else "clear"
            deja_signale = rule.get("last_state") == "crossed"
            db.set_rule_state(rule["id"], etat)
            if crossed and not deja_signale:
                sense = "au-dessus de" if kind == "price_above" else "au-dessous de"
                events.append(
                    AlertEvent(
                        ticker,
                        kind,
                        f"{ticker} : cours {_fmt(score.price)} {score.currency or ''} — "
                        f"seuil {sense} {_fmt(float(threshold))} franchi.",
                        rule["id"],
                    )
                )

        elif kind == "score_change":
            threshold = float(params.get("threshold", cfg.score_change_threshold))
            before = (previous.get(ticker) or {}).get("composite")
            if before is None or score.composite is None:
                continue
            delta = score.composite - float(before)
            if abs(delta) >= threshold:
                events.append(
                    AlertEvent(
                        ticker,
                        kind,
                        f"{ticker} : score composite {_fmt(float(before), 1)} → "
                        f"{_fmt(score.composite, 1)} ({delta:+.1f} pts, seuil "
                        f"{threshold:.1f}). Variation des critères fondamentaux, "
                        "sans portée prédictive.",
                        rule["id"],
                    )
                )

        elif kind == "earnings_published":
            last = score.__dict__.get("_last_earnings")
            if last is None:
                continue
            seen = db.last_earnings_seen(ticker)
            if str(last) != (seen or ""):
                db.set_last_earnings_seen(ticker, str(last))
                if seen:  # la premiere observation initialise sans alerter
                    events.append(
                        AlertEvent(
                            ticker,
                            kind,
                            f"{ticker} : nouvelle publication de résultats détectée "
                            f"(période de référence {last}, précédente {seen}).",
                            rule["id"],
                        )
                    )

        elif kind in ("top_n_entry", "top_n_exit"):
            top_n = int(params.get("n", cfg.top_n))
            now_rank = ranks.get(ticker)
            was_rank = previous_ranks.get(ticker)
            if now_rank is None and was_rank is None:
                continue
            entered = (
                now_rank is not None
                and now_rank <= top_n
                and (was_rank is None or was_rank > top_n)
            )
            exited = (
                was_rank is not None
                and was_rank <= top_n
                and (now_rank is None or now_rank > top_n)
            )
            if kind == "top_n_entry" and entered:
                events.append(
                    AlertEvent(
                        ticker,
                        kind,
                        f"{ticker} entre dans le top {top_n} du classement "
                        f"(rang {now_rank}, précédemment "
                        f"{was_rank if was_rank else 'hors classement'}). "
                        "Classement d'adéquation aux critères, non predictif.",
                        rule["id"],
                    )
                )
            if kind == "top_n_exit" and exited:
                events.append(
                    AlertEvent(
                        ticker,
                        kind,
                        f"{ticker} sort du top {top_n} du classement "
                        f"(rang {now_rank if now_rank else 'hors classement'}, "
                        f"précédemment {was_rank}).",
                        rule["id"],
                    )
                )

    for event in events:
        if event.rule_id:
            db.mark_rule_fired(event.rule_id)
        db.record_event(event.ticker, event.kind, event.message, event.rule_id)

    return events


def attach_earnings_dates(scores: list[StockScore], last_dates: dict[str, str]) -> None:
    """Injecte la derniere date de publication connue dans les scores.

    Passe par un attribut prive car cette information est de nature marche,
    pas de nature score : elle ne doit pas polluer le modele de notation.
    """
    for score in scores:
        value = last_dates.get(score.ticker.upper())
        if value:
            score.__dict__["_last_earnings"] = value
