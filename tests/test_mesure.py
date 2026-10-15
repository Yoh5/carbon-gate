"""La sonde : est-ce qu'elle mesure vraiment ce qu'elle dit mesurer ?

Un module de mesure qu'on n'étalonne pas ne produit que des nombres. Les tests
ci-dessous confrontent la sonde à des charges dont on connaît la vérité :
un processus qui brûle N secondes de processeur, un processus qui dort, un
processus qui occupe M mégaoctets.

**Les bornes sont larges exprès.** Un exécuteur de CI partagé est bruyant, et
un test qui exigerait ±2 % passerait ici et échouerait là-bas une fois sur
trois — ce qui apprendrait à tout le monde à ignorer la suite. On vérifie la
justesse d'ordre de grandeur et la **monotonie**, qui est la propriété dont le
calcul SCI a réellement besoin.

Étalonnage relevé le 14/10/2026 sous Windows : 0,5 s → 0,500 s ; 1,0 s →
0,984 s ; 2,0 s → 2,000 s. Soit moins de 2 % d'écart là où le test en tolère 35.
"""
from __future__ import annotations

import sys

import pytest

from carbongate.mesure import (ECHANTILLONS_MINIMUM, Mesure, cpu_depuis_stat,
                               mesurer, sonde_de_la_plateforme, _integrer)

SONDE = sonde_de_la_plateforme()
besoin_de_sonde = pytest.mark.skipif(
    SONDE is None, reason=f"aucune sonde pour {sys.platform}")


def brule(secondes: float) -> list[str]:
    return [sys.executable, "-c",
            f"import time\nfin=time.monotonic()+{secondes}\nx=0\n"
            "while time.monotonic()<fin: x+=1\n"]


def dort(secondes: float) -> list[str]:
    return [sys.executable, "-c", f"import time; time.sleep({secondes})"]


def occupe(mo: int, secondes: float) -> list[str]:
    return [sys.executable, "-c",
            f"import time\nb=bytearray({mo}*1024*1024)\ntime.sleep({secondes})\n"]


# --------------------------------------------------------------------------- #
# L'analyse de /proc, testable même sans /proc
# --------------------------------------------------------------------------- #
def test_le_nom_dexecutable_avec_espaces_et_parentheses_ne_decale_rien():
    """La ligne la plus facile à casser de tout le module.

    Un `.split()` naïf décalerait toutes les colonnes et rendrait un temps
    processeur plausible et faux — l'erreur qu'aucun test ne rattrape si on ne
    l'écrit pas exprès.
    """
    # Champs 4 à 13 de proc(5) : dix colonnes entre l'état et utime. En mettre
    # onze ferait lire la colonne d'à côté — une valeur plausible et fausse,
    # exactement la panne que ce test existe pour attraper. (Elle m'est arrivée
    # en écrivant ce test : la version fautive rendait 2,13 au lieu de 3,0.)
    entre_deux = " ".join(str(i) for i in range(4, 14))
    queue = " ".join(str(i) for i in range(30))
    stat = f"1234 (mon prog (test)) S {entre_deux} 200 100 {queue}"
    # utime = 200, stime = 100, à 100 tics par seconde → 3 s
    assert cpu_depuis_stat(stat, 100.0) == pytest.approx(3.0)


def test_un_nom_simple_marche_aussi():
    entre_deux = " ".join(str(i) for i in range(4, 14))
    queue = " ".join(str(i) for i in range(30))
    stat = f"4321 (python3) S {entre_deux} 50 25 {queue}"
    assert cpu_depuis_stat(stat, 100.0) == pytest.approx(0.75)


# --------------------------------------------------------------------------- #
# L'intégration
# --------------------------------------------------------------------------- #
def test_un_seul_releve_nintegre_rien():
    """On ne sait pas quelle durée il couvre, et `reserves` le dit déjà."""
    assert _integrer([(0.0, 0.0, 1000.0)]) == 0.0
    assert _integrer([]) == 0.0


def test_les_trapezes_valent_mieux_quun_pas_a_gauche():
    """Une mémoire qui monte linéairement de 0 à 100 sur 2 s vaut 100 o.s.

    Un pas à gauche rendrait 50 (sous-estimation de moitié), un pas à droite
    150. Les trapèzes rendent la valeur exacte pour une rampe.
    """
    releves = [(0.0, 0.0, 0.0), (1.0, 0.0, 50.0), (2.0, 0.0, 100.0)]
    assert _integrer(releves) == pytest.approx(100.0)


def test_une_memoire_constante_sintegre_en_produit():
    releves = [(0.0, 0.0, 10.0), (1.0, 0.0, 10.0), (3.0, 0.0, 10.0)]
    assert _integrer(releves) == pytest.approx(30.0)


# --------------------------------------------------------------------------- #
# Garde-fous d'appel
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("periode", [0.0, -0.1])
def test_une_periode_non_positive_est_refusee(periode):
    with pytest.raises(ValueError, match="positive"):
        mesurer([sys.executable, "-c", "pass"], periode=periode)


def test_un_code_de_sortie_non_nul_rend_la_mesure_inutilisable():
    """Mesurer un job qui a échoué est licite ; s'en servir ne l'est pas."""
    m = mesurer([sys.executable, "-c", "raise SystemExit(3)"])
    assert m.code_de_sortie == 3
    assert not m.utilisable


# --------------------------------------------------------------------------- #
# L'étalonnage
# --------------------------------------------------------------------------- #
@besoin_de_sonde
@pytest.mark.parametrize("consigne", [0.4, 1.0])
def test_le_temps_processeur_suit_le_travail(consigne):
    m = mesurer(brule(consigne))
    assert m.code_de_sortie == 0
    mesure = m.observation.secondes_processeur
    assert mesure == pytest.approx(consigne, rel=0.35), (
        f"{mesure:.3f} s mesurées pour {consigne} s de travail")


@besoin_de_sonde
def test_le_temps_processeur_est_monotone():
    """La propriété dont le calcul SCI a réellement besoin.

    Plus robuste qu'une valeur absolue sur un exécuteur partagé : même si la
    machine est chargée, deux fois plus de travail doit mesurer plus.
    """
    court = mesurer(brule(0.3)).observation.secondes_processeur
    long = mesurer(brule(1.2)).observation.secondes_processeur
    assert long > court * 1.8


@besoin_de_sonde
def test_un_processus_qui_dort_ne_consomme_presque_pas_de_processeur():
    m = mesurer(dort(1.0))
    o = m.observation
    assert o.secondes_horloge >= 1.0
    # Le démarrage de l'interpréteur coûte quelque chose ; ce qui compte est
    # que le processeur soit très inférieur à l'horloge.
    assert o.secondes_processeur < 0.5 * o.secondes_horloge


@besoin_de_sonde
def test_la_memoire_integree_suit_la_memoire_occupee():
    petit = mesurer(occupe(50, 0.8)).observation.gigaoctets_secondes
    gros = mesurer(occupe(300, 0.8)).observation.gigaoctets_secondes
    assert gros > petit * 2, f"{petit:.4f} puis {gros:.4f} Go.s"


@besoin_de_sonde
def test_lintegrale_reste_sous_le_produit_pic_par_duree():
    """La justification du choix des trapèzes.

    Multiplier le pic par la durée majore : l'allocation n'est pas instantanée,
    donc la vraie intégrale est plus basse. Mesurer cet écart est ce qui
    distingue une mesure d'une borne.
    """
    m = mesurer(occupe(300, 0.8))
    majorant = (300 / 1024 + 0.1) * m.observation.secondes_horloge
    assert m.observation.gigaoctets_secondes < majorant


# --------------------------------------------------------------------------- #
# Les réserves : ce qu'on n'a pas pu mesurer se dit
# --------------------------------------------------------------------------- #
@besoin_de_sonde
def test_un_processus_trop_court_declare_une_reserve():
    """Un échantillonnage trop maigre n'est pas une mesure, et le dit."""
    m = mesurer([sys.executable, "-c", "pass"], periode=0.5)
    assert m.echantillons < ECHANTILLONS_MINIMUM
    assert m.reserves, "il faut une réserve, pas un chiffre"
    assert not m.utilisable


@besoin_de_sonde
def test_une_periode_trop_grande_devant_la_duree_se_declare():
    m = mesurer(brule(0.3), periode=0.2)
    assert any("sous-estimé" in r or "trop peu" in r for r in m.reserves), m.reserves


@besoin_de_sonde
def test_une_mesure_propre_na_aucune_reserve():
    m = mesurer(brule(1.0), periode=0.05)
    assert m.reserves == []
    assert m.utilisable
    assert m.echantillons >= ECHANTILLONS_MINIMUM


def test_une_plateforme_sans_sonde_le_dit_au_lieu_dinventer(monkeypatch):
    """Rendre zéro silencieusement serait pire que l'absence de chiffre."""
    monkeypatch.setattr("carbongate.mesure.sonde_de_la_plateforme", lambda: None)
    m = mesurer([sys.executable, "-c", "pass"])
    assert m.observation.secondes_processeur == 0.0
    assert any("aucune sonde" in r for r in m.reserves)
    assert not m.utilisable, "un zéro non mesuré ne doit jamais passer pour une mesure"


def test_une_mesure_sans_reserve_et_sans_echec_est_utilisable():
    m = Mesure(observation=mesurer([sys.executable, "-c", "pass"]).observation,
               code_de_sortie=0, echantillons=10, reserves=[])
    assert m.utilisable


@besoin_de_sonde
def test_la_periode_par_defaut_mesure_proprement_un_job_dune_demi_seconde():
    """Verrouille le défaut choisi par la mesure (voir `PERIODE`).

    À 0,05 s, un job d'une demi-seconde déclenchait une réserve et se trouvait
    exclu du bilan — une réponse lourde pour une sous-estimation de 10 %. À
    0,01 s il est mesuré proprement. Si quelqu'un remonte le défaut, ce test
    tombe et le relevé qui a servi à le choisir est dans le module.
    """
    m = mesurer(brule(0.5))
    assert m.reserves == [], m.reserves
    assert m.echantillons >= 30
