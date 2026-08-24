"""L'avertissement de non-conseil est une exigence fonctionnelle : il doit
etre present, non vide, et aucune formulation prescriptive ne doit apparaitre
dans les textes destines a l'utilisateur.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from investassist import disclaimers

ROOT = Path(__file__).resolve().parents[1]

# Formulations interdites : incitation directe ou prediction de cours.
FORBIDDEN = [
    r"\bachetez\b",
    r"\bvendez\b",
    r"\bva monter\b",
    r"\bva baisser\b",
    r"\bva progresser\b",
    r"recommand(?:ons|e) d'acheter",
    r"conseil d'achat",
]


def test_avertissement_non_vide_et_explicite():
    for text in (disclaimers.MAIN, disclaimers.MAIN_HTML):
        assert "conseil en investissement" in text
        assert "prediction" in text or "prédiction" in text


def test_formulation_de_rang_neutre():
    phrase = disclaimers.ranking_phrasing("MSFT", 1)
    assert "classé n°1" in phrase
    assert not any(re.search(pattern, phrase, re.IGNORECASE) for pattern in FORBIDDEN)


def _user_facing_strings(source: str) -> list[tuple[int, str]]:
    """Litteraux de chaine du module, docstrings exclues.

    Les docstrings sont ecartees car elles documentent precisement les
    formulations a ne jamais employer : les inclure produirait un faux
    positif sur la regle qui les interdit.
    """
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_aucune_formulation_prescriptive_dans_le_code():
    """Balayage des textes destines a l'utilisateur (hors documentation)."""
    files = [ROOT / "app.py"] + sorted((ROOT / "src").rglob("*.py"))
    fautes = []
    for path in files:
        for ligne, texte in _user_facing_strings(path.read_text(encoding="utf-8")):
            for pattern in FORBIDDEN:
                match = re.search(pattern, texte, re.IGNORECASE)
                if match:
                    fautes.append(f"{path.relative_to(ROOT)}:{ligne} → {match.group(0)}")
    assert fautes == [], "Formulations prescriptives detectees : " + "; ".join(fautes)


def test_chaque_vue_de_l_interface_appelle_la_banniere():
    """Garde-fou : toute vue de app.py doit afficher l'avertissement."""
    content = (ROOT / "app.py").read_text(encoding="utf-8")
    vues = re.findall(r"^def (view_\w+)\(\) -> None:(.*?)(?=^def |\Z)", content, re.M | re.S)
    assert vues, "aucune vue detectee dans app.py"
    sans_banniere = [nom for nom, corps in vues if "disclaimer_banner" not in corps]
    assert sans_banniere == [], f"Vues sans avertissement : {sans_banniere}"


def test_pied_de_page_des_alertes():
    assert "conseil en investissement" in disclaimers.ALERT_FOOTER
    assert "incitation" in disclaimers.ALERT_FOOTER
