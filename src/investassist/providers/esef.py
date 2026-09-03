"""Depots europeens ESEF — source officielle, gratuite, sans cle.

Depuis l'exercice 2020, toute societe cotee sur un marche reglemente de
l'Union est tenue de publier son rapport annuel au format electronique unique
europeen (ESEF), c'est-a-dire en XBRL selon la taxonomie IFRS. L'autorite XBRL
en tient un index public : https://filings.xbrl.org/

C'est l'equivalent europeen de SEC EDGAR, et il combles la lacune structurelle
du projet : Yahoo Finance ne remonte que quatre exercices pour les titres
europeens, contre cinq via EDGAR pour les americains. Un taux de croissance
annuel moyen sur quatre ans et un sur cinq ans ne sont pas comparables.

Verifie le 2 septembre 2026 :
  - 25 892 depots indexes, 27 pays, dont 1 178 depots francais ;
  - aucune cle d'API, aucun quota annonce ;
  - un depot livre l'exercice courant ET ses comparatifs, soit TROIS
    exercices par fichier ;
  - les postes principaux portent les balises IFRS standard (chiffre
    d'affaires, resultat, fonds propres, actif, tresorerie d'exploitation,
    resultat brut, resultat par action).

Limites assumees :
  - les societes etendent la taxonomie pour leurs sous-totaux propres. Le
    resultat d'exploitation de LVMH, par exemple, est une balise maison :
    ce poste reste donc pris chez Yahoo. On ne lit ici QUE les balises
    normalisees, jamais les extensions, dont le sens varie d'un emetteur a
    l'autre ;
  - un fichier de faits pese environ 5 Mo (il contient le texte des annexes).
    Le cache est donc de tres longue duree : un exercice publie ne change
    plus jamais.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from ..config import Settings, load_esef_filers
from ..models import AnnualRecord
from .base import DiskCache, RateLimiter, get_json, make_session
from .edgar import fiscal_year_of

log = logging.getLogger(__name__)

INDEX_URL = "https://filings.xbrl.org/api/filings"
BASE_URL = "https://filings.xbrl.org"

# Un exercice publie ne change plus : le cache peut etre quasi permanent.
CACHE_HEURES = 24 * 365

# Cles de dimension communes a tout fait XBRL. Toute AUTRE cle est un axe de
# ventilation (par segment, par composante de capitaux propres...) : le fait
# porte alors une valeur partielle, qu'il ne faut surtout pas confondre avec
# le total consolide.
DIMENSIONS_DE_BASE = frozenset({"concept", "entity", "period", "unit", "language"})

# Chaines de priorite, balises IFRS normalisees uniquement.
DUREE_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": ("Revenue", "RevenueFromContractsWithCustomers"),
    # Le resultat attribuable aux proprietaires de la societe mere correspond
    # a la notion de resultat net utilisee partout ailleurs dans le projet.
    "net_income": ("ProfitLossAttributableToOwnersOfParent", "ProfitLoss"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("ProfitLossFromOperatingActivities",),
    "operating_cash_flow": ("CashFlowsFromUsedInOperatingActivities",),
    "depreciation_amortisation": (
        "DepreciationAndAmortisationExpense",
        "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss",
    ),
    "interest_expense": ("FinanceCosts", "InterestExpense"),
    "eps_diluted": ("DilutedEarningsLossPerShare", "BasicEarningsLossPerShare"),
}

INSTANT_TAGS: dict[str, tuple[str, ...]] = {
    "equity": ("EquityAttributableToOwnersOfParent", "Equity"),
    "total_assets": ("Assets",),
    "current_assets": ("CurrentAssets",),
    "current_liabilities": ("CurrentLiabilities",),
    "cash": ("CashAndCashEquivalents",),
    "long_term_debt": ("LongtermBorrowings", "NoncurrentPortionOfNoncurrentBorrowings"),
    "short_term_debt": (
        "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
        "ShorttermBorrowings",
    ),
}

# Duree acceptee pour un exercice annuel, en jours. Ecarte les trimestres et
# les semestres, et tolere les exercices de 52/53 semaines.
DUREE_MIN, DUREE_MAX = 330, 400


def _iso(valeur: str) -> date | None:
    try:
        return datetime.fromisoformat(valeur).date()
    except ValueError:
        return None


def exercice_d_instant(instant: date) -> int:
    """Exercice auquel rattacher un solde de bilan.

    Les emetteurs datent la cloture soit au dernier jour de l'exercice
    (31 decembre), soit au premier jour du suivant (1er janvier) : les deux
    conventions coexistent dans l'index. Retirer un jour avant de rattacher
    l'exercice les reconcilie sans ambiguite.
    """
    return fiscal_year_of(instant - timedelta(days=1))


class EsefClient:
    """Lecteur de l'index ESEF. Silencieux en cas d'echec : la source est un
    complement, jamais une dependance."""

    def __init__(self, settings: Settings, cache: DiskCache | None = None) -> None:
        self.settings = settings
        self.session = make_session("Investassist personnel")
        self.limiter = RateLimiter(2)
        base = cache or DiskCache(settings.cache_dir, settings.cache_ttl_hours)
        # Cache dedie : la duree de vie courte des cours n'a pas de sens pour
        # un rapport annuel deja publie.
        self.cache = DiskCache(base.dir, CACHE_HEURES)
        self.filers = load_esef_filers()

    # ------------------------------------------------------------- index
    def depots(self, deposant: str) -> list[tuple[date, str]]:
        """Liste (date de cloture, chemin du fichier de faits), plus recent d'abord."""
        cle = f"depots:{deposant}"
        cached = self.cache.get("esef_index", cle)
        if cached is None:
            data = get_json(
                self.session,
                INDEX_URL,
                limiter=self.limiter,
                params={"filter[entity.name]": deposant, "page[size]": 50},
                timeout=45.0,
            )
            if not isinstance(data, dict):
                return []
            cached = [
                {"fin": f["attributes"]["period_end"], "json": f["attributes"]["json_url"]}
                for f in data.get("data") or []
                if f.get("attributes", {}).get("json_url")
                and f["attributes"].get("period_end")
            ]
            self.cache.set("esef_index", cle, cached)
        sorties = []
        for entree in cached:
            fin = _iso(entree["fin"])
            if fin:
                sorties.append((fin, entree["json"]))
        return sorted(sorties, reverse=True)

    # --------------------------------------------------------- faits XBRL
    def _faits(self, chemin: str) -> dict[str, Any] | None:
        cached = self.cache.get("esef_faits", chemin)
        if cached is not None:
            return cached
        data = get_json(
            self.session, f"{BASE_URL}{chemin}", limiter=self.limiter, timeout=180.0
        )
        if not isinstance(data, dict) or "facts" not in data:
            return None
        # On ne conserve que les faits utiles : le fichier brut pese environ
        # 5 Mo, dont l'essentiel est le texte des annexes.
        reduit = self._reduire(data)
        self.cache.set("esef_faits", chemin, reduit)
        return reduit

    @staticmethod
    def _reduire(data: dict[str, Any]) -> dict[str, Any]:
        """Ne garde que les faits consolides portant une balise recherchee."""
        voulus = {
            f"ifrs-full:{tag}"
            for tags in (*DUREE_TAGS.values(), *INSTANT_TAGS.values())
            for tag in tags
        }
        retenus: list[dict[str, Any]] = []
        for fait in (data.get("facts") or {}).values():
            dim = fait.get("dimensions") or {}
            if dim.get("concept") not in voulus:
                continue
            # Presence d'un axe de ventilation : valeur partielle, a ecarter.
            if set(dim) - DIMENSIONS_DE_BASE:
                continue
            retenus.append(
                {"concept": dim["concept"], "period": dim.get("period", ""),
                 "value": fait.get("value")}
            )
        return {"faits": retenus}

    # ------------------------------------------------------------ public
    def annual_records(self, ticker: str, *, avant_exercice: int | None = None) -> tuple[list[AnnualRecord], list[str]]:
        """Exercices lus dans un depot ESEF.

        avant_exercice : exercice le plus ancien deja connu par ailleurs. Le
        depot choisi est celui qui le couvre — il apporte alors deux exercices
        plus anciens ET un exercice commun, qui sert a verifier la concordance
        des deux sources avant de les melanger.
        """
        deposant = self.filers.get(ticker.upper())
        if not deposant:
            return [], []

        depots = self.depots(deposant)
        if not depots:
            return [], [
                f"ESEF : aucun dépôt indexé pour « {deposant} » ({ticker})."
            ]

        choisi = depots[0]
        if avant_exercice is not None:
            couvrants = [d for d in depots if fiscal_year_of(d[0]) <= avant_exercice]
            if couvrants:
                choisi = couvrants[0]

        faits = self._faits(choisi[1])
        if not faits:
            return [], [
                f"ESEF : fichier de faits illisible pour {ticker} "
                f"(exercice {fiscal_year_of(choisi[0])})."
            ]

        par_exercice: dict[int, dict[str, float]] = {}
        for champ, tags in DUREE_TAGS.items():
            for exercice, valeur in self._durees(faits, tags).items():
                par_exercice.setdefault(exercice, {}).setdefault(champ, valeur)
        for champ, tags in INSTANT_TAGS.items():
            for exercice, valeur in self._instants(faits, tags).items():
                par_exercice.setdefault(exercice, {}).setdefault(champ, valeur)

        records: list[AnnualRecord] = []
        for exercice, valeurs in sorted(par_exercice.items()):
            ltd = valeurs.pop("long_term_debt", None)
            std = valeurs.pop("short_term_debt", None)
            total_debt = None if ltd is None and std is None else (ltd or 0.0) + (std or 0.0)
            if total_debt is not None:
                valeurs["total_debt"] = total_debt
            op = valeurs.get("operating_income")
            da = valeurs.get("depreciation_amortisation")
            if op is not None and da is not None:
                valeurs["ebitda"] = op + da
            records.append(AnnualRecord(fiscal_year=exercice, values=dict(valeurs)))

        # Un exercice sans chiffre d'affaires ne sert a rien en aval : la
        # fenetre d'analyse est justement definie sur ce poste.
        records = [r for r in records if r.get("revenue") is not None]
        return records, []

    def _durees(self, faits: dict[str, Any], tags: tuple[str, ...]) -> dict[int, float]:
        """Valeurs annuelles par exercice, premiere balise renseignee gagne."""
        for tag in tags:
            concept = f"ifrs-full:{tag}"
            trouve: dict[int, float] = {}
            for fait in faits["faits"]:
                if fait["concept"] != concept or "/" not in fait["period"]:
                    continue
                debut_txt, fin_txt = fait["period"].split("/", 1)
                debut, fin = _iso(debut_txt), _iso(fin_txt)
                if not debut or not fin:
                    continue
                if not DUREE_MIN <= (fin - debut).days <= DUREE_MAX:
                    continue  # trimestre ou semestre
                valeur = _nombre(fait["value"])
                if valeur is None:
                    continue
                trouve[fiscal_year_of(fin - timedelta(days=1))] = valeur
            if trouve:
                return trouve
        return {}

    def _instants(self, faits: dict[str, Any], tags: tuple[str, ...]) -> dict[int, float]:
        for tag in tags:
            concept = f"ifrs-full:{tag}"
            trouve: dict[int, float] = {}
            for fait in faits["faits"]:
                if fait["concept"] != concept or "/" in fait["period"]:
                    continue
                instant = _iso(fait["period"])
                valeur = _nombre(fait["value"])
                if instant is None or valeur is None:
                    continue
                trouve[exercice_d_instant(instant)] = valeur
            if trouve:
                return trouve
        return {}


def _nombre(valeur: Any) -> float | None:
    try:
        x = float(valeur)
    except (TypeError, ValueError):
        return None
    return None if x != x else x
