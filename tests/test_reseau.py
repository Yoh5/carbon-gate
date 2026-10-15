"""L'intensité horaire du réseau, et le levier « quand lancer ».

Deux règles s'ajoutent ici à celles du reste du projet :

- **on n'extrapole jamais** une courbe d'intensité au-delà de ce qu'on sait ;
- **une prévision n'est pas une mesure**, et le rapport doit pouvoir le dire.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from carbongate.intervalle import Intervalle
from carbongate.reseau import HorsSerie, Point, Serie, maintenant_utc, quand_lancer

BASE = datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc)

#: Un profil journalier : creux au petit matin, pointe du soir.
PROFIL = [520, 505, 470, 430, 395, 360, 340, 355, 390, 420, 445, 460,
          470, 455, 430, 440, 480, 545, 610, 640, 615, 580, 555, 535]


def serie(profil=PROFIL, origine="mesure", marge=0.0) -> Serie:
    points = [
        Point(BASE + timedelta(hours=h),
              Intervalle.exact(v) if not marge else Intervalle.autour(v, marge),
              origine)
        for h, v in enumerate(profil)
    ]
    return Serie(points, source="profil synthétique pour les tests")


# --------------------------------------------------------------------------- #
# Construction : ce qui doit être refusé
# --------------------------------------------------------------------------- #
def test_une_serie_sans_source_est_refusee():
    with pytest.raises(ValueError, match="source"):
        Serie([Point(BASE, Intervalle.exact(400), "mesure")], source="  ")


def test_un_instant_sans_fuseau_est_refuse():
    """Sur un sujet dont tout l'intérêt est l'heure, c'est une erreur."""
    with pytest.raises(ValueError, match="fuseau"):
        Point(datetime(2026, 10, 15, 0, 0), Intervalle.exact(400), "mesure")


def test_une_origine_inconnue_est_refusee():
    with pytest.raises(ValueError, match="origine"):
        Point(BASE, Intervalle.exact(400), "devinette")


def test_un_trou_dans_la_serie_est_refuse_a_la_construction():
    """Une heure manquante ferait silencieusement attribuer à un job
    l'intensité de l'heure d'avant."""
    points = [Point(BASE, Intervalle.exact(400), "mesure"),
              Point(BASE + timedelta(hours=3), Intervalle.exact(300), "mesure")]
    with pytest.raises(ValueError, match="[Tt]rou"):
        Serie(points, source="série trouée")


def test_un_doublon_est_refuse_aussi():
    points = [Point(BASE, Intervalle.exact(400), "mesure"),
              Point(BASE, Intervalle.exact(300), "mesure")]
    with pytest.raises(ValueError, match="[Tt]rou ou doublon"):
        Serie(points, source="série à doublon")


# --------------------------------------------------------------------------- #
# Lecture : on n'extrapole jamais
# --------------------------------------------------------------------------- #
def test_une_heure_couverte_rend_son_intensite():
    assert serie().intensite_a(BASE + timedelta(hours=6)) == Intervalle.exact(340)


def test_une_heure_entamee_rend_le_point_qui_la_couvre():
    """11h30 relève du point de 11h00, pas d'une interpolation."""
    assert serie().intensite_a(BASE + timedelta(hours=11, minutes=30)) \
        == Intervalle.exact(460)


@pytest.mark.parametrize("decalage", [timedelta(hours=-1), timedelta(hours=24),
                                      timedelta(days=3)])
def test_hors_de_la_serie_on_leve_au_lieu_dextrapoler(decalage):
    """Prolonger la courbe produirait un chiffre qui a l'air d'une mesure."""
    with pytest.raises(HorsSerie):
        serie().intensite_a(BASE + decalage)


def test_une_serie_vide_le_dit_clairement():
    vide = Serie([], source="rien")
    with pytest.raises(HorsSerie, match="vide"):
        vide.intensite_a(BASE)


# --------------------------------------------------------------------------- #
# Fenêtres
# --------------------------------------------------------------------------- #
def test_une_fenetre_doit_tenir_entierement_dans_la_serie():
    """Un job dont la seconde moitié tombe hors de ce qu'on sait n'a pas
    d'intensité connue."""
    fenetres = serie().fenetres(timedelta(hours=4), depuis=BASE,
                                avant=BASE + timedelta(hours=24))
    departs = [f[0] for f in fenetres]
    assert departs[-1] == BASE + timedelta(hours=20), "la dernière doit finir à 24h"


def test_une_fenetre_moyenne_les_heures_traversees():
    fenetres = serie().fenetres(timedelta(hours=3), depuis=BASE + timedelta(hours=4),
                                avant=BASE + timedelta(hours=7))
    assert len(fenetres) == 1
    _, moyenne, _ = fenetres[0]
    assert moyenne.bas == pytest.approx((395 + 360 + 340) / 3)


def test_une_fenetre_qui_touche_une_prevision_est_une_prevision():
    """Une fenêtre n'est mesurée que si TOUT ce qu'elle traverse l'est."""
    points = [Point(BASE + timedelta(hours=h), Intervalle.exact(v),
                    "mesure" if h < 3 else "prevision")
              for h, v in enumerate(PROFIL[:6])]
    s = Serie(points, source="moitié mesurée, moitié prévue")
    fenetres = {f[0]: f[2] for f in s.fenetres(
        timedelta(hours=2), depuis=BASE, avant=BASE + timedelta(hours=6))}
    assert fenetres[BASE] == "mesure"
    assert fenetres[BASE + timedelta(hours=2)] == "prevision", "chevauche 3h"


# --------------------------------------------------------------------------- #
# Le levier : quand lancer
# --------------------------------------------------------------------------- #
ENERGIE = Intervalle(0.0008, 0.0014)  # kWh


def decalage_a(heure: int, **kw):
    return quand_lancer(serie(), ENERGIE, timedelta(hours=1),
                        maintenant=BASE + timedelta(hours=heure),
                        echeance=BASE + timedelta(hours=24), **kw)


def test_depuis_la_pointe_du_soir_il_vaut_mieux_attendre():
    d = decalage_a(19)
    assert d.vaut_le_coup
    assert d.quand == BASE + timedelta(hours=23)
    assert d.gain.economie_etablie


def test_a_lheure_la_plus_propre_on_ne_decale_pas():
    d = decalage_a(6)
    assert not d.vaut_le_coup
    assert "déjà la plus propre" in d.raison


def test_sans_fenetre_avant_lecheance_on_ne_decale_pas():
    d = quand_lancer(serie(), ENERGIE, timedelta(hours=5),
                     maintenant=BASE + timedelta(hours=20),
                     echeance=BASE + timedelta(hours=22))
    assert not d.vaut_le_coup
    assert "aucune fenêtre" in d.raison


def test_une_heure_hors_serie_ne_fait_pas_lever():
    d = quand_lancer(serie(), ENERGIE, timedelta(hours=1),
                     maintenant=BASE + timedelta(days=2))
    assert not d.vaut_le_coup
    assert "pas couverte" in d.raison


def test_un_gain_noye_dans_lincertitude_de_la_prevision_nest_pas_annonce():
    """Le cas qui justifie de distinguer prévision et mesure.

    Avec une marge de ±40 %, l'écart entre la pointe et le creux disparaît
    dans l'incertitude : il reste peut-être un gain, mais on ne peut pas
    l'affirmer, donc on ne l'affirme pas.
    """
    large = serie(origine="prevision", marge=0.40)
    d = quand_lancer(large, ENERGIE, timedelta(hours=1),
                     maintenant=BASE + timedelta(hours=19),
                     echeance=BASE + timedelta(hours=24))
    assert not d.vaut_le_coup
    assert "pas distinguable de zéro" in d.raison


def test_un_gain_sur_prevision_le_dit_dans_sa_raison():
    """On consent à décider sur une prévision, mais on ne le cache pas."""
    prevue = serie(origine="prevision", marge=0.02)
    d = quand_lancer(prevue, ENERGIE, timedelta(hours=1),
                     maintenant=BASE + timedelta(hours=19),
                     echeance=BASE + timedelta(hours=24))
    assert d.vaut_le_coup
    assert "prévision" in d.raison


def test_lattente_est_rendue_et_lisible():
    d = decalage_a(19)
    assert d.attente == timedelta(hours=4)
    assert "4 h" in d.raison


# --------------------------------------------------------------------------- #
# Chargement
# --------------------------------------------------------------------------- #
def ecrire(tmp_path, points):
    chemin = tmp_path / "reseau.json"
    chemin.write_text(json.dumps({"source": "test", "pas_minutes": 60,
                                  "points": points}), encoding="utf-8")
    return chemin


def test_le_chargement_elargit_les_previsions_et_pas_les_mesures(tmp_path):
    chemin = ecrire(tmp_path, [
        {"debut": BASE.isoformat(), "gco2e_par_kwh": 400.0, "origine": "mesure"},
        {"debut": (BASE + timedelta(hours=1)).isoformat(),
         "gco2e_par_kwh": 400.0, "origine": "prevision"},
    ])
    s = Serie.depuis_json(chemin, marge_prevision=0.25,
                          source_de_la_marge="erreur type du fournisseur")
    assert s.points[0].intensite == Intervalle(400.0, 400.0)
    assert s.points[1].intensite == Intervalle(300.0, 500.0)
    assert "±25 %" in s.source and "erreur type" in s.source


def test_une_marge_sans_source_est_refusee(tmp_path):
    chemin = ecrire(tmp_path, [{"debut": BASE.isoformat(),
                                "gco2e_par_kwh": 400.0, "origine": "mesure"}])
    with pytest.raises(ValueError, match="d'où elle vient"):
        Serie.depuis_json(chemin, marge_prevision=0.25, source_de_la_marge="")


@pytest.mark.parametrize("marge", [-0.1, 1.0, 2.0])
def test_une_marge_hors_bornes_est_refusee(tmp_path, marge):
    chemin = ecrire(tmp_path, [{"debut": BASE.isoformat(),
                                "gco2e_par_kwh": 400.0, "origine": "mesure"}])
    with pytest.raises(ValueError, match="marge"):
        Serie.depuis_json(chemin, marge_prevision=marge, source_de_la_marge="x")


def test_maintenant_utc_porte_bien_un_fuseau():
    assert maintenant_utc().tzinfo is not None
