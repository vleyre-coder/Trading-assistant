"""SEC EDGAR — source officielle, gratuite, illimitee (societes cotees US).

API XBRL "companyfacts" : https://www.sec.gov/edgar/sec-api-documentation
Aucune cle requise, mais la SEC impose un User-Agent identifiant et une
limite de 10 requetes/seconde.

Cette source ne couvre QUE les societes deposant aupres de la SEC (10-K /
10-Q). Les societes europeennes non cotees aux Etats-Unis en sont absentes :
c'est la raison structurelle de l'ecart de qualite de donnees US / Europe.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Iterable

from ..config import Settings
from ..models import AnnualRecord
from .base import DiskCache, RateLimiter, get_json, make_session

log = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# Chaines de repli : les emetteurs n'utilisent pas tous les memes balises XBRL.
FLOW_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ),
    "net_income": (
        "NetIncomeLoss",
        "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    ),
    "operating_income": ("OperatingIncomeLoss",),
    "depreciation_amortization": (
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
        "AmortizationOfIntangibleAssets",
    ),
    "eps_diluted": ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"),
    "dividend_per_share": (
        "CommonStockDividendsPerShareDeclared",
        "CommonStockDividendsPerShareCashPaid",
    ),
}

INSTANT_TAGS: dict[str, tuple[str, ...]] = {
    "equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "cash": ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsAndShortTermInvestments"),
    "short_term_investments": ("ShortTermInvestments", "MarketableSecuritiesCurrent"),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
    "short_term_debt": ("LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"),
}

ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "40-F")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def fiscal_year_of(period_end: date) -> int:
    """Exercice attribue a une date de cloture.

    Convention : l'exercice porte l'annee civile ou tombe la majorite de la
    periode. Une cloture en janvier-mai est donc rattachee a l'annee
    precedente (cas des distributeurs cloturant fin janvier).
    """
    return period_end.year if period_end.month >= 6 else period_end.year - 1


class EdgarClient:
    def __init__(self, settings: Settings, cache: DiskCache | None = None) -> None:
        self.settings = settings
        self.session = make_session(settings.sec_user_agent)
        self.limiter = RateLimiter(settings.sec_rate_limit)
        self.cache = cache or DiskCache(settings.cache_dir, settings.cache_ttl_hours)
        self._ticker_map: dict[str, str] | None = None
        self._period_ends: dict[int, date] = {}
        self._filed: dict[tuple[str, int], str] = {}

    # ---------------------------------------------------------------- CIK
    def ticker_to_cik(self, ticker: str) -> str | None:
        """Table ticker -> CIK (mise en cache 30 jours, elle bouge peu)."""
        if self._ticker_map is None:
            cached = self.cache.get("edgar_tickers", "company_tickers")
            if cached is None:
                data = get_json(self.session, TICKERS_URL, limiter=self.limiter)
                if data is None:
                    log.warning("Table des tickers SEC indisponible.")
                    self._ticker_map = {}
                    return None
                cached = {
                    str(row["ticker"]).upper(): f"{int(row['cik_str']):010d}"
                    for row in data.values()
                }
                self.cache.set("edgar_tickers", "company_tickers", cached)
            self._ticker_map = cached
        # Les tickers europeens (suffixes .PA, .DE...) ne sont jamais dans EDGAR.
        return self._ticker_map.get(ticker.upper())

    # -------------------------------------------------------- companyfacts
    def company_facts(self, cik: str) -> dict[str, Any] | None:
        cached = self.cache.get("edgar_facts", cik)
        if cached is not None:
            return cached
        data = get_json(
            self.session, COMPANYFACTS_URL.format(cik=cik), limiter=self.limiter, timeout=60
        )
        if data is not None:
            self.cache.set("edgar_facts", cik, data)
        return data

    # ------------------------------------------------------------ parsing
    @staticmethod
    def _units(facts: dict[str, Any], tag: str) -> list[dict[str, Any]] | None:
        for taxonomy in ("us-gaap", "ifrs-full", "dei"):
            node = (facts.get("facts") or {}).get(taxonomy, {}).get(tag)
            if node:
                units = node.get("units") or {}
                # On privilegie USD puis USD/shares, sinon la premiere unite.
                for key in ("USD", "USD/shares", "pure"):
                    if key in units:
                        return units[key]
                if units:
                    return next(iter(units.values()))
        return None

    def _annual_flows(
        self, facts: dict[str, Any], tags: Iterable[str], name: str = ""
    ) -> dict[int, float]:
        """Valeurs annuelles d'un agregat de flux (CA, resultat...).

        On ne garde que les periodes d'environ 12 mois issues d'un rapport
        annuel, et pour chaque exercice la publication la plus recente
        (les retraitements ecrasent les premieres versions).
        """
        best: dict[int, tuple[str, float, date]] = {}
        for tag in tags:
            entries = self._units(facts, tag)
            if not entries:
                continue
            for e in entries:
                if e.get("form") not in ANNUAL_FORMS:
                    continue
                start, end = _parse_date(e.get("start")), _parse_date(e.get("end"))
                if not start or not end:
                    continue
                duration = (end - start).days
                if not 330 <= duration <= 400:
                    continue
                fy = fiscal_year_of(end)
                filed = str(e.get("filed") or "")
                val = e.get("val")
                if val is None:
                    continue
                prev = best.get(fy)
                if prev is None or filed > prev[0]:
                    best[fy] = (filed, float(val), end)
            if best:
                # Balise trouvee : on ne melange pas plusieurs definitions du
                # meme agregat, sauf pour completer des exercices manquants.
                break
        self._period_ends.update({fy: e for fy, (_, _, e) in best.items()})
        if name:
            self._filed.update({(name, fy): f for fy, (f, _, _) in best.items()})
        return {fy: v for fy, (_, v, _) in best.items()}

    def _instant_values(self, facts: dict[str, Any], tags: Iterable[str]) -> dict[int, float]:
        """Valeurs de bilan (instantanees) rattachees a chaque exercice."""
        best: dict[int, tuple[date, str, float]] = {}
        for tag in tags:
            entries = self._units(facts, tag)
            if not entries:
                continue
            for e in entries:
                if e.get("form") not in ANNUAL_FORMS or e.get("start"):
                    continue
                end = _parse_date(e.get("end"))
                val = e.get("val")
                if not end or val is None:
                    continue
                fy = fiscal_year_of(end)
                filed = str(e.get("filed") or "")
                prev = best.get(fy)
                # A exercice egal : date de cloture la plus tardive, puis
                # publication la plus recente.
                if prev is None or (end, filed) > (prev[0], prev[1]):
                    best[fy] = (end, filed, float(val))
            if best:
                break
        return {fy: v for fy, (_, _, v) in best.items()}

    # -------------------------------------------------------------- public
    def annual_records(self, ticker: str) -> tuple[list[AnnualRecord], list[str]]:
        """Historique annuel normalise. Renvoie (enregistrements, avertissements)."""
        warnings: list[str] = []
        self._period_ends = {}
        self._filed = {}
        cik = self.ticker_to_cik(ticker)
        if not cik:
            return [], [f"{ticker} absent du registre SEC (société non cotée aux États-Unis)."]

        facts = self.company_facts(cik)
        if not facts:
            return [], [f"EDGAR : companyfacts indisponible pour {ticker} (CIK {cik})."]

        flows = {name: self._annual_flows(facts, tags, name) for name, tags in FLOW_TAGS.items()}
        instants = {name: self._instant_values(facts, tags) for name, tags in INSTANT_TAGS.items()}

        years = sorted(
            set(flows["revenue"]) | set(flows["net_income"]) | set(instants["equity"])
        )
        if not years:
            return [], [f"EDGAR : aucune donnee annuelle exploitable pour {ticker}."]

        records: list[AnnualRecord] = []
        for fy in years:
            op = flows["operating_income"].get(fy)
            da = flows["depreciation_amortization"].get(fy)
            ebitda = op + da if op is not None and da is not None else None
            if ebitda is None and op is not None:
                # Sans D&A publiee, on n'invente pas d'EBITDA : le critere
                # dette nette / EBITDA sera marque N/A pour cet exercice.
                ebitda = None

            ltd = instants["long_term_debt"].get(fy)
            std = instants["short_term_debt"].get(fy)
            total_debt = None if ltd is None and std is None else (ltd or 0.0) + (std or 0.0)

            cash = instants["cash"].get(fy)
            sti = instants["short_term_investments"].get(fy)
            cash_total = None if cash is None else cash + (sti or 0.0)

            records.append(
                AnnualRecord(
                    fiscal_year=fy,
                    period_end=self._period_ends.get(fy),
                    filed={
                        name: self._filed[(name, fy)]
                        for name in ("eps_diluted", "dividend_per_share")
                        if (name, fy) in self._filed
                    },
                    values={
                        "revenue": flows["revenue"].get(fy),
                        "net_income": flows["net_income"].get(fy),
                        "operating_income": op,
                        "ebitda": ebitda,
                        "equity": instants["equity"].get(fy),
                        "total_debt": total_debt,
                        "cash": cash_total,
                        "current_assets": instants["current_assets"].get(fy),
                        "current_liabilities": instants["current_liabilities"].get(fy),
                        "eps_diluted": flows["eps_diluted"].get(fy),
                        "dividend_per_share": flows["dividend_per_share"].get(fy),
                    },
                )
            )

        missing_ebitda = sum(1 for r in records if r.get("ebitda") is None)
        if missing_ebitda == len(records):
            warnings.append(
                "EDGAR : EBITDA non reconstituable (résultat opérationnel ou "
                "amortissements absents des balises XBRL)."
            )
        return records, warnings
