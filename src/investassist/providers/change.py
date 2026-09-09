"""Taux de change de reference, source BCE — gratuit, sans cle.

Pourquoi ce module existe : une meme societe peut coter dans une devise et
publier ses comptes dans une autre. ASML depose ses comptes a la SEC en
euro et cote en dollar ; TotalEnergies publie en dollar et cote en euro.
Trois usages, tous mesures sur l'univers CAC 40 + Nasdaq-100 :

1. Ramener une valeur de MARCHE (capitalisation, cours) dans la devise des
   comptes, sans quoi un rendement du free cash flow, un EV/CA ou un P/E
   historique additionne deux monnaies. Mesure sur ASML : mediane du P/E
   historique 38,7 avant, 36,3 apres, et le sous-score du critere passe de
   30,7 a 21,0 — le titre paraissait moins cher que son passe alors qu'il
   l'est moins encore.
2. Convertir un poste comptable venu d'une source complementaire qui compte
   dans une autre devise (voir fundamentals.py) plutot que de le recopier
   tel quel.
3. Comparer les capitalisations sur une base unique, l'euro.

Source : portail de donnees de la BCE, serie EXR (taux de reference
quotidiens, publies vers 16 h CET les jours ouvres). Verifiee par appel
reel le 8 septembre 2026 : USD, CNY, GBP, CHF repondent, sans cle et sans
quota annonce. La serie est cotee EN UNITES DE DEVISE POUR UN EURO, d'ou
le passage systematique par l'euro pour construire un croisement.

Regle de prudence : quand un taux manque, la conversion renvoie None et le
critere concerne se declare indisponible. Un taux devine, ou pris egal a 1,
produirait un chiffre faux presente comme exact — exactement le defaut que
ce module sert a corriger.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime

from ..config import Settings
from .base import DiskCache, RateLimiter, make_session

log = logging.getLogger(__name__)

BCE_DONNEES = "https://data-api.ecb.europa.eu/service/data"

# Les taux de reference sont publies une fois par jour ouvre : six heures de
# cache suffisent et evitent de solliciter un service public gratuit.
CACHE_HEURES = 6

# Premiere annee demandee a la BCE. La fenetre d'analyse est de cinq
# exercices ; on prend large pour couvrir les exercices decales.
PREMIERE_ANNEE = 2015


class ChangeClient:
    """Taux de change de reference de la BCE, avec cache disque.

    Aucune requete n'est emise quand les deux devises sont identiques, ce
    qui est le cas de la quasi-totalite des titres.
    """

    def __init__(self, settings: Settings, cache: DiskCache | None = None) -> None:
        self.settings = settings
        self.session = make_session("Investassist personnel")
        self.limiter = RateLimiter(3)
        base = cache or DiskCache(settings.cache_dir, settings.cache_ttl_hours)
        self.cache = DiskCache(base.dir, CACHE_HEURES)
        self._series: dict[str, list[tuple[str, float]]] = {}

    # ------------------------------------------------------------- serie
    def serie(self, devise: str) -> list[tuple[str, float]]:
        """Serie quotidienne « unites de <devise> pour un euro », triee.

        L'euro n'a pas de serie : il vaut un euro, ce qui evite un appel
        reseau pour la moitie de l'univers.
        """
        devise = (devise or "").upper()
        if not devise:
            return []
        if devise == "EUR":
            return [("1999-01-01", 1.0)]
        if devise in self._series:
            return self._series[devise]

        cle = f"exr:{devise}:{PREMIERE_ANNEE}"
        brut = self.cache.get("change", cle)
        if brut is None:
            self.limiter.wait()
            try:
                reponse = self.session.get(
                    f"{BCE_DONNEES}/EXR/D.{devise}.EUR.SP00.A",
                    params={"startPeriod": f"{PREMIERE_ANNEE}-01-01",
                            "format": "csvdata"},
                    timeout=40,
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("BCE change %s indisponible : %s", devise, exc)
                return []
            if reponse.status_code != 200 or not reponse.text.strip():
                log.debug("BCE change %s : reponse %s vide", devise, reponse.status_code)
                return []
            brut = reponse.text
            self.cache.set("change", cle, brut)

        points: list[tuple[str, float]] = []
        for ligne in csv.DictReader(io.StringIO(brut)):
            periode, valeur = ligne.get("TIME_PERIOD"), ligne.get("OBS_VALUE")
            if not periode or valeur in (None, ""):
                continue
            try:
                nombre = float(valeur)
            except ValueError:
                continue
            if nombre > 0:
                points.append((periode, nombre))
        points.sort()
        self._series[devise] = points
        return points

    # ------------------------------------------------------------ facteur
    def _pour_un_euro(self, devise: str, le: date | None) -> float | None:
        """Unites de <devise> pour un euro, a la date demandee ou avant.

        « Ou avant » et non « la plus proche » : les taux de reference ne
        sont pas publies les jours de fermeture, et un exercice clos un
        31 decembre tombe presque toujours un jour sans publication.
        """
        points = self.serie(devise)
        if not points:
            return None
        if le is None:
            return points[-1][1]
        borne = le.isoformat()
        retenu = None
        for periode, valeur in points:
            if periode <= borne:
                retenu = valeur
            else:
                break
        # Date anterieure au debut de la serie : on ne remonte pas plus loin
        # plutot que d'appliquer un taux hors periode.
        return retenu

    def facteur(self, de: str, vers: str, le: date | None = None) -> float | None:
        """Multiplicateur pour passer d'un montant en <de> a un montant en <vers>.

        Renvoie None si l'un des deux taux manque : voir la regle de
        prudence en tete de module.
        """
        de, vers = (de or "").upper(), (vers or "").upper()
        if not de or not vers:
            return None
        if de == vers:
            return 1.0
        depart = self._pour_un_euro(de, le)
        arrivee = self._pour_un_euro(vers, le)
        if not depart or not arrivee:
            return None
        return arrivee / depart

    def convertir(
        self, montant: float | None, de: str, vers: str, le: date | None = None
    ) -> float | None:
        if montant is None:
            return None
        f = self.facteur(de, vers, le)
        return None if f is None else montant * f
