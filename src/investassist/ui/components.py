"""Composants d'interface reutilisables.

La banniere d'avertissement est un composant obligatoire : chaque vue
affichant un score, un classement ou une alerte doit l'appeler.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from ..disclaimers import DATA_LIMITS, MAIN_HTML, WHAT_THIS_IS, WHAT_THIS_IS_NOT
from ..models import StockScore

PILLAR_LABELS = {
    "growth": "Croissance",
    "valuation": "Valorisation",
    "profitability": "Rentabilité",
    "balance_sheet": "Qualité du bilan",
    "dividend": "Dividende",
    "qualitative": "Signal qualitatif",
}


def disclaimer_banner(*, expanded_details: bool = False) -> None:
    """Avertissement de non-conseil — a appeler sur CHAQUE vue de resultat."""
    st.warning(f"**⚠️ {MAIN_HTML}**")
    with st.expander("Ce que ce classement mesure — et ce qu'il ne mesure pas", expanded=expanded_details):
        st.markdown(f"**Ce que c'est.** {WHAT_THIS_IS}")
        st.markdown(f"**Ce que ce n'est pas.** {WHAT_THIS_IS_NOT}")
        st.markdown(f"**Limites des données.** {DATA_LIMITS}")


def format_criterion_value(value: float | None, unit: str) -> str:
    if value is None:
        return "n/d"
    if unit == "percent":
        return f"{value * 100:.1f} %"
    if unit == "pp":
        return f"{value * 100:+.1f} pts"
    return f"{value:.2f}"


def ranking_table(scores: list[StockScore]) -> pd.DataFrame:
    """Tableau de classement : score composite et sous-scores par pilier."""
    rows: list[dict[str, Any]] = []
    for index, score in enumerate(scores, start=1):
        row = {
            "Rang": index,
            "Ticker": score.ticker,
            "Nom": score.name or "—",
            "Secteur": score.sector or "—",
            "Zone": score.region or "—",
            "Score composite": round(score.composite, 1) if score.composite is not None else None,
        }
        for key, label in PILLAR_LABELS.items():
            pillar = score.pillars.get(key)
            if pillar is None or pillar.weight <= 0:
                continue
            row[label] = round(pillar.score, 1) if pillar.score is not None else None
        row["Fenêtre"] = f"{score.window_years} ans"
        row["Couverture données"] = f"{score.coverage * 100:.0f} %"
        row["Cours"] = score.price
        row["Devise"] = score.currency or ""
        rows.append(row)
    return pd.DataFrame(rows)


def criteria_detail_table(score: StockScore) -> pd.DataFrame:
    """Detail critere par critere : pourquoi ce titre est a ce rang."""
    rows = []
    for pillar_key, pillar in score.pillars.items():
        for criterion in pillar.criteria:
            rows.append(
                {
                    "Pilier": PILLAR_LABELS.get(pillar_key, pillar_key),
                    "Critère": criterion.label,
                    "Valeur": format_criterion_value(criterion.value, criterion.unit),
                    "Sous-score /100": round(criterion.score, 1) if criterion.score is not None else None,
                    "Poids dans le pilier": f"{criterion.weight * 100:.0f} %",
                    "Détail du calcul": criterion.detail or criterion.reason_missing or "—",
                }
            )
    return pd.DataFrame(rows)


def pillar_summary(score: StockScore) -> pd.DataFrame:
    rows = []
    for key, pillar in score.pillars.items():
        rows.append(
            {
                "Pilier": PILLAR_LABELS.get(key, key),
                "Sous-score /100": round(pillar.score, 1) if pillar.score is not None else None,
                "Poids": f"{pillar.weight * 100:.0f} %",
                "Couverture": f"{pillar.coverage * 100:.0f} %",
                "Neutralisé": "oui" if pillar.neutralized else "",
            }
        )
    return pd.DataFrame(rows)


def data_quality_notice(score: StockScore) -> None:
    """Badge de fenetre d'analyse et avertissements propres au titre."""
    columns = st.columns(3)
    columns[0].metric("Fenêtre d'analyse", f"{score.window_years} ans")
    columns[1].metric("Couverture des critères", f"{score.coverage * 100:.0f} %")
    columns[2].metric(
        "Score composite",
        f"{score.composite:.1f}" if score.composite is not None else "non calculable",
    )
    if score.window_years < 5:
        st.info(
            f"Fenêtre de {score.window_years} ans : l'historique fondamental gratuit est "
            "plus court pour ce titre (fréquent hors États-Unis). Comparer un TCAM sur "
            f"{score.window_years} ans à un TCAM sur 5 ans n'est pas strictement homogène."
        )
    if not score.ranked:
        st.error(f"**Exclu du classement.** {score.exclusion_reason}")
    for warning in score.warnings:
        st.caption(f"ℹ️ {warning}")


def excluded_table(scores: list[StockScore]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Ticker": s.ticker,
                "Nom": s.name or "—",
                "Zone": s.region or "—",
                "Fenêtre": f"{s.window_years} ans",
                "Couverture": f"{s.coverage * 100:.0f} %",
                "Raison de l'exclusion": s.exclusion_reason,
            }
            for s in scores
        ]
    )
