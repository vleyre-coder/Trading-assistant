"""Calcul des criteres fondamentaux a partir de donnees normalisees.

Chaque fonction renvoie (valeur, detail, raison_absence) :
  - valeur : nombre, ou None si non calculable ;
  - detail : explication lisible destinee a l'interface, pour que le
    classement ne soit jamais un chiffre opaque ;
  - raison_absence : pourquoi le critere est N/A.

Principe directeur : un critere non calculable est marque N/A, JAMAIS
remplace par une valeur par defaut favorable. Un PEG incalculable faute de
croissance positive ne doit pas ressembler a un PEG excellent.
"""
from __future__ import annotations

from datetime import date
from typing import Sequence

import pandas as pd

from .models import Fundamentals

Result = tuple[float | None, str, str]


def _fmt_money(value: float | None, currency: str | None = None) -> str:
    if value is None:
        return "n/d"
    unit = f" {currency}" if currency else ""
    for divisor, suffix in ((1e9, " Md"), (1e6, " M"), (1e3, " k")):
        if abs(value) >= divisor:
            return f"{value / divisor:,.2f}{suffix}{unit}".replace(",", " ")
    return f"{value:,.0f}{unit}".replace(",", " ")


def _pct(value: float | None, digits: int = 1) -> str:
    return "n/d" if value is None else f"{value * 100:.{digits}f} %"


def cagr(series: Sequence[tuple[int, float]]) -> tuple[float | None, str]:
    """Taux de croissance annuel moyen entre le premier et le dernier point.

    Non defini si la valeur de depart est negative ou nulle : un TCAM
    calcule sur une base negative n'a pas de sens economique.
    """
    if len(series) < 2:
        return None, "moins de deux exercices disponibles"
    (y0, v0), (y1, v1) = series[0], series[-1]
    years = y1 - y0
    if years <= 0:
        return None, "exercices non distincts"
    if v0 <= 0:
        return None, f"base de depart negative ou nulle ({y0})"
    if v1 <= 0:
        return None, f"valeur d'arrivee negative ou nulle ({y1})"
    return (v1 / v0) ** (1 / years) - 1, ""


def revenue_cagr(fund: Fundamentals) -> Result:
    series = fund.series("revenue")
    value, reason = cagr(series)
    if value is None:
        return None, "", f"CAGR du chiffre d'affaires non calculable : {reason}"
    cur = fund.snapshot.currency
    detail = (
        f"{series[0][0]} : {_fmt_money(series[0][1], cur)} → "
        f"{series[-1][0]} : {_fmt_money(series[-1][1], cur)} "
        f"({series[-1][0] - series[0][0]} ans)"
    )
    return value, detail, ""


def net_income_cagr(fund: Fundamentals) -> Result:
    series = fund.series("net_income")
    value, reason = cagr(series)
    if value is None:
        return None, "", f"CAGR du resultat net non calculable : {reason}"
    cur = fund.snapshot.currency
    detail = (
        f"{series[0][0]} : {_fmt_money(series[0][1], cur)} → "
        f"{series[-1][0]} : {_fmt_money(series[-1][1], cur)}"
    )
    return value, detail, ""


def _net_margins(fund: Fundamentals) -> list[tuple[int, float]]:
    out = []
    for rec in fund.sorted_annual():
        revenue, net = rec.get("revenue"), rec.get("net_income")
        if revenue and revenue > 0 and net is not None:
            out.append((rec.fiscal_year, net / revenue))
    return out


def net_margin_trend(fund: Fundamentals) -> Result:
    """Evolution de la marge nette : croissance rentable ou destructrice ?

    Sur quatre exercices ou plus, on compare la moyenne des deux premiers a
    celle des deux derniers, pour lisser un exercice exceptionnel.
    """
    margins = _net_margins(fund)
    if len(margins) < 2:
        return None, "", "moins de deux exercices avec marge nette calculable"
    if len(margins) >= 4:
        start = sum(m for _, m in margins[:2]) / 2
        end = sum(m for _, m in margins[-2:]) / 2
        window = f"moyenne {margins[0][0]}-{margins[1][0]} → moyenne {margins[-2][0]}-{margins[-1][0]}"
    else:
        start, end = margins[0][1], margins[-1][1]
        window = f"{margins[0][0]} → {margins[-1][0]}"
    delta = end - start
    detail = f"{window} : {_pct(start)} → {_pct(end)} (soit {delta * 100:+.1f} pts)"
    return delta, detail, ""


def net_margin_avg(fund: Fundamentals) -> Result:
    margins = _net_margins(fund)
    if not margins:
        return None, "", "marge nette non calculable (chiffre d'affaires ou resultat absent)"
    value = sum(m for _, m in margins) / len(margins)
    detail = f"moyenne sur {len(margins)} exercices : " + ", ".join(
        f"{y} {_pct(m, 0)}" for y, m in margins
    )
    return value, detail, ""


def roe_avg(fund: Fundamentals) -> Result:
    """ROE moyen. Les exercices a fonds propres negatifs sont ecartes.

    Un ROE calcule sur des fonds propres negatifs produit un nombre positif
    trompeur ; l'exercice est donc exclu et signale.
    """
    values: list[tuple[int, float]] = []
    skipped: list[int] = []
    for rec in fund.sorted_annual():
        equity, net = rec.get("equity"), rec.get("net_income")
        if equity is None or net is None:
            continue
        if equity <= 0:
            skipped.append(rec.fiscal_year)
            continue
        values.append((rec.fiscal_year, net / equity))
    if not values:
        return None, "", "ROE non calculable (fonds propres ou resultat net absents)"
    value = sum(v for _, v in values) / len(values)
    detail = f"moyenne sur {len(values)} exercices : " + ", ".join(
        f"{y} {_pct(v, 0)}" for y, v in values
    )
    if skipped:
        detail += f" — exercices a fonds propres negatifs ecartes : {skipped}"
    return value, detail, ""


def net_debt_to_ebitda(fund: Fundamentals) -> Result:
    """Dette nette / EBITDA sur le dernier exercice disponible."""
    for rec in reversed(fund.sorted_annual()):
        debt, cash, ebitda = rec.get("total_debt"), rec.get("cash"), rec.get("ebitda")
        if debt is None or ebitda is None:
            continue
        if ebitda <= 0:
            return None, "", f"EBITDA negatif ou nul en {rec.fiscal_year} — ratio non interpretable"
        net_debt = debt - (cash or 0.0)
        cur = fund.snapshot.currency
        detail = (
            f"exercice {rec.fiscal_year} : dette {_fmt_money(debt, cur)} − tresorerie "
            f"{_fmt_money(cash, cur)} = {_fmt_money(net_debt, cur)} ; EBITDA "
            f"{_fmt_money(ebitda, cur)}"
        )
        if net_debt < 0:
            detail += " (tresorerie nette positive)"
        return net_debt / ebitda, detail, ""
    return None, "", "dette totale ou EBITDA absents des donnees disponibles"


def current_ratio(fund: Fundamentals) -> Result:
    for rec in reversed(fund.sorted_annual()):
        ca, cl = rec.get("current_assets"), rec.get("current_liabilities")
        if ca is None or cl is None or cl == 0:
            continue
        cur = fund.snapshot.currency
        detail = (
            f"exercice {rec.fiscal_year} : actifs courants {_fmt_money(ca, cur)} / "
            f"passifs courants {_fmt_money(cl, cur)}"
        )
        return ca / cl, detail, ""
    return None, "", "actifs ou passifs courants absents des donnees disponibles"


def dividend_yield(fund: Fundamentals) -> Result:
    dy = fund.snapshot.dividend_yield
    if dy is None:
        return None, "", "aucun dividende connu pour ce titre"
    if dy <= 0:
        return None, "", "titre ne versant pas de dividende"
    return dy, f"rendement courant : {_pct(dy, 2)}", ""


def dividend_growth_streak(fund: Fundamentals) -> Result:
    """Indice 0-1 de regularite et de croissance du dividende.

    Combinaison a parts egales de :
      - la regularite : part des exercices de la fenetre avec versement ;
      - la croissance : TCAM du dividende par action, plafonne a 10 %/an.
    Une baisse du dividende sur la periode retire 0,2 point d'indice.
    Un titre sans aucun dividende renvoie None : le pilier recoit alors le
    score neutre configure (no_dividend_score), sans penalite.
    """
    series = fund.series("dividend_per_share")
    # L'annee civile en cours n'est pas terminee : la somme des versements y
    # est partielle par construction. La conserver ferait apparaitre une
    # baisse du dividende pour tout titre encore en cours d'exercice.
    current_year = date.today().year
    partial = [y for y, v in series if y >= current_year and v]
    series = [(y, v) for y, v in series if y < current_year]

    positive = [(y, v) for y, v in series if v and v > 0]
    if not positive:
        if partial:
            return None, "", (
                "dividende connu uniquement sur l'annee civile en cours, encore "
                "incomplete — critere non evalue"
            )
        return None, "", "titre ne versant pas de dividende sur la fenetre analysee"

    window_years = max(len([r for r in fund.annual if r.fiscal_year < current_year]), 1)
    consistency = min(len(positive) / window_years, 1.0)

    growth_component = 0.0
    growth_txt = "croissance non calculable"
    if len(positive) >= 2:
        g, _ = cagr(positive)
        if g is not None:
            growth_component = max(0.0, min(g / 0.10, 1.0))
            growth_txt = f"TCAM du dividende {_pct(g)}"

    cut = any(b < a * 0.999 for (_, a), (_, b) in zip(positive, positive[1:]))
    index = 0.5 * consistency + 0.5 * growth_component - (0.2 if cut else 0.0)
    index = max(0.0, min(index, 1.0))

    detail = (
        f"{len(positive)}/{window_years} exercices avec versement ; {growth_txt}"
        + (" ; baisse constatee sur la periode" if cut else "")
        + " — "
        + ", ".join(f"{y} {v:.2f}" for y, v in positive)
        + (f" (annee {current_year} en cours, exclue du calcul)" if partial else "")
    )
    return index, detail, ""


def price_to_book(fund: Fundamentals) -> Result:
    pb = fund.snapshot.price_to_book
    if pb is None:
        return None, "", "P/B non fourni par la source de donnees"
    if pb <= 0:
        return None, "", "P/B negatif (fonds propres comptables negatifs)"
    return pb, f"P/B courant : {pb:.2f}", ""


def eps_growth_rate(fund: Fundamentals) -> tuple[float | None, str]:
    """Croissance annuelle retenue pour le PEG : BPA dilue, sinon resultat net."""
    eps = fund.series("eps_diluted")
    value, _ = cagr(eps)
    if value is not None:
        return value, f"TCAM du BPA dilue {_pct(value)} ({eps[0][0]}-{eps[-1][0]})"
    net = fund.series("net_income")
    value, _ = cagr(net)
    if value is not None:
        return value, f"TCAM du resultat net {_pct(value)} ({net[0][0]}-{net[-1][0]})"
    return None, ""


def peg_ratio(fund: Fundamentals) -> Result:
    """PEG = P/E courant / croissance annuelle en points de pourcentage.

    Non calculable si le P/E est negatif (pertes) ou si la croissance est
    negative ou nulle : dans ces cas le ratio n'a pas d'interpretation.
    """
    pe = fund.snapshot.trailing_pe
    if pe is None:
        return None, "", "P/E courant non disponible"
    if pe <= 0:
        return None, "", "P/E negatif (societe en perte) — PEG non interpretable"
    growth, growth_txt = eps_growth_rate(fund)
    if growth is None:
        return None, "", "croissance des benefices non calculable"
    if growth <= 0:
        return None, "", f"croissance des benefices negative ou nulle ({_pct(growth)}) — PEG non interpretable"
    value = pe / (growth * 100)
    detail = f"P/E {pe:.1f} ÷ croissance {growth * 100:.1f} — {growth_txt}"
    return value, detail, ""


def historical_pe(fund: Fundamentals, prices: pd.DataFrame | None) -> tuple[list[tuple[int, float]], str]:
    """P/E historique par exercice : cours a la cloture / BPA de l'exercice."""
    if prices is None or prices.empty:
        return [], "historique de cours indisponible"
    out: list[tuple[int, float]] = []
    for rec in fund.sorted_annual():
        eps, end = rec.get("eps_diluted"), rec.period_end
        if eps is None or eps <= 0 or end is None:
            continue
        try:
            window = prices.loc[: pd.Timestamp(end)]
        except (KeyError, TypeError):
            continue
        if window.empty:
            continue
        close = float(window["Close"].iloc[-1])
        out.append((rec.fiscal_year, close / eps))
    return out, "" if out else "aucun exercice avec BPA positif et cours connu"


def pe_vs_own_history(fund: Fundamentals, prices: pd.DataFrame | None) -> Result:
    pe = fund.snapshot.trailing_pe
    if pe is None or pe <= 0:
        return None, "", "P/E courant indisponible ou negatif"
    history, reason = historical_pe(fund, prices)
    if len(history) < 3:
        return None, "", f"historique de P/E insuffisant ({len(history)} exercices, 3 requis) : {reason or 'donnees partielles'}"
    mean_pe = sum(v for _, v in history) / len(history)
    if mean_pe <= 0:
        return None, "", "P/E moyen historique non exploitable"
    ratio = pe / mean_pe
    detail = (
        f"P/E courant {pe:.1f} vs moyenne {mean_pe:.1f} sur {len(history)} exercices "
        f"(" + ", ".join(f"{y} {v:.0f}" for y, v in history) + ")"
    )
    return ratio, detail, ""


# Criteres calculables titre par titre. Le critere pe_vs_sector est relatif
# aux pairs et se calcule au niveau de l'univers (voir scoring.py).
SINGLE_STOCK_CRITERIA = {
    "revenue_cagr": revenue_cagr,
    "net_income_cagr": net_income_cagr,
    "net_margin_trend": net_margin_trend,
    "roe_avg": roe_avg,
    "net_margin_avg": net_margin_avg,
    "net_debt_to_ebitda": net_debt_to_ebitda,
    "current_ratio": current_ratio,
    "dividend_yield": dividend_yield,
    "dividend_growth_streak": dividend_growth_streak,
    "peg_ratio": peg_ratio,
    "price_to_book": price_to_book,
}


def compute_all(fund: Fundamentals, prices: pd.DataFrame | None = None) -> dict[str, Result]:
    results: dict[str, Result] = {}
    for key, fn in SINGLE_STOCK_CRITERIA.items():
        try:
            results[key] = fn(fund)
        except Exception as exc:  # noqa: BLE001
            results[key] = (None, "", f"erreur de calcul ({type(exc).__name__}: {exc})")
    try:
        results["pe_vs_own_history"] = pe_vs_own_history(fund, prices)
    except Exception as exc:  # noqa: BLE001
        results["pe_vs_own_history"] = (None, "", f"erreur de calcul ({type(exc).__name__})")
    return results
