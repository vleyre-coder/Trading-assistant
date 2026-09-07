"""Contexte macroeconomique — sources publiques officielles, sans cle.

Ce module ne produit AUCUN signal d'investissement et ne recommande rien.
Il affiche des indicateurs publies par les institutions qui les produisent,
avec leur periode de reference, leur date de publication et un lien vers la
source. Rien de plus : l'outil ne sait pas, et ne pretend pas savoir, ce
qu'un taux directeur implique pour un titre donne.

Sources verifiees par appel reel le 3 septembre 2026 :
  - BCE, portail de donnees (data-api.ecb.europa.eu) : taux directeur et
    courbe des taux souverains de la zone euro ;
  - Eurostat (ec.europa.eu/eurostat/api) : inflation, croissance du PIB,
    taux de chomage ;
  - Tresor americain (api.fiscaldata.treasury.gov) : taux moyens de la dette.
Aucune ne demande de cle ni n'annonce de quota.

Deux precautions structurelles :

1. La composition de la zone euro change, et son code change avec elle.
   L'inflation utilise « EA », la croissance « EA20 », le chomage « EA21 » —
   et ces codes se decalent au fil des elargissements. Chaque serie essaie
   donc une CHAINE de codes : coder « EA20 » en dur revient a programmer une
   panne silencieuse a la prochaine adhesion.

2. Un indicateur publie avec retard n'est pas une erreur, mais le lire comme
   s'il etait courant en est une. Chaque valeur porte donc sa periode de
   reference et sa date de publication, et se declare perimee au-dela d'un
   delai propre a sa frequence.
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Sequence

from ..config import Settings
from .base import DiskCache, RateLimiter, get_json, make_session

log = logging.getLogger(__name__)

BCE_DONNEES = "https://data-api.ecb.europa.eu/service/data"
EUROSTAT = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
TRESOR_US = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
    "/v2/accounting/od/avg_interest_rates"
)

# Les indicateurs macro bougent lentement : six heures de cache evitent de
# solliciter inutilement des services publics gratuits.
CACHE_HEURES = 6

# Codes de la zone euro, essayes dans cet ordre. Voir la note 1 de l'en-tete.
CODES_ZONE_EURO = ("EA21", "EA20", "EA19", "EA")


@dataclass
class Indicateur:
    """Une mesure publiee, avec tout ce qu'il faut pour la lire correctement."""

    cle: str
    label: str
    valeur: float | None
    unite: str
    periode: str = ""          # periode de reference (2026-07, 2026-Q2...)
    publie_le: str = ""        # date de publication par l'institution
    source: str = ""
    url: str = ""
    precedent: float | None = None
    commentaire: str = ""
    # Delai au-dela duquel la valeur ne doit plus etre presentee comme
    # courante, en jours. Depend de la frequence de publication.
    fraicheur_jours: int = 60

    @property
    def variation(self) -> float | None:
        if self.valeur is None or self.precedent is None:
            return None
        return self.valeur - self.precedent

    @property
    def perime(self) -> bool:
        """La derniere publication est-elle trop ancienne pour etre lue
        comme l'etat actuel ?"""
        jour = _date_iso(self.publie_le)
        if jour is None:
            return False
        return (date.today() - jour).days > self.fraicheur_jours

    def en_dict(self) -> dict[str, Any]:
        return {
            "cle": self.cle,
            "label": self.label,
            "valeur": self.valeur,
            "unite": self.unite,
            "periode": self.periode,
            "publie_le": self.publie_le,
            "source": self.source,
            "url": self.url,
            "precedent": self.precedent,
            "variation": self.variation,
            "commentaire": self.commentaire,
            "perime": self.perime,
        }


def _date_iso(valeur: str) -> date | None:
    if not valeur:
        return None
    try:
        return datetime.fromisoformat(valeur.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(valeur[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


class MacroClient:
    """Lecteur d'indicateurs publics. Une source muette ne fait jamais
    echouer l'ensemble : l'indicateur concerne est simplement absent."""

    def __init__(self, settings: Settings, cache: DiskCache | None = None) -> None:
        self.settings = settings
        self.session = make_session("Investassist personnel")
        self.limiter = RateLimiter(3)
        base = cache or DiskCache(settings.cache_dir, settings.cache_ttl_hours)
        self.cache = DiskCache(base.dir, CACHE_HEURES)

    # ------------------------------------------------------------ BCE
    def _serie_bce(self, chemin: str, observations: int = 2) -> list[tuple[str, float]]:
        """Derniere(s) observation(s) d'une serie du portail de la BCE."""
        cle = f"bce:{chemin}:{observations}"
        cached = self.cache.get("macro", cle)
        if cached is None:
            self.limiter.wait()
            try:
                reponse = self.session.get(
                    f"{BCE_DONNEES}/{chemin}",
                    params={"lastNObservations": observations, "format": "csvdata"},
                    timeout=40,
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("BCE %s indisponible : %s", chemin, exc)
                return []
            if reponse.status_code != 200 or not reponse.text.strip():
                return []
            cached = reponse.text
            self.cache.set("macro", cle, cached)

        sorties: list[tuple[str, float]] = []
        for ligne in csv.DictReader(io.StringIO(cached)):
            periode, brut = ligne.get("TIME_PERIOD"), ligne.get("OBS_VALUE")
            if not periode or brut in (None, ""):
                continue
            try:
                sorties.append((periode, float(brut)))
            except ValueError:
                continue
        return sorties

    def taux_directeur(self) -> Indicateur:
        # La serie est quotidienne mais ne change qu'aux decisions du Conseil :
        # on demande plusieurs points pour retrouver le niveau precedent.
        serie = self._serie_bce("FM/D.U2.EUR.4F.KR.MRR_FR.LEV", observations=400)
        niveaux = [v for _, v in serie]
        precedent = next((v for v in reversed(niveaux[:-1]) if v != niveaux[-1]), None) if niveaux else None
        return Indicateur(
            cle="taux_directeur_bce",
            label="Taux directeur BCE",
            valeur=serie[-1][1] if serie else None,
            unite="percent",
            periode=serie[-1][0] if serie else "",
            publie_le=serie[-1][0] if serie else "",
            source="Banque centrale européenne",
            url="https://www.ecb.europa.eu/stats/policy_and_exchange_rates/key_ecb_interest_rates/html/index.en.html",
            precedent=precedent,
            commentaire="Taux des opérations principales de refinancement.",
            fraicheur_jours=15,
        )

    def taux_10_ans_zone_euro(self) -> Indicateur:
        serie = self._serie_bce("YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y", observations=30)
        return Indicateur(
            cle="taux_10_ans_zone_euro",
            label="Emprunt d'État 10 ans, zone euro",
            valeur=serie[-1][1] if serie else None,
            unite="percent",
            periode=serie[-1][0] if serie else "",
            publie_le=serie[-1][0] if serie else "",
            source="Banque centrale européenne",
            url="https://www.ecb.europa.eu/stats/financial_markets_and_interest_rates/euro_area_yield_curves/html/index.en.html",
            precedent=serie[-2][1] if len(serie) > 1 else None,
            commentaire="Courbe des taux souverains notés AAA.",
            fraicheur_jours=10,
        )

    # ------------------------------------------------------- Eurostat
    def _serie_eurostat(
        self,
        jeu: str,
        *,
        geos: Sequence[str] = CODES_ZONE_EURO,
        periodes: int = 3,
        **filtres: str,
    ) -> tuple[list[tuple[str, float]], str, str]:
        """Serie Eurostat, en essayant plusieurs codes geographiques.

        Renvoie (observations, code geo retenu, date de publication). La
        chaine de codes existe parce que la composition de la zone euro
        change : « EA20 » deviendra faux, comme « EA19 » l'est devenu.
        """
        for geo in geos:
            cle = f"eurostat:{jeu}:{geo}:{periodes}:{sorted(filtres.items())}"
            cached = self.cache.get("macro", cle)
            if cached is None:
                params = {"format": "JSON", "geo": geo, "lastTimePeriod": periodes}
                params.update(filtres)
                data = get_json(
                    self.session, f"{EUROSTAT}/{jeu}",
                    limiter=self.limiter, params=params, timeout=45.0,
                )
                if not isinstance(data, dict) or "value" not in data:
                    continue
                cached = data
                self.cache.set("macro", cle, cached)

            valeurs = cached.get("value") or {}
            if not valeurs:
                continue  # code geo non reconnu par ce jeu de donnees
            index = (
                cached.get("dimension", {}).get("time", {})
                .get("category", {}).get("index", {})
            )
            inverse = {rang: periode for periode, rang in index.items()}
            observations = sorted(
                (
                    (inverse.get(int(rang), ""), float(valeur))
                    for rang, valeur in valeurs.items()
                    if valeur is not None
                ),
                key=lambda couple: couple[0],
            )
            if observations:
                return observations, geo, str(cached.get("updated") or "")
        return [], "", ""

    def inflation_zone_euro(self) -> Indicateur:
        obs, geo, publie = self._serie_eurostat(
            "prc_hicp_manr", coicop="CP00", geos=("EA", "EA20", "EA21")
        )
        return Indicateur(
            cle="inflation_zone_euro",
            label="Inflation, zone euro",
            valeur=obs[-1][1] if obs else None,
            unite="percent",
            periode=obs[-1][0] if obs else "",
            publie_le=publie,
            source=f"Eurostat ({geo})" if geo else "Eurostat",
            url="https://ec.europa.eu/eurostat/databrowser/view/prc_hicp_manr/default/table",
            precedent=obs[-2][1] if len(obs) > 1 else None,
            commentaire="Indice des prix à la consommation harmonisé, glissement annuel.",
            fraicheur_jours=60,
        )

    def croissance_zone_euro(self) -> Indicateur:
        obs, geo, publie = self._serie_eurostat(
            "namq_10_gdp", na_item="B1GQ", unit="CLV_PCH_PRE", s_adj="SCA",
            geos=("EA20", "EA21", "EA19"),
        )
        return Indicateur(
            cle="croissance_zone_euro",
            label="Croissance du PIB, zone euro",
            valeur=obs[-1][1] if obs else None,
            unite="percent",
            periode=obs[-1][0] if obs else "",
            publie_le=publie,
            source=f"Eurostat ({geo})" if geo else "Eurostat",
            url="https://ec.europa.eu/eurostat/databrowser/view/namq_10_gdp/default/table",
            precedent=obs[-2][1] if len(obs) > 1 else None,
            commentaire="En volume, d'un trimestre au suivant, corrigé des variations saisonnières.",
            fraicheur_jours=120,
        )

    def chomage_zone_euro(self) -> Indicateur:
        obs, geo, publie = self._serie_eurostat(
            "une_rt_m", s_adj="SA", unit="PC_ACT", sex="T", age="TOTAL",
            geos=("EA21", "EA20", "EA19"),
        )
        return Indicateur(
            cle="chomage_zone_euro",
            label="Taux de chômage, zone euro",
            valeur=obs[-1][1] if obs else None,
            unite="percent",
            periode=obs[-1][0] if obs else "",
            publie_le=publie,
            source=f"Eurostat ({geo})" if geo else "Eurostat",
            url="https://ec.europa.eu/eurostat/databrowser/view/une_rt_m/default/table",
            precedent=obs[-2][1] if len(obs) > 1 else None,
            commentaire="Part de la population active sans emploi.",
            fraicheur_jours=60,
        )

    # --------------------------------------------------- Tresor americain
    def taux_americains(self) -> Indicateur:
        """Taux moyen des obligations du Tresor americain (« Treasury Notes »).

        Le Tresor publie le cout moyen de sa dette existante, non un taux de
        marche du jour : c'est un reperage de niveau, pas une cotation.
        """
        cle = "tresor_us:notes"
        cached = self.cache.get("macro", cle)
        if cached is None:
            data = get_json(
                self.session, TRESOR_US, limiter=self.limiter,
                params={
                    "sort": "-record_date",
                    "page[size]": 60,
                    "fields": "record_date,security_desc,avg_interest_rate_amt",
                },
                timeout=40.0,
            )
            if not isinstance(data, dict):
                return Indicateur(
                    cle="taux_us", label="Taux moyen du Trésor américain",
                    valeur=None, unite="percent", source="Trésor américain",
                )
            cached = data
            self.cache.set("macro", cle, cached)

        lignes = [
            l for l in (cached.get("data") or [])
            if l.get("security_desc") == "Treasury Notes"
        ]
        lignes.sort(key=lambda l: l.get("record_date", ""))
        valeurs = []
        for ligne in lignes:
            try:
                valeurs.append((ligne["record_date"], float(ligne["avg_interest_rate_amt"])))
            except (KeyError, TypeError, ValueError):
                continue
        return Indicateur(
            cle="taux_us",
            label="Trésor américain, taux moyen",
            valeur=valeurs[-1][1] if valeurs else None,
            unite="percent",
            periode=valeurs[-1][0] if valeurs else "",
            publie_le=valeurs[-1][0] if valeurs else "",
            source="Trésor américain",
            url="https://fiscaldata.treasury.gov/datasets/average-interest-rates-treasury-securities/",
            precedent=valeurs[-2][1] if len(valeurs) > 1 else None,
            commentaire="Coût moyen de la dette en circulation, non un taux de marché du jour.",
            fraicheur_jours=60,
        )

    # ------------------------------------------------------------ public
    def indicateurs(self) -> list[Indicateur]:
        """Tous les indicateurs disponibles. Une source muette est omise."""
        lecteurs = (
            self.taux_directeur,
            self.inflation_zone_euro,
            self.croissance_zone_euro,
            self.chomage_zone_euro,
            self.taux_10_ans_zone_euro,
            self.taux_americains,
        )
        sorties: list[Indicateur] = []
        for lecteur in lecteurs:
            try:
                indicateur = lecteur()
            except Exception as exc:  # noqa: BLE001
                log.warning("Indicateur %s indisponible : %s", lecteur.__name__, exc)
                continue
            if indicateur.valeur is not None:
                sorties.append(indicateur)
        return sorties
