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
    "gross_profit": ("GrossProfit",),
    # Tresorerie : postes obligatoires du tableau de flux americain.
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "capex": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsForCapitalImprovements",
    ),
    "interest_expense": (
        "InterestExpense",
        "InterestExpenseDebt",
        "InterestIncomeExpenseNet",
    ),
    "shares_diluted": (
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasic",
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
    "total_assets": ("Assets",),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
    "short_term_debt": ("LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"),
}

ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "40-F")

# Agregats qui sont des TOTAUX : aucune de leurs composantes ne peut etre plus
# grande qu'eux. Pour ceux-la, toutes les balises candidates sont lues et la
# plus grande valeur de l'exercice est retenue. Voir _annual_flows.
# Volontairement limite au chiffre d'affaires : « la plus grande » n'a aucun
# sens pour un resultat net, ou deux definitions legitimes (part du groupe,
# ensemble consolide) divergent sans que la plus grande soit la bonne.
TOTAUX_CONSOLIDES = frozenset({"revenue"})


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
    # Unites non monetaires : elles ne dependent d'aucune devise et restent
    # lisibles quelle que soit celle retenue pour les comptes.
    UNITES_NEUTRES = ("shares", "pure")

    def _devise_des_comptes(self, facts: dict[str, Any], *, fenetre: int = 5) -> str:
        """Devise UNIQUE dans laquelle lire tous les postes monetaires.

        Pourquoi choisir une fois pour toutes plutot que balise par balise :
        un emetteur etranger cote aux Etats-Unis depose ses comptes dans sa
        devise ET une traduction de commodite en dollar, mais pas forcement
        pour toutes les balises ni toutes les annees. Le choix balise par
        balise fabriquait alors des exercices MELANGES.

        Cas mesure sur PDD Holdings : chiffre d'affaires lu en dollar
        (61,8 Md) et marge brute completee en yuan (243,0 Md) dans le meme
        exercice — une marge brute de 394 % du chiffre d'affaires, et un free
        cash flow superieur au chiffre d'affaires.

        Regle : la devise qui couvre le plus d'exercices recents sur les
        postes structurants ; a egalite, le dollar, unite de reference de la
        SEC.
        """
        couverture: dict[str, set[int]] = {}
        for poste in ("revenue", "net_income", "operating_income"):
            for tag in FLOW_TAGS[poste]:
                node = self._noeud(facts, tag)
                if not node:
                    continue
                for unite, entrees in (node.get("units") or {}).items():
                    if unite in self.UNITES_NEUTRES or "/" in unite:
                        continue
                    for e in entrees:
                        if e.get("form") not in ANNUAL_FORMS:
                            continue
                        debut, fin = _parse_date(e.get("start")), _parse_date(e.get("end"))
                        if not debut or not fin or not 330 <= (fin - debut).days <= 400:
                            continue
                        couverture.setdefault(unite, set()).add(fiscal_year_of(fin))
        if not couverture:
            return "USD"
        # Seuls les exercices recents comptent : une devise qui couvre 2016
        # mais pas 2025 n'aide en rien pour une fenetre de cinq exercices.
        recents = sorted({a for annees in couverture.values() for a in annees})[-fenetre:]
        classement = sorted(
            couverture,
            key=lambda u: (len(couverture[u] & set(recents)), u == "USD", u),
            reverse=True,
        )
        return classement[0]

    @staticmethod
    def _noeud(facts: dict[str, Any], tag: str) -> dict[str, Any] | None:
        for taxonomy in ("us-gaap", "ifrs-full", "dei"):
            node = (facts.get("facts") or {}).get(taxonomy, {}).get(tag)
            if node:
                return node
        return None

    @classmethod
    def _units(
        cls, facts: dict[str, Any], tag: str, devise: str = "USD"
    ) -> list[dict[str, Any]] | None:
        """Serie d'un poste, dans la devise retenue pour la societe UNIQUEMENT.

        Aucune retombee sur une autre devise : mieux vaut un poste absent —
        que la source complementaire pourra fournir, convertie et signalee —
        qu'un exercice qui additionne deux monnaies.

        La devise est un PARAMETRE et non un attribut : ce client est partage
        par plusieurs fils d'execution (voir annual_records).
        """
        node = cls._noeud(facts, tag)
        if not node:
            return None
        units = node.get("units") or {}
        for key in (devise, f"{devise}/shares", *cls.UNITES_NEUTRES):
            if key in units:
                return units[key]
        return None

    def _annual_flows(
        self,
        facts: dict[str, Any],
        tags: Iterable[str],
        *,
        devise: str = "USD",
        total_consolide: bool = False,
    ) -> dict[int, tuple[str, float, date]]:
        """Valeurs annuelles d'un agregat de flux (CA, resultat...).

        On ne garde que les periodes d'environ 12 mois issues d'un rapport
        annuel, et pour chaque exercice la publication la plus recente
        (les retraitements ecrasent les premieres versions).

        Renvoie, par exercice, (date de depot, valeur, date de cloture). Le
        depot et la cloture ne sont pas des details : c'est la date de DEPOT
        qui dit si une donnee par action est deja retraitee d'une division
        d'actions, et la cloture qui permet de retrouver le cours de fin
        d'exercice. Ils etaient auparavant stockes sur l'instance — voir
        annual_records.

        total_consolide : l'agregat recherche est un TOTAL, dont aucune
        composante ne peut etre plus grande. Toutes les balises candidates
        sont alors lues et la plus grande valeur de chaque exercice est
        retenue, au lieu de s'arreter a la premiere balise renseignee.

        Pourquoi : certains emetteurs reservent la balise « revenus des
        contrats clients » a UNE LIGNE de leur compte de resultat et publient
        le total sous « Revenus ». Mesure sur l'univers analyse — Charter
        Communications, chiffre d'affaires 2025 lu a 889 M$ au lieu de
        54,8 Md$ (98 % d'ecart), et MercadoLibre a 20,3 Md$ au lieu de
        28,9 Md$. Deux titres sur 101 americains, mais toute la croissance,
        toutes les marges et tous les ratios de valorisation en dependent.
        """
        best: dict[int, tuple[str, float, date]] = {}
        for tag in tags:
            entries = self._units(facts, tag, devise)
            if not entries:
                continue
            courant: dict[int, tuple[str, float, date]] = {}
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
                prev = courant.get(fy)
                # Publication la plus recente : les retraitements ecrasent les
                # premieres versions.
                if prev is None or filed > prev[0]:
                    courant[fy] = (filed, float(val), end)
            if not total_consolide:
                for fy, valeur in courant.items():
                    prev = best.get(fy)
                    if prev is None or valeur[0] > prev[0]:
                        best[fy] = valeur
                if best:
                    # Balise trouvee : on ne melange pas plusieurs definitions
                    # du meme agregat.
                    break
                continue
            for fy, valeur in courant.items():
                prev = best.get(fy)
                if prev is None or valeur[1] > prev[1]:
                    best[fy] = valeur
        return best

    def _instant_values(
        self, facts: dict[str, Any], tags: Iterable[str], *, devise: str = "USD"
    ) -> dict[int, float]:
        """Valeurs de bilan (instantanees) rattachees a chaque exercice."""
        best: dict[int, tuple[date, str, float]] = {}
        for tag in tags:
            entries = self._units(facts, tag, devise)
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
        cik = self.ticker_to_cik(ticker)
        if not cik:
            return [], [f"{ticker} absent du registre SEC (société non cotée aux États-Unis)."]

        facts = self.company_facts(cik)
        if not facts:
            return [], [f"EDGAR : companyfacts indisponible pour {ticker} (CIK {cik})."]

        # A fixer AVANT toute lecture : c'est elle qui determine quelle serie
        # est lue pour chaque poste.
        devise = self._devise_des_comptes(facts)
        if devise != "USD":
            warnings.append(
                f"Comptes deposes a la SEC en {devise} : tous les postes "
                f"sont lus dans cette devise."
            )

        # Tout l'etat de lecture est LOCAL a cet appel. Il vivait auparavant
        # sur l'instance, partagee par les fils d'execution du screener : un
        # titre lu en parallele d'un autre reinitialisait les dates de cloture
        # au milieu de son analyse. Reproduit en test : les cinq exercices du
        # titre le plus lent revenaient sans aucune date de cloture, ce qui
        # supprime silencieusement son P/E historique et prive le
        # retraitement des divisions d'actions de sa date de reference.
        brut = {
            name: self._annual_flows(
                facts, tags, devise=devise, total_consolide=(name in TOTAUX_CONSOLIDES)
            )
            for name, tags in FLOW_TAGS.items()
        }
        flows = {name: {fy: v for fy, (_, v, _) in serie.items()} for name, serie in brut.items()}
        clotures: dict[int, date] = {}
        for serie in brut.values():
            clotures.update({fy: fin for fy, (_, _, fin) in serie.items()})
        depots = {
            (name, fy): depot
            for name in ("eps_diluted", "dividend_per_share")
            for fy, (depot, _, _) in brut.get(name, {}).items()
        }
        instants = {
            name: self._instant_values(facts, tags, devise=devise)
            for name, tags in INSTANT_TAGS.items()
        }

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

            # Capex et charge d'interets sont declares en valeur positive dans
            # les balises XBRL de paiement, mais certains emetteurs signent la
            # sortie de tresorerie. On normalise, comme pour Yahoo.
            ocf = flows["operating_cash_flow"].get(fy)
            capex = flows["capex"].get(fy)
            capex = None if capex is None else abs(capex)
            interet = flows["interest_expense"].get(fy)
            interet = None if interet is None else abs(interet)
            fcf = None if ocf is None or capex is None else ocf - capex

            cash = instants["cash"].get(fy)
            sti = instants["short_term_investments"].get(fy)
            cash_total = None if cash is None else cash + (sti or 0.0)

            records.append(
                AnnualRecord(
                    fiscal_year=fy,
                    period_end=clotures.get(fy),
                    devise=devise,
                    filed={
                        name: depots[(name, fy)]
                        for name in ("eps_diluted", "dividend_per_share")
                        if (name, fy) in depots
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
                        "total_assets": instants["total_assets"].get(fy),
                        "gross_profit": flows["gross_profit"].get(fy),
                        "eps_diluted": flows["eps_diluted"].get(fy),
                        "dividend_per_share": flows["dividend_per_share"].get(fy),
                        "operating_cash_flow": ocf,
                        "capex": capex,
                        "free_cash_flow": fcf,
                        "depreciation_amortisation": da,
                        "interest_expense": interet,
                        "shares_diluted": flows["shares_diluted"].get(fy),
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
