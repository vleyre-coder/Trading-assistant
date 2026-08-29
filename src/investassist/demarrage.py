"""Écran de démarrage et animation de console.

Un exécutable « un seul fichier » se déballe pendant une dizaine de secondes
avant d'afficher quoi que ce soit. Deux retours visuels comblent ce vide :
l'écran de démarrage fourni par PyInstaller, et une animation dans la console.
Les deux se dégradent proprement lorsqu'ils ne sont pas disponibles (lancement
depuis les sources, sortie redirigée vers un fichier).
"""
from __future__ import annotations

import itertools
import os
import sys
import threading
import time


class EcranDemarrage:
    """Pilote l'écran de démarrage de PyInstaller, s'il est présent."""

    def __init__(self) -> None:
        self._module = None
        # Le lanceur de PyInstaller renseigne cette variable uniquement
        # lorsque l'écran de démarrage tourne réellement. Sans ce test, le
        # module pyi_splash affiche une trace d'erreur au démarrage quand
        # l'exécutable a été construit sans écran — un bruit inquiétant pour
        # rien.
        if not os.environ.get("_PYI_SPLASH_IPC"):
            return
        try:
            import pyi_splash  # type: ignore

            self._module = pyi_splash
        except (ImportError, RuntimeError):
            self._module = None

    @property
    def actif(self) -> bool:
        return self._module is not None

    def message(self, texte: str) -> None:
        if self._module is None:
            return
        try:
            self._module.update_text(texte)
        except Exception:  # noqa: BLE001 - un écran décoratif ne doit rien casser
            self._module = None

    def fermer(self) -> None:
        if self._module is None:
            return
        try:
            self._module.close()
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._module = None


class Animation:
    """Petite animation de console pendant une attente.

    Elle n'écrit que sur un vrai terminal : redirigée vers un fichier, elle
    produirait des milliers de lignes inutiles.
    """

    IMAGES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    IMAGES_SIMPLES = "|/-\\"

    def __init__(self, message: str, flux=None) -> None:
        self.message = message
        self.flux = flux or sys.stdout
        self._arret = threading.Event()
        self._fil: threading.Thread | None = None

    def _utilisable(self) -> bool:
        try:
            return bool(self.flux) and self.flux.isatty()
        except (AttributeError, ValueError):
            return False

    def _images(self) -> str:
        encodage = (getattr(self.flux, "encoding", "") or "").lower()
        # Les consoles Windows héritées ne savent pas afficher les points
        # Braille : on retombe alors sur une animation en ASCII.
        return self.IMAGES if "utf" in encodage else self.IMAGES_SIMPLES

    def _boucle(self) -> None:
        images = self._images()
        for image in itertools.cycle(images):
            if self._arret.is_set():
                break
            try:
                self.flux.write(f"\r  {image}  {self.message}   ")
                self.flux.flush()
            except (ValueError, OSError):
                return
            time.sleep(0.09)

    def __enter__(self) -> "Animation":
        if self._utilisable():
            self._fil = threading.Thread(target=self._boucle, daemon=True)
            self._fil.start()
        return self

    def __exit__(self, *exception) -> None:
        self._arret.set()
        if self._fil:
            self._fil.join(timeout=0.4)
            try:
                self.flux.write("\r" + " " * (len(self.message) + 10) + "\r")
                self.flux.flush()
            except (ValueError, OSError):
                pass
