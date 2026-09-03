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

import statistics
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
        return None, f"base de départ négative ou nulle ({y0})"
    if v1 <= 0:
        return None, f"valeur d'arrivée négative ou nulle ({y1})"
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
        return None, "", f"CAGR du résultat net non calculable : {reason}"
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
        return None, "", "marge nette non calculable (chiffre d'affaires ou résultat absent)"
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
        # Message distinct selon la cause : des fonds propres NEGATIFS ne sont
        # pas une donnee manquante mais un choix de structure de capital
        # (rachats d'actions superieurs aux benefices accumules, cas de
        # Starbucks ou McDonald's). Annoncer « fonds propres absents » serait
        # faux et laisserait croire a un defaut de la source.
        if skipped:
            return None, "", (
                f"fonds propres comptables négatifs sur tous les exercices "
                f"({', '.join(str(y) for y in skipped)}) — ROE sans signification ; "
                "voir la rentabilité du capital employé"
            )
        return None, "", "ROE non calculable (fonds propres ou résultat net absents)"
    value = sum(v for _, v in values) / len(values)
    detail = f"moyenne sur {len(values)} exercices : " + ", ".join(
        f"{y} {_pct(v, 0)}" for y, v in values
    )
    if skipped:
        detail += f" — exercices à fonds propres négatifs écartés : {skipped}"
    return value, detail, ""


def roce_avg(fund: Fundamentals) -> Result:
    """Rentabilite du capital employe : resultat d'exploitation / capital employe.

    Capital employe = total de l'actif - passifs courants, c'est-a-dire les
    capitaux durables reellement mobilises, dette comprise.

    Pourquoi ce critere en complement du ROE : le ROE rapporte le benefice aux
    seuls fonds propres, donc une societe qui s'endette pour racheter ses
    actions ameliore mecaniquement son ROE sans rien ameliorer de son
    exploitation. Le capital employe inclut la dette : la performance ne peut
    plus etre fabriquee par le levier. C'est aussi le seul des deux qui reste
    calculable quand les fonds propres sont negatifs.

    Volontairement le ROCE et non le ROIC : le ROIC exige un taux d'impot
    effectif que les sources gratuites ne publient pas de facon fiable, et le
    poser par convention reviendrait a inventer une donnee.
    """
    values: list[tuple[int, float]] = []
    for rec in fund.sorted_annual():
        op = rec.get("operating_income")
        assets, cl = rec.get("total_assets"), rec.get("current_liabilities")
        if op is None or assets is None or cl is None:
            continue
        employe = assets - cl
        if employe <= 0:
            continue
        values.append((rec.fiscal_year, op / employe))
    if not values:
        return None, "", (
            "capital employé non calculable (résultat d'exploitation, total de "
            "l'actif ou passifs courants absents)"
        )
    value = sum(v for _, v in values) / len(values)
    detail = f"moyenne sur {len(values)} exercices : " + ", ".join(
        f"{y} {_pct(v, 0)}" for y, v in values
    )
    return value, detail, ""


def gross_margin_avg(fund: Fundamentals) -> Result:
    """Marge brute moyenne : meilleur indicateur simple du pouvoir de prix.

    Une marge brute elevee et stable signale une offre difficile a remplacer.
    A la difference de la marge nette, elle est peu sensible a la structure
    financiere et aux elements exceptionnels.
    """
    values: list[tuple[int, float]] = []
    for rec in fund.sorted_annual():
        gp, revenue = rec.get("gross_profit"), rec.get("revenue")
        if gp is None or not revenue or revenue <= 0:
            continue
        values.append((rec.fiscal_year, gp / revenue))
    if not values:
        return None, "", "résultat brut non publié par la source pour ce titre"
    value = sum(v for _, v in values) / len(values)
    detail = f"moyenne sur {len(values)} exercices : " + ", ".join(
        f"{y} {_pct(v, 0)}" for y, v in values
    )
    return value, detail, ""


def cash_conversion(fund: Fundamentals) -> Result:
    """Conversion du benefice en tresorerie : free cash flow / resultat net.

    Le controle de qualite des benefices le plus direct : le resultat
    comptable se pilote (provisions, etalements, depreciations), les
    encaissements beaucoup moins. Un ratio durablement inferieur a 0,6 signale
    un benefice qui ne se transforme pas en argent disponible.

    Les exercices a resultat net negatif sont ecartes : le rapport y change de
    signe et perd toute lisibilite.
    """
    values: list[tuple[int, float]] = []
    for rec in fund.sorted_annual():
        fcf, net = rec.get("free_cash_flow"), rec.get("net_income")
        if fcf is None or net is None or net <= 0:
            continue
        values.append((rec.fiscal_year, fcf / net))
    if not values:
        return None, "", (
            "conversion non calculable (free cash flow indisponible ou aucun "
            "exercice bénéficiaire sur la fenêtre)"
        )
    value = sum(v for _, v in values) / len(values)
    detail = f"moyenne sur {len(values)} exercices : " + ", ".join(
        f"{y} {v:.2f}" for y, v in values
    )
    return value, detail, ""


def fcf_yield(fund: Fundamentals) -> Result:
    """Rendement du free cash flow : FCF du dernier exercice / capitalisation.

    Critere de valorisation qui reste calculable la ou le P/E ne l'est pas :
    une societe en perte comptable peut tres bien degager de la tresorerie, et
    a l'inverse un FCF negatif est une information, pas une absence de donnee.
    """
    cap = fund.snapshot.market_cap
    if not cap or cap <= 0:
        return None, "", "capitalisation boursière non disponible"
    for rec in reversed(fund.sorted_annual()):
        fcf = rec.get("free_cash_flow")
        if fcf is None:
            continue
        cur = fund.snapshot.currency
        detail = (
            f"exercice {rec.fiscal_year} : free cash flow {_fmt_money(fcf, cur)} / "
            f"capitalisation {_fmt_money(cap, cur)}"
        )
        if fcf < 0:
            detail += " (trésorerie consommée)"
        return fcf / cap, detail, ""
    return None, "", "free cash flow absent des données disponibles"


def ev_to_sales(fund: Fundamentals) -> Result:
    """Valeur d'entreprise / chiffre d'affaires.

    Repli de valorisation pour les societes sans benefice : le P/E, le PEG et
    le P/E historique sont alors tous les trois indisponibles, ce qui suffisait
    a neutraliser le pilier valorisation et a faire disparaitre du classement
    des societes en forte croissance pas encore rentables.
    """
    cap = fund.snapshot.market_cap
    if not cap or cap <= 0:
        return None, "", "capitalisation boursière non disponible"
    for rec in reversed(fund.sorted_annual()):
        revenue = rec.get("revenue")
        if not revenue or revenue <= 0:
            continue
        debt, cash = rec.get("total_debt"), rec.get("cash")
        net_debt = (debt or 0.0) - (cash or 0.0)
        ve = cap + net_debt
        cur = fund.snapshot.currency
        detail = (
            f"exercice {rec.fiscal_year} : capitalisation {_fmt_money(cap, cur)} + dette "
            f"nette {_fmt_money(net_debt, cur)} = {_fmt_money(ve, cur)} ; chiffre "
            f"d'affaires {_fmt_money(revenue, cur)}"
        )
        if debt is None:
            detail += " (dette non publiée, valeur d'entreprise approchée)"
        return ve / revenue, detail, ""
    return None, "", "chiffre d'affaires absent des données disponibles"


def interest_coverage(fund: Fundamentals) -> Result:
    """Couverture des interets : resultat d'exploitation / charge d'interets.

    Mesure la marge de securite face au service de la dette. Une charge
    d'interets nulle ou absente signale en general une societe sans dette
    financiere : le critere est alors porte au maximum du bareme plutot que
    marque manquant.
    """
    for rec in reversed(fund.sorted_annual()):
        op, interest = rec.get("operating_income"), rec.get("interest_expense")
        if op is None:
            continue
        cur = fund.snapshot.currency
        if interest is None or interest <= 0:
            debt = rec.get("total_debt")
            if debt is not None and debt > 0:
                continue  # de la dette mais pas de charge publiee : on ne conclut pas
            return 100.0, (
                f"exercice {rec.fiscal_year} : aucune charge d'intérêts significative"
            ), ""
        return op / interest, (
            f"exercice {rec.fiscal_year} : résultat d'exploitation "
            f"{_fmt_money(op, cur)} / charge d'intérêts {_fmt_money(interest, cur)}"
        ), ""
    return None, "", "résultat d'exploitation ou charge d'intérêts absents"


def equity_to_assets(fund: Fundamentals) -> Result:
    """Fonds propres / total de l'actif : mesure de levier universelle.

    Seul critere de solidite qui garde un sens pour une banque ou un
    assureur, dont le bilan ne se lit ni en « dette nette sur EBITDA » ni en
    ratio de liquidite generale.
    """
    for rec in reversed(fund.sorted_annual()):
        equity, assets = rec.get("equity"), rec.get("total_assets")
        if equity is None or not assets or assets <= 0:
            continue
        cur = fund.snapshot.currency
        detail = (
            f"exercice {rec.fiscal_year} : fonds propres {_fmt_money(equity, cur)} / "
            f"total de l'actif {_fmt_money(assets, cur)}"
        )
        if equity < 0:
            detail += " (fonds propres comptables négatifs)"
        return equity / assets, detail, ""
    return None, "", "fonds propres ou total de l'actif absents"


def share_count_trend(fund: Fundamentals) -> Result:
    """Reduction annuelle du nombre d'actions (positif = relutif).

    Une croissance du chiffre d'affaires financee par emission d'actions
    n'enrichit pas l'actionnaire en place. A l'inverse, une base d'actions qui
    se reduit ajoute a la performance par action. Aucun autre critere ne
    capture cette dimension.
    """
    series = fund.series("shares_diluted")
    series = [(y, v) for y, v in series if v and v > 0]
    if len(series) < 2:
        return None, "", "nombre d'actions non publié sur au moins deux exercices"
    growth, reason = cagr(series)
    if growth is None:
        return None, "", f"évolution du nombre d'actions non calculable : {reason}"
    value = -growth
    sens = "réduction" if value > 0 else "dilution"
    detail = (
        f"{series[0][0]} : {series[0][1] / 1e6:,.0f} M d'actions → {series[-1][0]} : "
        f"{series[-1][1] / 1e6:,.0f} M — {sens} de {abs(value) * 100:.1f} %/an"
    ).replace(",", " ")
    return value, detail, ""


def net_debt_to_ebitda(fund: Fundamentals) -> Result:
    """Levier : dette nette rapportee a la capacite de remboursement annuelle.

    L'EBITDA sert de reference. Quand il est negatif, on se replie sur le free
    cash flow s'il est positif : c'est exactement le cas des editeurs de
    logiciels, dont la remuneration en actions creuse le resultat comptable
    alors que la tresorerie rentre. Sans ce repli, leur pilier bilan etait
    neutralise et ces societes sortaient du classement.

    Le bareme reste le meme dans les deux cas : les deux ratios expriment un
    nombre d'annees de remboursement. Le free cash flow etant plus etroit que
    l'EBITDA, la lecture obtenue est un peu plus severe — c'est le sens
    prudent, et l'origine du chiffre est indiquee dans le detail.
    """
    for rec in reversed(fund.sorted_annual()):
        debt, cash = rec.get("total_debt"), rec.get("cash")
        if debt is None:
            continue
        ebitda, fcf = rec.get("ebitda"), rec.get("free_cash_flow")
        if ebitda is not None and ebitda > 0:
            base, base_label = ebitda, "EBITDA"
        elif fcf is not None and fcf > 0:
            base, base_label = fcf, "free cash flow (EBITDA négatif)"
        elif ebitda is None and fcf is None:
            continue
        else:
            return None, "", (
                f"EBITDA et free cash flow négatifs ou nuls en {rec.fiscal_year} — "
                "aucune capacité de remboursement à rapporter à la dette"
            )
        net_debt = debt - (cash or 0.0)
        cur = fund.snapshot.currency
        detail = (
            f"exercice {rec.fiscal_year} : dette {_fmt_money(debt, cur)} − trésorerie "
            f"{_fmt_money(cash, cur)} = {_fmt_money(net_debt, cur)} ; {base_label} "
            f"{_fmt_money(base, cur)}"
        )
        if net_debt < 0:
            detail += " (trésorerie nette positive)"
        return net_debt / base, detail, ""
    return None, "", "dette totale absente des données disponibles"


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
    return None, "", "actifs ou passifs courants absents des données disponibles"


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
                "dividende connu uniquement sur l'année civile en cours, encore "
                "incomplète — critère non évalué"
            )
        return None, "", "titre ne versant pas de dividende sur la fenêtre analysée"

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
        + (" ; baisse constatée sur la période" if cut else "")
        + " — "
        + ", ".join(f"{y} {v:.2f}" for y, v in positive)
        + (f" (année {current_year} en cours, exclue du calcul)" if partial else "")
    )
    return index, detail, ""


def price_to_book(fund: Fundamentals) -> Result:
    pb = fund.snapshot.price_to_book
    if pb is None:
        return None, "", "P/B non fourni par la source de données"
    if pb <= 0:
        return None, "", "P/B négatif (fonds propres comptables négatifs)"
    return pb, f"P/B courant : {pb:.2f}", ""


def eps_growth_rate(fund: Fundamentals) -> tuple[float | None, str]:
    """Croissance annuelle retenue pour le PEG : BPA dilue, sinon resultat net."""
    eps = fund.series("eps_diluted")
    value, _ = cagr(eps)
    if value is not None:
        return value, f"TCAM du BPA dilué {_pct(value)} ({eps[0][0]}-{eps[-1][0]})"
    net = fund.series("net_income")
    value, _ = cagr(net)
    if value is not None:
        return value, f"TCAM du résultat net {_pct(value)} ({net[0][0]}-{net[-1][0]})"
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
        return None, "", "P/E négatif (société en perte) — PEG non interprétable"
    growth, growth_txt = eps_growth_rate(fund)
    if growth is None:
        return None, "", "croissance des bénéfices non calculable"
    if growth <= 0:
        return None, "", f"croissance des bénéfices négative ou nulle ({_pct(growth)}) — PEG non interprétable"
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
    """P/E courant rapporte a la MEDIANE de son P/E historique.

    La mediane et non la moyenne : un exercice a benefice quasi nul produit un
    P/E de plusieurs centaines, qui tire la moyenne vers le haut et fait
    passer le titre pour bon marche alors qu'il ne l'est pas. Mesure sur
    l'univers CAC 40 + Nasdaq-100 : neuf titres portaient une valeur aberrante
    superieure a cinq fois la mediane de leur propre serie, et le passage a la
    mediane deplace 49 titres sur 120 de cinq points ou plus sur ce critere,
    presque toujours a la baisse. La moyenne biaisait donc le pilier
    valorisation vers l'optimisme.
    """
    pe = fund.snapshot.trailing_pe
    if pe is None or pe <= 0:
        return None, "", "P/E courant indisponible ou négatif"
    history, reason = historical_pe(fund, prices)
    if len(history) < 3:
        return None, "", f"historique de P/E insuffisant ({len(history)} exercices, 3 requis) : {reason or 'données partielles'}"
    values = [v for _, v in history]
    median_pe = statistics.median(values)
    if median_pe <= 0:
        return None, "", "P/E médian historique non exploitable"
    ratio = pe / median_pe
    detail = (
        f"P/E courant {pe:.1f} vs médiane {median_pe:.1f} sur {len(history)} exercices "
        f"(" + ", ".join(f"{y} {v:.0f}" for y, v in history) + ")"
    )
    # Signaler l'exercice atypique plutot que de le masquer : c'est ce qui
    # explique l'ecart entre la mediane retenue et une moyenne naive.
    if max(values) > 5 * median_pe:
        detail += (
            f" — exercice atypique à P/E {max(values):.0f} neutralisé par la médiane"
        )
    return ratio, detail, ""


# Criteres calculables titre par titre. Le critere pe_vs_sector est relatif
# aux pairs et se calcule au niveau de l'univers (voir scoring.py).
SINGLE_STOCK_CRITERIA = {
    "revenue_cagr": revenue_cagr,
    "net_income_cagr": net_income_cagr,
    "net_margin_trend": net_margin_trend,
    "roe_avg": roe_avg,
    "roce_avg": roce_avg,
    "net_margin_avg": net_margin_avg,
    "gross_margin_avg": gross_margin_avg,
    "cash_conversion": cash_conversion,
    "net_debt_to_ebitda": net_debt_to_ebitda,
    "current_ratio": current_ratio,
    "interest_coverage": interest_coverage,
    "equity_to_assets": equity_to_assets,
    "share_count_trend": share_count_trend,
    "dividend_yield": dividend_yield,
    "dividend_growth_streak": dividend_growth_streak,
    "peg_ratio": peg_ratio,
    "fcf_yield": fcf_yield,
    "ev_to_sales": ev_to_sales,
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
