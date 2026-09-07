"""Tests des profils d'investissement : reponderation sans recalcul."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from investassist import scoring
from investassist.config import Profil, load_profils, load_scoring

sys.path.insert(0, str(Path(__file__).parent))
from test_scoring import make_fundamentals  # noqa: E402

CONFIG = load_scoring()
PROFILS = load_profils()


def _note(**kwargs):
    fund = make_fundamentals(**kwargs)
    return fund, scoring.score_stock(fund, CONFIG)


def test_profil_par_defaut_ne_change_rien():
    """Le profil « équilibré » ne redefinit rien : le score doit etre
    strictement identique, sinon la reference n'en est plus une."""
    _, score = _note()
    renote = scoring.appliquer_profil(score, CONFIG, PROFILS.profils["equilibre"])
    assert renote is score
    assert renote.composite == score.composite


def test_reponderation_ne_touche_ni_valeurs_ni_sous_scores():
    """Un profil change l'importance des criteres, jamais leur mesure."""
    _, score = _note()
    renote = scoring.appliquer_profil(score, CONFIG, PROFILS.profils["securise"])

    avant = {c.key: (c.value, c.score) for c in score.criteria_flat()}
    apres = {c.key: (c.value, c.score) for c in renote.criteria_flat()}
    # market_size n'est retourne que dans « fort potentiel » ; ici tout doit
    # etre identique.
    assert avant == apres
    # Seuls les poids et le composite bougent.
    assert renote.composite != score.composite


def test_objet_d_origine_jamais_modifie():
    """L'interface doit pouvoir passer d'un profil a l'autre et revenir."""
    _, score = _note()
    composite = score.composite
    poids = {k: p.weight for k, p in score.pillars.items()}
    scoring.appliquer_profil(score, CONFIG, PROFILS.profils["croissance"])
    scoring.appliquer_profil(score, CONFIG, PROFILS.profils["potentiel"])
    assert score.composite == composite
    assert {k: p.weight for k, p in score.pillars.items()} == poids


def test_profil_croissance_ignore_le_dividende():
    """Poids nul sur le pilier dividende : un titre distributeur et un titre
    qui ne distribue pas doivent etre departages sur le reste."""
    _, avec = _note(dividends=True)
    _, sans = _note(dividends=False)
    profil = PROFILS.profils["croissance"]
    a = scoring.appliquer_profil(avec, CONFIG, profil)
    b = scoring.appliquer_profil(sans, CONFIG, profil)
    assert a.pillars["dividend"].weight == 0.0
    assert a.composite == pytest.approx(b.composite)


def test_critere_inverse_retourne_le_sous_score_et_le_dit():
    """« Fort potentiel » retourne la taille : petite capitalisation =
    avantage. La valeur brute doit rester intacte et l'inversion annoncee,
    sans quoi le lecteur verrait un sous-score incoherent avec le chiffre."""
    _, score = _note()
    taille_avant = score.criterion("market_size")
    assert taille_avant is not None and taille_avant.score is not None

    renote = scoring.appliquer_profil(score, CONFIG, PROFILS.profils["potentiel"])
    taille = renote.criterion("market_size")
    assert taille.value == taille_avant.value          # la mesure ne change pas
    assert taille.score == pytest.approx(100.0 - taille_avant.score)
    assert "inversé" in taille.detail


def test_exigence_de_profil_ecarte_avec_un_motif_explicite():
    """Un titre trop endette pour le profil « sécurisé » est ecarte — mais le
    motif doit dire qu'il ne CORRESPOND PAS, pas que ses donnees manquent."""
    fund, score = _note()
    critere = score.criterion("net_debt_to_ebitda")
    critere.value = 4.5                      # au-dessus du plafond de 3

    renote = scoring.appliquer_profil(score, CONFIG, PROFILS.profils["securise"])
    assert renote.ranked is False
    assert "Ne correspond pas au profil" in renote.exclusion_reason
    assert "4.5 fois l'EBITDA" in renote.exclusion_reason
    assert "incomplètes" not in renote.exclusion_reason


def test_exigence_sans_objet_ne_disqualifie_pas():
    """Une banque n'a pas de dette nette sur EBITDA : l'exigence ne peut pas
    lui etre opposee, la question ne se pose pas pour elle."""
    _, score = _note()
    critere = score.criterion("net_debt_to_ebitda")
    critere.value, critere.not_applicable = 9.0, True

    renote = scoring.appliquer_profil(score, CONFIG, PROFILS.profils["securise"])
    assert "EBITDA" not in renote.exclusion_reason


def test_titre_deja_exclu_le_reste_quel_que_soit_le_profil():
    """Un profil repondere des criteres ; il ne repare pas une lacune."""
    _, court = _note(years=2)
    assert court.ranked is False
    for profil in PROFILS.profils.values():
        renote = scoring.appliquer_profil(court, CONFIG, profil)
        assert renote.ranked is False


def test_pilier_risque_a_poids_nul_ne_change_pas_le_score_de_reference():
    """Le pilier « risque » est calcule pour tous les titres mais pese zéro
    dans la ponderation d'origine : le classement de reference doit etre
    exactement celui d'avant son ajout."""
    _, score = _note()
    risque = score.pillars.get("risk")
    assert risque is not None            # bien calcule
    assert risque.weight == 0.0          # mais sans effet

    retenus = [p for p in score.pillars.values() if p.score is not None and p.weight > 0]
    attendu = sum(p.score * p.weight for p in retenus) / sum(p.weight for p in retenus)
    assert score.composite == pytest.approx(attendu)


def test_profil_inconnu_retombe_sur_le_defaut():
    assert PROFILS.get("nexiste_pas") is None
    assert PROFILS.get(None).cle == PROFILS.defaut


def test_profil_sans_pilier_calculable_ne_plante_pas():
    """Ponderation degeneree : tous les poids a zero. On ne doit ni diviser
    par zero ni renvoyer un score fantaisiste."""
    _, score = _note()
    absurde = Profil(
        cle="absurde", label="Absurde", resume="", description="",
        pillar_weights={k: 0.0 for k in CONFIG.pillar_weights},
    )
    renote = scoring.appliquer_profil(score, CONFIG, absurde)
    assert renote.composite is None or isinstance(renote.composite, float)
