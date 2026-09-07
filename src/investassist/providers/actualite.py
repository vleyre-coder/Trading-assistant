"""Lecteur de flux d'actualite — RSS officiels, en lecture seule.

Deux natures d'information, volontairement separees et jamais melangees :

  INSTITUTIONNEL — communiques de la BCE et de la Reserve federale. Ce sont
  des faits publies par ceux qui les decident.

  PRESSE — titres de presse financiere. Ce sont des publications de tiers,
  relayees telles quelles, avec leur source et leur date. Elles ne sont ni
  verifiees, ni reprises, ni approuvees par l'outil, et n'entrent dans aucun
  calcul. Cette separation n'est pas cosmetique : un titre de presse dit
  couramment « signal d'achat » ou « action a saisir », et une telle
  formulation ne doit jamais pouvoir etre lue comme une sortie de cet outil.
  L'interface doit donc les presenter comme une revue de presse attribuee,
  a l'ecart du classement.

Aucun contenu lu ici n'influence un score, un rang ou une alerte : ce module
alimente un affichage, rien d'autre.

Choix technique : analyse RSS avec la bibliotheque standard. Les flux de la
BCE, de la Fed et de Yahoo Finance sont tous du RSS 2.0 — verifie le
3 septembre 2026 — ce qui rend une dependance externe inutile.

Sources ecartees apres verification : l'INSEE renvoie une erreur 500 sur son
flux, la Banque de France refuse les requetes automatisees (403). Elles ne
sont donc pas proposees plutot que d'echouer silencieusement.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from ..config import Settings
from .base import BROWSER_UA, DiskCache, RateLimiter, make_session

log = logging.getLogger(__name__)

# L'actualite se rafraichit vite, mais pas au point de re-interroger a chaque
# affichage : une heure suffit et menage des services gratuits.
CACHE_HEURES = 1

# Longueur maximale conservee pour un titre. Les flux publient parfois des
# resumes entiers dans la balise titre.
TITRE_MAX = 220

FLUX_INSTITUTIONNELS = (
    {
        "cle": "bce",
        "libelle": "Banque centrale européenne",
        "url": "https://www.ecb.europa.eu/rss/press.html",
    },
    {
        "cle": "fed",
        "libelle": "Réserve fédérale américaine",
        "url": "https://www.federalreserve.gov/feeds/press_monetary.xml",
    },
)

FLUX_PRESSE = (
    {
        "cle": "marches",
        "libelle": "Presse financière — marchés",
        "url": (
            "https://feeds.finance.yahoo.com/rss/2.0/headline"
            "?s=%5EGSPC&region=US&lang=en-US"
        ),
    },
)

GABARIT_TITRE = (
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
)


@dataclass
class Article:
    titre: str
    lien: str
    source: str
    publie_le: str = ""
    nature: str = "presse"      # « institutionnel » ou « presse »

    def en_dict(self) -> dict[str, Any]:
        return {
            "titre": self.titre,
            "lien": self.lien,
            "source": self.source,
            "publie_le": self.publie_le,
            "nature": self.nature,
        }


def _date_rss(valeur: str | None) -> str:
    """Normalise une date RSS en ISO. Chaine vide si illisible."""
    if not valeur:
        return ""
    try:
        moment = parsedate_to_datetime(valeur)
    except (TypeError, ValueError):
        try:
            moment = datetime.fromisoformat(valeur.replace("Z", "+00:00"))
        except ValueError:
            return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _texte(element: ET.Element | None) -> str:
    return (element.text or "").strip() if element is not None else ""


class ActualiteClient:
    """Lecteur de flux. Un flux muet est ignore, jamais fatal."""

    def __init__(self, settings: Settings, cache: DiskCache | None = None) -> None:
        self.settings = settings
        self.session = make_session("Investassist personnel")
        # Les flux Yahoo refusent un User-Agent non navigateur.
        self.session.headers.update({"User-Agent": BROWSER_UA})
        self.limiter = RateLimiter(3)
        base = cache or DiskCache(settings.cache_dir, settings.cache_ttl_hours)
        self.cache = DiskCache(base.dir, CACHE_HEURES)

    def _lire(self, url: str, source: str, nature: str, limite: int) -> list[Article]:
        """Articles d'un flux. Le cache conserve les articles, pas le XML."""
        cle = f"{url}|{limite}"
        cached = self.cache.get("actualite", cle)
        if cached is None:
            brut = self._telecharger(url)
            if brut is None:
                return []
            cached = [a.en_dict() for a in self._extraire(brut, url, limite)]
            self.cache.set("actualite", cle, cached)
        return [
            Article(
                titre=entree["titre"],
                lien=entree.get("lien", ""),
                source=source,
                publie_le=entree.get("publie_le", ""),
                nature=nature,
            )
            for entree in cached
        ]

    def _telecharger(self, url: str) -> bytes | None:
        self.limiter.wait()
        try:
            reponse = self.session.get(url, timeout=25)
        except Exception as exc:  # noqa: BLE001
            log.debug("Flux %s indisponible : %s", url, exc)
            return None
        if reponse.status_code != 200 or not reponse.content.strip():
            log.debug("Flux %s : HTTP %s", url, reponse.status_code)
            return None
        # Les OCTETS, et non reponse.text. Sans en-tete « charset », requests
        # decode en ISO-8859-1 : le flux de la Reserve federale commence alors
        # par trois caracteres parasites (son marqueur d'ordre d'octets mal
        # decode) qui font echouer l'analyseur — le flux paraissait vide, sans
        # aucune erreur — et tous les accents seraient de toute facon
        # corrompus. L'analyseur XML, lui, lit la declaration d'encodage du
        # document lui-meme.
        return reponse.content

    @staticmethod
    def _extraire(brut: bytes, url: str, limite: int) -> list[Article]:
        try:
            racine = ET.fromstring(brut)
        except ET.ParseError as exc:
            log.warning("Flux %s illisible (XML invalide) : %s", url, exc)
            return []

        articles: list[Article] = []
        for item in racine.iter("item"):
            titre = _texte(item.find("title"))
            if not titre:
                continue
            if len(titre) > TITRE_MAX:
                titre = titre[: TITRE_MAX - 1].rstrip() + "\u2026"
            articles.append(
                Article(
                    titre=titre,
                    lien=_texte(item.find("link")),
                    source="",
                    publie_le=_date_rss(_texte(item.find("pubDate"))),
                )
            )
            if len(articles) >= limite:
                break
        return articles

    # ------------------------------------------------------------ public
    def institutionnel(self, limite_par_flux: int = 6) -> list[Article]:
        """Communiques des banques centrales, les plus recents d'abord."""
        articles: list[Article] = []
        for flux in FLUX_INSTITUTIONNELS:
            articles.extend(
                self._lire(flux["url"], flux["libelle"], "institutionnel", limite_par_flux)
            )
        return sorted(articles, key=lambda a: a.publie_le, reverse=True)

    def presse(self, limite: int = 12) -> list[Article]:
        """Revue de presse de marche. Contenu de tiers, non verifie."""
        articles: list[Article] = []
        for flux in FLUX_PRESSE:
            articles.extend(self._lire(flux["url"], flux["libelle"], "presse", limite))
        return sorted(articles, key=lambda a: a.publie_le, reverse=True)[:limite]

    def par_titre(self, ticker: str, limite: int = 8) -> list[Article]:
        """Actualite d'un titre precis. Contenu de tiers, non verifie."""
        if not ticker or not ticker.replace(".", "").replace("-", "").isalnum():
            return []
        url = GABARIT_TITRE.format(ticker=ticker.upper())
        return self._lire(url, f"Presse financière — {ticker.upper()}", "presse", limite)
