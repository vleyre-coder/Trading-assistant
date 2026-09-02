"""Serveur local de l'application de bureau.

L'application n'est pas un site : c'est un petit serveur qui tourne sur votre
machine, ecoute UNIQUEMENT sur 127.0.0.1 et sert l'interface web deja utilisee
pour la version en ligne. Rien ne sort de l'ordinateur en dehors des appels aux
sources de donnees financieres.

Le meme fichier d'interface fonctionne donc dans deux modes :
  - hors ligne, en lisant des fichiers JSON figes ;
  - ici, ou les memes URL sont servies depuis la base locale et ou des
    fonctions supplementaires (relancer l'analyse, watchlist, alertes)
    deviennent disponibles.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import secrets
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import criteria as crit
from . import export, scoring
from .alerts import Notifier, evaluate_rules
from .alerts.rules import attach_earnings_dates
from .chemins import dossier_donnees, dossier_site, resume as resume_chemins
from .config import ScoringConfig, Settings, load_universes
from .fundamentals import FundamentalsService
from .screener import Screener, tickers_for
from .storage import ALERT_KINDS, Database, score_from_row

log = logging.getLogger(__name__)

VERSION_API = 1
NOM_COOKIE = "investassist_jeton"


class Application:
    """Etat partage du serveur : services, base et analyse en cours."""

    def __init__(self, settings: Settings, cfg: ScoringConfig) -> None:
        self.settings = settings
        self.cfg = cfg
        self.donnees = dossier_donnees()
        self.donnees.mkdir(parents=True, exist_ok=True)
        self.db = Database(settings.database_path)
        self.service = FundamentalsService(settings)
        self.screener = Screener(settings, cfg, service=self.service, database=self.db)
        self.jeton = secrets.token_urlsafe(24)

        self._verrou = threading.Lock()
        self.analyse: dict[str, Any] = {
            "en_cours": False,
            "fait": 0,
            "total": 0,
            "ticker": "",
            "demarree_a": None,
            "terminee_a": None,
            "erreur": "",
            "resume": None,
        }
        self._amorcer_donnees()

    # ------------------------------------------------------------ fichiers
    def fichier_donnees(self, nom: str) -> Path:
        return self.donnees / nom

    def _amorcer_donnees(self) -> None:
        """Copie l'instantane livre avec l'application au premier lancement.

        L'utilisateur voit ainsi un classement des l'ouverture, clairement
        date, plutot qu'un ecran vide suivi de huit minutes d'attente.
        """
        for nom in ("ranking.json", "history.json"):
            cible = self.fichier_donnees(nom)
            if cible.exists():
                continue
            source = dossier_site() / "data" / nom
            if source.exists():
                cible.write_bytes(source.read_bytes())
                log.info("Instantané initial copié : %s", nom)

    # ------------------------------------------------------------- analyse
    def lancer_analyse(self, univers: list[str], utiliser_cache: bool) -> bool:
        """Demarre une analyse en tache de fond. Faux si une autre tourne."""
        with self._verrou:
            if self.analyse["en_cours"]:
                return False
            self.analyse.update(
                en_cours=True, fait=0, total=0, ticker="",
                demarree_a=datetime.now().isoformat(timespec="seconds"),
                terminee_a=None, erreur="", resume=None,
            )
        fil = threading.Thread(
            target=self._executer_analyse, args=(univers, utiliser_cache), daemon=True
        )
        fil.start()
        return True

    def _executer_analyse(self, univers: list[str], utiliser_cache: bool) -> None:
        depart = time.time()
        try:
            def progression(fait: int, total: int, ticker: str) -> None:
                with self._verrou:
                    self.analyse.update(fait=fait, total=total, ticker=ticker)

            ancien_historique = export.read_json(self.fichier_donnees("history.json"))
            precedent, rangs_precedents = export.previous_state_from_history(ancien_historique)

            resultat = self.screener.run(
                univers, use_cache=utiliser_cache, persist=True, progress=progression
            )
            genere_le = datetime.now()

            export.write_json(
                self.fichier_donnees("ranking.json"),
                export.ranking_payload(
                    resultat.ranked, resultat.excluded, resultat.failures, self.cfg,
                    universes=univers, generated_at=genere_le,
                    duration_seconds=time.time() - depart,
                ),
            )
            export.write_json(
                self.fichier_donnees("history.json"),
                export.append_history(
                    ancien_historique, resultat.ranked, resultat.excluded,
                    generated_at=genere_le,
                ),
            )

            # Alertes : seulement s'il existe un point de comparaison.
            evenements = []
            if precedent:
                attach_earnings_dates(resultat.scores, resultat.last_earnings)
                evenements = evaluate_rules(
                    self.db, resultat.scores, self.cfg,
                    previous=precedent, ranks=resultat.ranks,
                    previous_ranks=rangs_precedents,
                )
                if evenements:
                    Notifier(self.settings).dispatch(evenements)

            with self._verrou:
                self.analyse.update(
                    resume={
                        "classes": len(resultat.ranked),
                        "exclus": len(resultat.excluded),
                        "echecs": len(resultat.failures),
                        "duree_secondes": round(time.time() - depart, 1),
                        "alertes": len(evenements),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            log.exception("Analyse interrompue")
            with self._verrou:
                self.analyse.update(erreur=f"{type(exc).__name__}: {exc}")
        finally:
            with self._verrou:
                self.analyse.update(
                    en_cours=False, terminee_a=datetime.now().isoformat(timespec="seconds")
                )

    def etat_analyse(self) -> dict[str, Any]:
        with self._verrou:
            return dict(self.analyse)

    # -------------------------------------------------------------- titres
    def detail_titre(self, ticker: str) -> dict[str, Any] | None:
        """Fiche complete d'un titre, recalculee a la demande.

        Utile pour un titre absent du dernier classement (ajout recent a la
        watchlist) : l'utilisateur n'a pas a relancer tout l'univers.
        """
        classement = export.read_json(self.fichier_donnees("ranking.json")) or {}
        for entree in list(classement.get("ranked") or []) + list(classement.get("excluded") or []):
            if entree.get("ticker", "").upper() == ticker.upper():
                return entree

        fundamentals = self.service.load(ticker, target_years=self.cfg.target_years)
        prices = self.service.price_history(ticker)
        note = scoring.score_stock(
            fundamentals, self.cfg, prices=prices,
            sector_medians={}, raw_values=crit.compute_all(fundamentals, prices),
        )
        return export.score_payload(note)


def construire_gestionnaire(app: Application):
    """Fabrique la classe de gestion des requetes liee a cette application."""

    class Gestionnaire(BaseHTTPRequestHandler):
        server_version = "Investassist"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        # ------------------------------------------------------- utilitaires
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            log.debug("%s - %s", self.address_string(), format % args)

        def _repondre(self, code: int, corps: bytes, type_mime: str,
                      poser_cookie: bool = False) -> None:
            self.send_response(code)
            self.send_header("Content-Type", type_mime)
            self.send_header("Content-Length", str(len(corps)))
            if poser_cookie:
                # Le jeton devient un cookie de session : rouvrir l'adresse
                # sans le jeton continue de fonctionner, et le cookie meurt
                # avec le navigateur.
                self.send_header(
                    "Set-Cookie",
                    f"{NOM_COOKIE}={app.jeton}; Path=/; SameSite=Strict",
                )
            # L'application est locale : rien ne doit etre mis en cache par le
            # navigateur, sans quoi un classement fraichement calcule
            # n'apparaitrait pas.
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(corps)

        def _json(self, charge: Any, code: int = 200) -> None:
            self._repondre(
                code,
                json.dumps(charge, ensure_ascii=False, default=str).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def _erreur(self, code: int, message: str) -> None:
            self._json({"erreur": message}, code)

        def _corps_json(self) -> dict[str, Any]:
            longueur = int(self.headers.get("Content-Length") or 0)
            if longueur <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(longueur).decode("utf-8")) or {}
            except (ValueError, UnicodeDecodeError):
                return {}

        def _jeton_fourni(self) -> str:
            """Jeton presente par le client, quelle qu'en soit la forme.

            L'en-tete est la voie normale de l'interface. L'URL sert au tout
            premier acces (c'est ainsi que le navigateur est ouvert). Le
            cookie prend le relais ensuite, pour qu'un simple rechargement de
            l'adresse — sans le jeton — continue de fonctionner.
            """
            depuis_entete = self.headers.get("X-Jeton")
            if depuis_entete:
                return depuis_entete
            depuis_url = parse_qs(urlparse(self.path).query).get("jeton", [""])[0]
            if depuis_url:
                return depuis_url
            for morceau in (self.headers.get("Cookie") or "").split(";"):
                nom, _, valeur = morceau.strip().partition("=")
                if nom == NOM_COOKIE:
                    return valeur
            return ""

        def _jeton_valide(self) -> bool:
            """Le jeton empeche une page web tierce de piloter l'application.

            Un serveur sur 127.0.0.1 est joignable par tout programme de la
            machine, y compris un onglet ouvert sur un site quelconque : sans
            ce controle, ce site pourrait declencher des analyses ou lire la
            watchlist. Le cookie est pose en SameSite=Strict et l'interface
            envoie le jeton par en-tete : un site tiers ne peut faire ni l'un
            ni l'autre.
            """
            return secrets.compare_digest(self._jeton_fourni(), app.jeton)

        # ------------------------------------------------------------ routes
        def do_GET(self) -> None:  # noqa: N802
            chemin = urlparse(self.path).path
            if chemin.startswith("/api/"):
                if not self._jeton_valide():
                    return self._erreur(403, "jeton absent ou invalide")
                return self._api_get(chemin)
            if chemin.startswith("/data/"):
                return self._servir_donnees(chemin[len("/data/"):])
            return self._servir_statique(chemin)

        def do_HEAD(self) -> None:  # noqa: N802
            self.do_GET()

        def do_POST(self) -> None:  # noqa: N802
            chemin = urlparse(self.path).path
            if not chemin.startswith("/api/"):
                return self._erreur(404, "route inconnue")
            if not self._jeton_valide():
                return self._erreur(403, "jeton absent ou invalide")
            return self._api_post(chemin)

        def do_DELETE(self) -> None:  # noqa: N802
            chemin = urlparse(self.path).path
            if not chemin.startswith("/api/"):
                return self._erreur(404, "route inconnue")
            if not self._jeton_valide():
                return self._erreur(403, "jeton absent ou invalide")
            return self._api_delete(chemin)

        # --------------------------------------------------------- fichiers
        def _servir_donnees(self, nom: str) -> None:
            nom = Path(unquote(nom)).name  # jamais de remontee de repertoire
            fichier = app.fichier_donnees(nom)
            if not fichier.exists():
                fichier = dossier_site() / "data" / nom
            if not fichier.exists():
                return self._erreur(404, f"donnée absente : {nom}")
            self._repondre(200, fichier.read_bytes(), "application/json; charset=utf-8")

        def _servir_statique(self, chemin: str) -> None:
            # Le jeton presente dans l'URL est converti en cookie a l'ouverture
            # de la page : l'interface reste utilisable apres un simple
            # rechargement, sans que l'utilisateur ait quoi que ce soit a saisir.
            depuis_url = parse_qs(urlparse(self.path).query).get("jeton", [""])[0]
            poser = bool(depuis_url) and secrets.compare_digest(depuis_url, app.jeton)

            relatif = unquote(chemin.lstrip("/")) or "index.html"
            racine = dossier_site().resolve()
            fichier = (racine / relatif).resolve()
            if not str(fichier).startswith(str(racine)) or not fichier.is_file():
                return self._erreur(404, "page introuvable")
            type_mime = mimetypes.guess_type(str(fichier))[0] or "application/octet-stream"
            if type_mime.startswith("text/") or type_mime == "application/javascript":
                type_mime += "; charset=utf-8"
            self._repondre(200, fichier.read_bytes(), type_mime, poser_cookie=poser)

        # -------------------------------------------------------------- API
        def _api_get(self, chemin: str) -> None:
            if chemin == "/api/etat":
                catalogue = load_universes().get("universes") or {}
                return self._json(
                    {
                        "version": VERSION_API,
                        "mode": "application locale",
                        "analyse": app.etat_analyse(),
                        "chemins": resume_chemins(),
                        "univers": [
                            {
                                "cle": cle,
                                "libelle": bloc.get("label", cle),
                                "region": bloc.get("region"),
                                "nombre": len(tickers_for([cle])),
                            }
                            for cle, bloc in catalogue.items()
                        ],
                        "univers_par_defaut": load_universes().get("default_selection") or [],
                        "alertes_email": app.settings.alerts_email_enabled,
                    }
                )

            if chemin == "/api/watchlist":
                return self._json({"titres": app.db.watchlist()})

            if chemin == "/api/alertes":
                return self._json(
                    {
                        "regles": app.db.alert_rules(enabled_only=False),
                        "journal": app.db.events(limit=60),
                        "types": list(ALERT_KINDS),
                    }
                )

            if chemin.startswith("/api/titre/"):
                ticker = unquote(chemin[len("/api/titre/"):]).upper()
                if not ticker:
                    return self._erreur(400, "ticker manquant")
                try:
                    fiche = app.detail_titre(ticker)
                except Exception as exc:  # noqa: BLE001
                    return self._erreur(502, f"données indisponibles : {exc}")
                if fiche is None:
                    return self._erreur(404, "titre inconnu")
                return self._json(fiche)

            if chemin.startswith("/api/historique/"):
                ticker = unquote(chemin[len("/api/historique/"):]).upper()
                return self._json({"points": app.db.score_history(ticker)})

            return self._erreur(404, "route inconnue")

        def _api_post(self, chemin: str) -> None:
            corps = self._corps_json()

            if chemin == "/api/analyse":
                univers = [str(u) for u in (corps.get("univers") or [])]
                if not univers:
                    univers = load_universes().get("default_selection") or []
                if not univers:
                    return self._erreur(400, "aucun univers sélectionné")
                demarree = app.lancer_analyse(univers, bool(corps.get("cache", True)))
                if not demarree:
                    return self._erreur(409, "une analyse est déjà en cours")
                return self._json({"demarree": True, "univers": univers})

            if chemin == "/api/watchlist":
                ticker = str(corps.get("ticker") or "").strip().upper()
                if not ticker:
                    return self._erreur(400, "ticker manquant")
                app.db.add_to_watchlist(ticker, str(corps.get("note") or ""))
                return self._json({"titres": app.db.watchlist()})

            if chemin == "/api/alertes":
                ticker = str(corps.get("ticker") or "").strip().upper()
                genre = str(corps.get("type") or "")
                if not ticker:
                    return self._erreur(400, "ticker manquant")
                if genre not in ALERT_KINDS:
                    return self._erreur(400, f"type d'alerte inconnu : {genre}")
                parametres = corps.get("parametres") or {}
                if genre in ("price_above", "price_below"):
                    try:
                        seuil = float(parametres.get("threshold"))
                    except (TypeError, ValueError):
                        seuil = 0.0
                    if seuil <= 0:
                        return self._erreur(400, "seuil de cours strictement positif attendu")
                app.db.add_alert_rule(ticker, genre, parametres)
                return self._json({"regles": app.db.alert_rules(enabled_only=False)})

            return self._erreur(404, "route inconnue")

        def _api_delete(self, chemin: str) -> None:
            if chemin.startswith("/api/watchlist/"):
                ticker = unquote(chemin[len("/api/watchlist/"):]).upper()
                app.db.remove_from_watchlist(ticker)
                return self._json({"titres": app.db.watchlist()})

            if chemin.startswith("/api/alertes/"):
                identifiant = chemin[len("/api/alertes/"):]
                if not identifiant.isdigit():
                    return self._erreur(400, "identifiant invalide")
                app.db.delete_alert_rule(int(identifiant))
                return self._json({"regles": app.db.alert_rules(enabled_only=False)})

            return self._erreur(404, "route inconnue")

    return Gestionnaire


def demarrer(
    settings: Settings, cfg: ScoringConfig, *, port: int = 0
) -> tuple[ThreadingHTTPServer, Application, threading.Thread]:
    """Demarre le serveur sur la boucle locale et renvoie (serveur, app, fil).

    Le port 0 laisse le systeme en choisir un libre : deux lancements
    simultanes ne se genent pas, et aucun conflit avec un autre programme.
    """
    app = Application(settings, cfg)
    serveur = ThreadingHTTPServer(("127.0.0.1", port), construire_gestionnaire(app))
    serveur.daemon_threads = True
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    return serveur, app, fil
