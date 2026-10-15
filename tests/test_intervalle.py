"""L'arithmétique d'intervalles, et la vérification de ce qu'elle promet.

Le module `intervalle` affirme deux choses sur lesquelles tout le reste repose :

1. la propagation encadre bien le résultat, pour des grandeurs **indépendantes
   et positives** — c'est le test de Monte-Carlo ci-dessous, et il est ici
   parce que la docstring du module dit « vérifiez plutôt que de croire » ;
2. son pessimisme joue du côté de la prudence — il élargit l'encadrement, donc
   il rend la porte plus réservée, jamais plus bavarde.
"""
from __future__ import annotations

import random

import pytest

from carbongate.intervalle import Ecart, Intervalle, somme


def test_un_intervalle_inverse_est_refuse():
    with pytest.raises(ValueError, match="bornes inversées"):
        Intervalle(3.0, 1.0)


def test_une_grandeur_negative_est_refusee():
    """Une borne basse négative trahit un coefficient mal saisi."""
    with pytest.raises(ValueError, match="négative"):
        Intervalle(-1.0, 2.0)


def test_un_nan_est_refuse():
    with pytest.raises(ValueError, match="fini"):
        Intervalle(float("nan"), 1.0)


def test_autour_encadre_la_valeur():
    i = Intervalle.autour(100.0, 0.3)
    assert (i.bas, i.haut) == (70.0, 130.0)
    assert i.milieu == 100.0


@pytest.mark.parametrize("part", [-0.1, 1.0, 1.5])
def test_autour_refuse_une_part_hors_bornes(part):
    with pytest.raises(ValueError):
        Intervalle.autour(10.0, part)


def test_la_somme_vide_vaut_zero_exact():
    assert somme([]) == Intervalle(0.0, 0.0)


def test_division_par_zero_refusee():
    with pytest.raises(ValueError, match="positive"):
        Intervalle(1.0, 2.0) / 0


# --------------------------------------------------------------------------- #
# La vérification promise par la docstring du module
# --------------------------------------------------------------------------- #
def test_la_propagation_encadre_un_tirage_de_monte_carlo():
    """Pour des grandeurs indépendantes et positives, l'encadrement tient.

    On tire au hasard dans chaque intervalle, on calcule l'expression de la
    formule SCI — (a×b + c×d) / e — et on vérifie qu'aucun tirage ne sort de
    l'encadrement propagé. Puis qu'il n'est pas absurdement large : les tirages
    doivent approcher les deux bornes, sinon l'arithmétique serait correcte et
    inutile.
    """
    alea = random.Random(20261014)
    a, b = Intervalle(0.74, 3.5), Intervalle(400.0, 900.0)
    c, d = Intervalle(0.30, 0.42), Intervalle(800.0, 1600.0)
    e = 3_600_000.0

    propage = somme((a * b, c * d)) / e

    observes = []
    for _ in range(20_000):
        va = alea.uniform(a.bas, a.haut)
        vb = alea.uniform(b.bas, b.haut)
        vc = alea.uniform(c.bas, c.haut)
        vd = alea.uniform(d.bas, d.haut)
        observes.append((va * vb + vc * vd) / e)

    assert min(observes) >= propage.bas, "un tirage est passé sous la borne basse"
    assert max(observes) <= propage.haut, \
        "un tirage est passé au-dessus de la borne haute"

    # Et l'encadrement n'est pas inutilement lâche : les extrêmes du tirage
    # atteignent chaque borne à mieux que 10 % de la largeur.
    marge = 0.10 * propage.largeur
    assert min(observes) - propage.bas < marge
    assert propage.haut - max(observes) < marge


def test_le_pessimisme_joue_du_cote_de_la_prudence():
    """`x - x` rend un écart large au lieu de zéro, et c'est voulu ici.

    L'arithmétique d'intervalles ignore qu'il s'agit de la même variable. Le
    résultat enjambe zéro, donc la porte refuse d'annoncer une économie — ce
    qui est exactement la bonne conclusion quand on compare une chose à
    elle-même.
    """
    x = Intervalle(2.0, 6.0)
    ecart = x - x
    assert ecart.contient_zero
    assert not ecart.economie_etablie


# --------------------------------------------------------------------------- #
# Ecart : la seule question qui compte
# --------------------------------------------------------------------------- #
def test_une_economie_nest_etablie_que_si_toute_la_plage_est_positive():
    assert Ecart(0.5, 2.0).economie_etablie
    assert not Ecart(-0.1, 2.0).economie_etablie, "enjambe zéro"
    assert not Ecart(0.0, 2.0).economie_etablie, "touche zéro"


def test_un_surcout_se_dit_aussi():
    """La mesure a un coût ; si elle dépasse ce qu'elle épargne, il faut l'avouer."""
    ecart = Ecart(-3.0, -0.5)
    assert ecart.surcout_etabli and not ecart.economie_etablie


def test_un_gros_intervalle_moins_un_petit_peut_ne_rien_etablir():
    """Le cas qui motive tout le projet.

    Un coût complet de [10, 30] et un coût retenu de [8, 25] : le milieu suggère
    une économie de 3,5 g, et pourtant l'écart va de -15 à +22. Prendre les
    milieux aurait publié un gain inventé.
    """
    complet, retenu = Intervalle(10.0, 30.0), Intervalle(8.0, 25.0)
    assert complet.milieu - retenu.milieu == pytest.approx(3.5)
    ecart = complet - retenu
    assert ecart.contient_zero and not ecart.economie_etablie


def test_laffichage_ne_donne_pas_de_faux_chiffres_significatifs():
    """Trois chiffres significatifs : au-delà, une estimation a l'air d'une mesure."""
    assert str(Intervalle(0.30712, 1.2399)) == "[0.307 ; 1.24]"
    assert str(Intervalle(0.0, 0.0)) == "[0 ; 0]"


def test_laffichage_arrondit_vers_lexterieur():
    """L'intervalle imprimé doit contenir celui qu'on a calculé.

    Au plus proche, [0,3078 ; 1,2341] s'afficherait [0.308 ; 1.23] — qui ne
    contient plus les bornes réelles. L'affichage défairait ce que le calcul
    protège.
    """
    assert str(Intervalle(0.3078, 1.2341)) == "[0.307 ; 1.24]"
    # Et l'arrondi du banquier ne décide d'aucune borne : 1234,5 descend.
    assert str(Intervalle(1234.5, 5678.1)) == "[1234 ; 5679]"


@pytest.mark.parametrize("bas,haut", [
    (0.3078, 1.2341), (1234.5, 5678.1), (0.000412, 0.00099),
    (9.9999, 10.0001), (1.0, 1.0),
])
def test_laffichage_contient_toujours_les_bornes_reelles(bas, haut):
    affiche = str(Intervalle(bas, haut))
    lu_bas, lu_haut = (float(x) for x in affiche.strip("[]").split(" ; "))
    assert lu_bas <= bas and lu_haut >= haut, affiche
