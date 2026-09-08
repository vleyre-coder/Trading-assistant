"""Verrous sur la palette de l'interface.

Le style est noir et blanc par choix : une seule serie est representee (le
score, la tendance), donc aucune teinte n'a d'identite a porter. Les seules
couleurs admises sont les quatre statuts, et elles ne portent jamais seules
une information — le texte l'accompagne toujours.

Ces tests mesurent la feuille de style au lieu de la relire : un contraste
insuffisant ou une teinte reintroduite par mégarde echoue ici, pas six mois
plus tard sur un ecran lumineux.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

FEUILLE = Path(__file__).resolve().parents[1] / "web" / "assets" / "styles.css"
CSS = FEUILLE.read_text(encoding="utf-8")

# Statuts declares dans la feuille. Ce sont les seules couleurs saturees.
STATUTS = {"#0ca30c", "#fab219", "#ec835a", "#d03b3b"}


def _octets(hexa: str) -> tuple[int, int, int]:
    h = hexa.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _luminance(rgb: tuple[float, float, float]) -> float:
    canaux = []
    for valeur in rgb:
        v = valeur / 255
        canaux.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * canaux[0] + 0.7152 * canaux[1] + 0.0722 * canaux[2]


def contraste(avant: str, arriere: str) -> float:
    """Rapport de contraste WCAG entre deux couleurs hexadecimales."""
    clair, sombre = sorted(
        (_luminance(_octets(avant)), _luminance(_octets(arriere))), reverse=True
    )
    return (clair + 0.05) / (sombre + 0.05)


def _jetons(bloc: str) -> dict[str, str]:
    return {
        nom.removeprefix("--"): valeur.strip()
        for nom, valeur in re.findall(r"(--[a-z-]+):\s*([^;]+);", bloc)
    }


def _bloc(entete: str) -> str:
    """Corps du bloc de declarations qui suit cet en-tete."""
    debut = CSS.index(entete) + len(entete)
    return CSS[debut:CSS.index("\n}", debut)]


CLAIR = _jetons(_bloc(":root {"))
SOMBRE = _jetons(_bloc(':root:not([data-theme="light"]) {'))
SOMBRE_EXPLICITE = _jetons(_bloc(':root[data-theme="dark"] {'))


# ==================================================== absence de teinte
def test_toutes_les_couleurs_hexadecimales_sont_neutres_hors_statuts():
    """Une teinte reintroduite quelque part casse le parti pris monochrome
    sans qu'aucun ecran ne paraisse fautif : elle passerait inapercue."""
    fautives = []
    for hexa in re.findall(r"#[0-9a-fA-F]{3,8}\b", CSS):
        if hexa.lower() in STATUTS:
            continue
        r, v, b = _octets(hexa)
        if max(r, v, b) - min(r, v, b) > 4:
            fautives.append(hexa)
    assert not fautives, f"couleurs saturees hors statuts : {sorted(set(fautives))}"


def test_les_couleurs_translucides_sont_neutres():
    """Meme controle pour les rgba() : un voile teinte se voit surtout en
    aplat, la ou il sert justement de fond a du texte."""
    fautives = []
    for canaux in re.findall(r"rgba?\(([^)]+)\)", CSS):
        nombres = [float(x) for x in re.findall(r"[\d.]+", canaux)[:3]]
        if len(nombres) == 3 and max(nombres) - min(nombres) > 4:
            fautives.append(canaux.strip())
    assert not fautives, f"voiles teintes : {fautives}"


# ======================================================= lisibilite reelle
@pytest.mark.parametrize("theme", ["clair", "sombre", "sombre_explicite"])
def test_chaque_niveau_d_encre_atteint_le_contraste_requis(theme):
    """Les trois niveaux d'encre doivent tenir 4,5:1 (norme WCAG AA pour le
    texte courant) sur TOUTES les surfaces qu'ils peuvent rencontrer — y
    compris la surface composee par le voile de serie, la plus claire en
    mode sombre. Le gris discret mesurait 3,95:1 sur celle-la : lisible sur
    un ecran de bureau, illisible en plein jour.
    """
    jetons = {"clair": CLAIR, "sombre": SOMBRE, "sombre_explicite": SOMBRE_EXPLICITE}[theme]
    base = jetons["surface"]
    # Surface composee : le voile de serie pose sur la surface de carte.
    voile = re.findall(r"[\d.]+", jetons["series-voile"])
    alpha = float(voile[3])
    teinte = _octets(jetons["series"])
    fond = _octets(base)
    composee = "#%02x%02x%02x" % tuple(
        round(teinte[i] * alpha + fond[i] * (1 - alpha)) for i in range(3)
    )

    surfaces = [jetons["plane"], jetons["surface"], jetons["surface-alt"], composee]
    for encre in ("ink", "ink-secondary", "ink-muted"):
        for surface in surfaces:
            mesure = contraste(jetons[encre], surface)
            assert mesure >= 4.5, (
                f"{theme} : --{encre} ({jetons[encre]}) sur {surface} "
                f"ne donne que {mesure:.2f}:1"
            )


def test_chaque_theme_definit_ce_qui_s_ecrit_sur_la_serie():
    """En monochrome, un aplat de serie est noir en clair et BLANC en
    sombre : un texte fixe a blanc y devenait invisible. Le jeton
    --sur-series existe pour cela et doit accompagner chaque --series.
    """
    for nom, jetons in (("clair", CLAIR), ("sombre", SOMBRE),
                        ("sombre explicite", SOMBRE_EXPLICITE)):
        assert "sur-series" in jetons, f"--sur-series absent du theme {nom}"
        mesure = contraste(jetons["sur-series"], jetons["series"])
        assert mesure >= 4.5, f"{nom} : texte sur aplat de serie a {mesure:.2f}:1"


def test_aucun_blanc_ni_noir_code_en_dur_sur_un_aplat_de_serie():
    """La regression exacte a eviter : « background: var(--series); color:
    #fff », correcte en clair et invisible en sombre."""
    for regle in re.findall(r"\{[^}]*var\(--series\)[^}]*\}", CSS):
        if "background" not in regle:
            continue
        couleurs = re.findall(r"color:\s*(#[0-9a-fA-F]{3,6})", regle)
        assert not couleurs, f"couleur figee sur un aplat de serie : {couleurs}"


# ============================================================ typographie
def test_la_pile_de_polices_ne_depend_d_aucune_ressource_externe():
    """Le style s'inspire d'une grotesque neutre, mais aucune police ne peut
    etre telechargee : l'outil est personnel et ne doit rien reveler de sa
    consultation. La pile doit donc contenir une police systeme."""
    pile = CLAIR["font"]
    assert "@font-face" not in CSS
    assert "system-ui" in pile or "-apple-system" in pile
    assert pile.rstrip().endswith("sans-serif")
