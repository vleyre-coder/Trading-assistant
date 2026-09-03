"""Consolidation des sources en un jeu de fondamentaux normalise.

Regle de priorite :
  - Titres US deposant aupres de la SEC  -> EDGAR (officiel) en premier,
    Yahoo en complement des champs manquants (typiquement l'EBITDA).
  - Autres titres (Europe)               -> Yahoo uniquement.
Chaque champ retenu est trace dans Fundamentals.sources.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from .config import Settings
from .models import ANNUAL_FIELDS, AnnualRecord, Fundamentals, Snapshot
from .providers.base import DiskCache
from .providers.edgar import EdgarClient
from .providers.esef import EsefClient
from .providers.yahoo import YahooClient

log = logging.getLogger(__name__)

# Suffixes Yahoo des places europeennes (un ticker sans suffixe est US).
EU_SUFFIXES = {
    ".PA", ".AS", ".BR", ".LS", ".DE", ".F", ".MI", ".MC", ".SW", ".VI",
    ".ST", ".OL", ".CO", ".HE", ".L", ".IR", ".WA", ".PR", ".AT",
}


# Champs exprimes PAR ACTION : sensibles aux divisions d'actions.
PER_SHARE_FIELDS = ("eps_diluted", "dividend_per_share")


def split_factor_after(reference: date | None, splits: dict[str, float]) -> float:
    """Produit des divisions d'actions survenues APRES une date de reference.

    La reference est la date de DEPOT de la donnee, non la cloture de
    l'exercice : une donnee par action reflete la base d'actions en vigueur
    au moment ou elle est publiee. EDGAR retraite lui-meme les comparatifs
    dans les depots posterieurs a une division — un BPA de 3,85 USD depose
    avant une division 10 pour 1 doit etre ramene a 0,385 USD, mais le meme
    exercice republie apres la division vaut deja 0,385 et ne doit surtout
    pas etre divise une seconde fois.
    """
    if reference is None or not splits:
        return 1.0
    factor = 1.0
    for iso, ratio in splits.items():
        try:
            split_date = datetime.strptime(iso, "%Y-%m-%d").date()
        except ValueError:
            continue
        if split_date > reference:
            factor *= float(ratio)
    return factor or 1.0


def region_of(ticker: str, snapshot: Snapshot | None) -> str:
    for suffix in EU_SUFFIXES:
        if ticker.upper().endswith(suffix):
            return "EU"
    if snapshot and snapshot.country and snapshot.country not in ("United States",):
        return "EU" if snapshot.exchange not in ("NMS", "NYQ", "NGM", "PCX", "ASE") else "US"
    return "US"


class FundamentalsService:
    def __init__(self, settings: Settings, cache: DiskCache | None = None) -> None:
        self.settings = settings
        self.cache = cache or DiskCache(settings.cache_dir, settings.cache_ttl_hours)
        self.yahoo = YahooClient(settings, self.cache)
        self.edgar = EdgarClient(settings, self.cache)
        self.esef = EsefClient(settings, self.cache)

    def load(self, ticker: str, *, target_years: int = 5, use_cache: bool = True) -> Fundamentals:
        warnings: list[str] = []
        sources: dict[str, str] = {}

        snapshot = self.yahoo.snapshot(ticker, use_cache=use_cache)
        if snapshot is None:
            snapshot = Snapshot(ticker=ticker)
            warnings.append(
                f"Yahoo : aucune donnee de marche pour {ticker} (ticker inconnu ou "
                "service indisponible)."
            )
        else:
            sources["snapshot"] = "yahoo"

        region = region_of(ticker, snapshot)

        edgar_records: list[AnnualRecord] = []
        if region == "US":
            edgar_records, edgar_warnings = self.edgar.annual_records(ticker)
            # L'absence d'un titre dans EDGAR n'est pas une anomalie a signaler
            # a l'utilisateur pour un titre europeen : c'est attendu.
            warnings.extend(w for w in edgar_warnings if "absent du registre SEC" not in w)

        yahoo_records, yahoo_warnings = self.yahoo.annual_records(ticker, use_cache=use_cache)

        if edgar_records:
            base, complement = edgar_records, yahoo_records
            primary, secondary = "edgar", "yahoo"
        else:
            base, complement = yahoo_records, []
            primary, secondary = "yahoo", ""
            warnings.extend(yahoo_warnings)

        if not base:
            warnings.append(f"Aucun historique annuel exploitable pour {ticker}.")
            # Ni cours ni comptes : la source n'a rien renvoye. On le signale
            # comme un echec technique plutot que comme une lacune des
            # fondamentaux du titre, qui serait un diagnostic trompeur.
            return Fundamentals(
                ticker=ticker, snapshot=snapshot, annual=[], sources=sources,
                warnings=warnings, region=region,
                fetch_failed=snapshot.price is None,
            )

        by_year: dict[int, AnnualRecord] = {r.fiscal_year: r for r in base}
        for field in ANNUAL_FIELDS:
            if any(r.get(field) is not None for r in base):
                sources[field] = primary

        # Completion champ par champ, exercice par exercice.
        filled: set[str] = set()
        for rec in complement:
            target = by_year.get(rec.fiscal_year)
            if target is None:
                continue
            for field in ANNUAL_FIELDS:
                if target.get(field) is None and rec.get(field) is not None:
                    target.values[field] = rec.values[field]
                    filled.add(field)
        for field in filled:
            sources[field] = f"{sources.get(field, primary)}+{secondary}" if secondary else primary

        # --- Retraitement des divisions d'actions (donnees EDGAR) --------
        if primary == "edgar":
            splits = self.yahoo.splits(ticker, use_cache=use_cache)
            if splits:
                adjusted = False
                for rec in by_year.values():
                    for field in PER_SHARE_FIELDS:
                        value = rec.get(field)
                        if value is None:
                            continue
                        filed_iso = rec.filed.get(field)
                        reference = (
                            date.fromisoformat(filed_iso) if filed_iso else rec.period_end
                        )
                        factor = split_factor_after(reference, splits)
                        if factor != 1.0:
                            rec.values[field] = value / factor
                            adjusted = True
                if not adjusted:
                    splits = {}
            if splits:
                sources["eps_diluted"] = f"{sources.get('eps_diluted', 'edgar')} (retraité des splits)"
                warnings.append(
                    "Données par action retraitées des divisions d'actions "
                    f"({', '.join(sorted(splits))})."
                )

            # Les dividendes de Yahoo sont deja exprimes sur la base actuelle
            # et couvrent les versements effectifs : ils sont plus fiables ici
            # que le dividende declare des annexes XBRL (qui inclut parfois des
            # dividendes exceptionnels sur un exercice decale).
            yahoo_dividends = self.yahoo.dividends_by_year(ticker, use_cache=use_cache)
            if yahoo_dividends:
                for fy, rec in by_year.items():
                    if fy in yahoo_dividends:
                        rec.values["dividend_per_share"] = yahoo_dividends[fy]
                sources["dividend_per_share"] = "yahoo (ajuste des splits)"

        # --- Allongement de l'historique europeen par les depots ESEF ----
        if region == "EU" and self.settings.esef_enabled and by_year:
            avertissement = self._completer_par_esef(ticker, by_year, target_years, sources)
            if avertissement:
                warnings.append(avertissement)

        # Fenetre : les N derniers exercices disponibles.
        records = sorted(by_year.values(), key=lambda r: r.fiscal_year)
        usable = [r for r in records if r.get("revenue") is not None]
        window = usable[-target_years:] if usable else []

        if window and len(window) < target_years:
            warnings.append(
                f"Fenêtre réduite a {len(window)} exercices ({window[0].fiscal_year}-"
                f"{window[-1].fiscal_year}) : historique gratuit limite pour ce titre."
            )

        return Fundamentals(
            ticker=ticker,
            snapshot=snapshot,
            annual=window,
            sources=sources,
            warnings=warnings,
            region=region,
        )

    # Ecart tolere entre deux sources sur un meme exercice. Au-dela, les deux
    # series ne decrivent pas le meme perimetre de consolidation et les
    # melanger fabriquerait un taux de croissance faux.
    ECART_MAX_CONCORDANCE = 0.05

    def _completer_par_esef(
        self,
        ticker: str,
        by_year: dict[int, AnnualRecord],
        target_years: int,
        sources: dict[str, str],
    ) -> str:
        """Ajoute les exercices anterieurs lus dans le depot ESEF officiel.

        Yahoo ne remonte que quatre exercices pour l'Europe. Le depot ESEF en
        contient trois d'un coup, comparatifs inclus : de quoi atteindre la
        fenetre visee avec des chiffres officiels.

        Regle de prudence : Yahoo reste maitre sur les exercices qu'il couvre
        deja, et l'exercice commun aux deux sources sert de controle. Une
        divergence de plus de 5 % sur le chiffre d'affaires signale deux
        perimetres de consolidation differents ; on renonce alors a completer
        plutot que de fabriquer une serie incoherente.
        """
        connus = sorted(y for y, r in by_year.items() if r.get("revenue") is not None)
        if not connus or len(connus) >= target_years:
            return ""

        try:
            recs, _ = self.esef.annual_records(ticker, avant_exercice=connus[0])
        except Exception as exc:  # noqa: BLE001
            log.debug("ESEF indisponible pour %s : %s", ticker, exc)
            return ""
        if not recs:
            return ""

        # Controle de concordance sur les exercices communs.
        for rec in recs:
            reference = by_year.get(rec.fiscal_year)
            if reference is None:
                continue
            a, b = reference.get("revenue"), rec.get("revenue")
            if a and b and abs(a - b) / max(abs(a), abs(b)) > self.ECART_MAX_CONCORDANCE:
                log.warning(
                    "ESEF ecarte pour %s : chiffre d'affaires %s divergent "
                    "(Yahoo %.4g / ESEF %.4g).", ticker, rec.fiscal_year, a, b,
                )
                return (
                    f"Historique ESEF écarté : le chiffre d'affaires {rec.fiscal_year} "
                    "diverge de plus de 5 % entre les deux sources (périmètres de "
                    "consolidation différents)."
                )

        # Un exercice est compte comme gagne s'il devient exploitable, c'est-a-
        # dire s'il porte un chiffre d'affaires qu'il n'avait pas. Yahoo
        # renvoie en effet des colonnes entierement vides pour les exercices
        # les plus anciens : l'exercice existe deja dans le dictionnaire mais
        # ne sert a rien tant qu'il n'est pas rempli.
        gagnes: list[int] = []
        for rec in recs:
            cible = by_year.get(rec.fiscal_year)
            if cible is None:
                by_year[rec.fiscal_year] = rec
                if rec.get("revenue") is not None:
                    gagnes.append(rec.fiscal_year)
                continue
            exploitable_avant = cible.get("revenue") is not None
            for champ, valeur in rec.values.items():
                if valeur is not None and cible.get(champ) is None:
                    cible.values[champ] = valeur
            if not exploitable_avant and cible.get("revenue") is not None:
                gagnes.append(rec.fiscal_year)

        if not gagnes:
            return ""
        sources["historique_ancien"] = "esef (dépôt officiel)"
        return (
            f"Historique complété par le dépôt ESEF officiel : exercice(s) "
            f"{', '.join(str(a) for a in sorted(gagnes))}."
        )

    def price_history(self, ticker: str, *, period: str = "5y", use_cache: bool = True):
        return self.yahoo.price_history(ticker, period=period, use_cache=use_cache)
