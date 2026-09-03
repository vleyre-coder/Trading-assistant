"""Structures de donnees normalisees, independantes du fournisseur."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# Metriques annuelles normalisees. Toute source (Yahoo, EDGAR, FMP) doit se
# ramener a ce vocabulaire : le reste du code ne connait que ces cles.
ANNUAL_FIELDS = (
    "revenue",
    "net_income",
    "operating_income",
    "ebitda",
    "gross_profit",
    "equity",
    "total_assets",
    "total_debt",
    "cash",
    "current_assets",
    "current_liabilities",
    "eps_diluted",
    "dividend_per_share",
    # Tresorerie. Le resultat comptable se pilote (provisions, etalements,
    # depreciations) ; les encaissements beaucoup moins. Sans ces postes,
    # aucun controle de la qualite des benefices n'est possible.
    "operating_cash_flow",
    "capex",             # toujours stocke en valeur POSITIVE (sortie de tresorerie)
    "free_cash_flow",    # publie si disponible, sinon exploitation - capex
    "depreciation_amortisation",
    "interest_expense",  # toujours stocke en valeur POSITIVE (charge)
    "shares_diluted",    # nombre moyen d'actions diluees : mesure la dilution
)


@dataclass
class AnnualRecord:
    """Un exercice comptable."""

    fiscal_year: int
    period_end: date | None = None
    values: dict[str, float | None] = field(default_factory=dict)
    # Date de depot (ISO) de la valeur retenue, par champ. Necessaire pour
    # savoir si une donnee par action est deja retraitee d'une division
    # d'actions : EDGAR restitue les comparatifs sur la base en vigueur au
    # moment du depot.
    filed: dict[str, str] = field(default_factory=dict)

    def get(self, name: str) -> float | None:
        v = self.values.get(name)
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return None if f != f else f


@dataclass
class Snapshot:
    """Photo instantanee du titre (prix et donnees de marche)."""

    ticker: str
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    currency: str | None = None
    exchange: str | None = None
    price: float | None = None
    market_cap: float | None = None
    shares_outstanding: float | None = None
    trailing_pe: float | None = None
    price_to_book: float | None = None
    dividend_yield: float | None = None  # exprime en fraction (0.025 = 2,5 %)
    trailing_eps: float | None = None
    next_earnings_date: date | None = None
    last_earnings_date: date | None = None
    as_of: datetime | None = None


@dataclass
class Fundamentals:
    """Donnees consolidees d'un titre, tracees par source."""

    ticker: str
    snapshot: Snapshot
    annual: list[AnnualRecord] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)  # champ -> source retenue
    warnings: list[str] = field(default_factory=list)
    region: str | None = None
    # Vrai quand AUCUNE donnee n'a pu etre recuperee : il s'agit alors d'un
    # echec technique (source bridee, ticker inconnu), a distinguer d'un titre
    # dont les fondamentaux sont reellement incomplets.
    fetch_failed: bool = False

    @property
    def years_available(self) -> int:
        """Nombre d'exercices exploitables (chiffre d'affaires renseigne)."""
        return sum(1 for r in self.annual if r.get("revenue") is not None)

    def sorted_annual(self) -> list[AnnualRecord]:
        return sorted(self.annual, key=lambda r: r.fiscal_year)

    def series(self, field_name: str) -> list[tuple[int, float]]:
        """Serie (exercice, valeur) triee, sans les trous."""
        out = []
        for rec in self.sorted_annual():
            v = rec.get(field_name)
            if v is not None:
                out.append((rec.fiscal_year, v))
        return out


@dataclass
class CriterionResult:
    """Un critere calcule : valeur brute, sous-score et tracabilite."""

    key: str
    label: str
    unit: str
    value: float | None
    score: float | None
    weight: float
    pillar: str
    detail: str = ""          # explication lisible (ex. "2020: 143,0 Md -> 2025: 281,7 Md")
    reason_missing: str = ""  # pourquoi le critere est N/A
    # Critere sans signification pour le secteur du titre (« dette nette /
    # EBITDA » pour une banque, par exemple). A distinguer absolument d'une
    # donnee manquante : une lacune doit peser sur la couverture, une
    # non-pertinence non — sinon on penalise une banque pour ne pas etre une
    # entreprise industrielle.
    not_applicable: bool = False

    @property
    def available(self) -> bool:
        return self.score is not None


@dataclass
class PillarResult:
    key: str
    weight: float
    score: float | None
    coverage: float
    criteria: list[CriterionResult] = field(default_factory=list)
    neutralized: bool = False


@dataclass
class StockScore:
    """Resultat complet pour un titre."""

    ticker: str
    name: str | None
    sector: str | None
    region: str | None
    currency: str | None
    price: float | None
    composite: float | None
    # « region » designe la place de COTATION, qui sert a choisir la source de
    # donnees (EDGAR pour les Etats-Unis, Yahoo ailleurs). Elle ne dit rien de
    # l'emetteur lui-meme : ARM et AstraZeneca sont britanniques, ASML
    # neerlandais, tous etiquetes « US » parce qu'ils cotent au Nasdaq.
    #
    # « country » est le SIEGE SOCIAL declare par la source, ce qui est plus
    # informatif que la place de cotation mais ne designe pas le pays
    # d'activite : PDD Holdings est domicilie en Irlande et MercadoLibre en
    # Uruguay, alors que leurs marches sont ailleurs. A lire comme une
    # information de rattachement juridique, jamais comme une mesure
    # d'exposition geographique.
    country: str | None = None
    # Rang au sein du secteur dans l'univers analyse. Le classement general
    # compare des seuils absolus : un distributeur ne peut structurellement
    # pas atteindre la marge d'un editeur de logiciels. Le rang sectoriel
    # repond a l'autre question, « le meilleur de sa categorie ».
    sector_rank: int | None = None
    sector_count: int | None = None
    pillars: dict[str, PillarResult] = field(default_factory=dict)
    window_years: int = 0
    coverage: float = 0.0
    ranked: bool = False           # False = exclu du classement
    exclusion_reason: str = ""
    warnings: list[str] = field(default_factory=list)
    computed_at: datetime | None = None

    def criteria_flat(self) -> list[CriterionResult]:
        out: list[CriterionResult] = []
        for p in self.pillars.values():
            out.extend(p.criteria)
        return out

    def criterion(self, key: str) -> CriterionResult | None:
        for c in self.criteria_flat():
            if c.key == key:
                return c
        return None

    def to_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "ticker": self.ticker,
            "nom": self.name,
            "secteur": self.sector,
            "region": self.region,
            "pays": self.country,
            "rang_secteur": self.sector_rank,
            "titres_du_secteur": self.sector_count,
            "score": None if self.composite is None else round(self.composite, 1),
            "fenetre_ans": self.window_years,
            "couverture": round(self.coverage * 100, 0),
        }
        for key, p in self.pillars.items():
            row[f"pilier_{key}"] = None if p.score is None else round(p.score, 1)
        for c in self.criteria_flat():
            row[f"val_{c.key}"] = c.value
            row[f"score_{c.key}"] = None if c.score is None else round(c.score, 1)
        return row
