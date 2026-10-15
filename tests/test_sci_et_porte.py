"""Le score SCI, et la porte qui décide.

Deux règles sont éprouvées ici plutôt qu'affirmées :

- **la correction décide, le carbone mesure** — aucun chemin de code ne doit
  permettre au carbone de faire sauter un job ;
- **une économie qu'on ne peut pas distinguer de zéro n'est pas annoncée.**
"""
from __future__ import annotations

import pytest

from carbongate.intervalle import somme
from carbongate.porte import Travail, decider
from carbongate.sci import (Coefficient, Modele, Observation, energie_kwh,
                            incarne_g, operationnel_g, sci_g)

ANNEE = 365 * 24 * 3600


def modele(**surcharges) -> Modele:
    defaut = dict(
        watts_par_coeur=Coefficient(0.74, 3.5, "W/vCPU", "Cloud Carbon Footprint, x86"),
        watts_par_gigaoctet=Coefficient(0.30, 0.42, "W/Go", "Cloud Carbon Footprint"),
        intensite_reseau=Coefficient(380.0, 480.0, "gCO2e/kWh", "moyenne annuelle"),
        carbone_incarne_machine=Coefficient(1.2e6, 1.8e6, "gCO2e", "ACV serveur"),
        duree_de_vie=Coefficient(3 * ANNEE, 6 * ANNEE, "s", "3 à 6 ans"),
        part_de_machine=Coefficient(0.20, 0.30, "part", "quart de machine"),
    )
    return Modele(**{**defaut, **surcharges})


# --------------------------------------------------------------------------- #
# Un coefficient sans source n'est pas une mesure
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("source", ["", "   ", None])
def test_un_coefficient_sans_source_est_refuse(source):
    """La contrainte centrale de `sci.py`, et elle est tenue par le type.

    Un nombre dont on a perdu la provenance ne peut être ni contesté ni mis à
    jour, et un score bâti dessus ne peut pas être refait par quelqu'un d'autre.
    """
    with pytest.raises(ValueError, match="source"):
        Coefficient(1.0, 2.0, "W", source)


def test_un_coefficient_sans_unite_est_refuse():
    with pytest.raises(ValueError, match="[Uu]nité"):
        Coefficient(1.0, 2.0, "", "une source")


def test_les_sources_sont_toutes_recuperables_pour_le_rapport():
    """Six, et non quatre : la durée de vie et la part de machine échappaient
    à la règle « pas de coefficient sans source » alors que ce sont elles qui
    gouvernent le résultat le plus fort du projet."""
    sources = modele().sources()
    assert len(sources) == 6
    assert all("(" in s and ")" in s for s in sources), "chaque ligne cite sa provenance"


# --------------------------------------------------------------------------- #
# Le calcul
# --------------------------------------------------------------------------- #
def test_lenergie_se_calcule_a_la_main():
    """Un cas vérifiable au crayon, pour que le code ne soit pas cru sur parole.

    100 s de processeur à 1 W, aucune mémoire : 100 J, soit 100/3,6e6 kWh.
    """
    m = modele(watts_par_coeur=Coefficient(1.0, 1.0, "W/vCPU", "posé pour le test"),
               watts_par_gigaoctet=Coefficient(0.0, 0.0, "W/Go", "posé pour le test"))
    e = energie_kwh(Observation(100.0, 0.0, 100.0), m)
    assert e.bas == pytest.approx(100 / 3_600_000)
    assert e.haut == pytest.approx(100 / 3_600_000)


def test_le_carbone_incarne_depend_du_temps_et_de_la_part():
    """Occuper un quart de machine n'engage pas le même matériel que la machine."""
    m = modele(carbone_incarne_machine=Coefficient(4e6, 4e6, "gCO2e", "posé"),
               duree_de_vie=Coefficient(ANNEE, ANNEE, "s", "posé"),
               part_de_machine=Coefficient(0.5, 0.5, "part", "posé"))
    # Une heure sur une durée de vie d'un an, à moitié de machine.
    attendu = 4e6 * (3600 / ANNEE) * 0.5
    m_incarne = incarne_g(Observation(0.0, 0.0, 3600.0), m)
    assert m_incarne.bas == pytest.approx(attendu)


def test_une_duree_de_vie_plus_longue_dilue_le_carbone_incarne():
    court = modele(duree_de_vie=Coefficient(2 * ANNEE, 2 * ANNEE, "s", "posé"))
    long = modele(duree_de_vie=Coefficient(8 * ANNEE, 8 * ANNEE, "s", "posé"))
    o = Observation(100.0, 100.0, 60.0)
    assert incarne_g(o, long).milieu == pytest.approx(incarne_g(o, court).milieu / 4)


def test_le_materiel_domine_lelectricite_sur_un_job_de_ci():
    """Le résultat central, et il est éprouvé plutôt qu'affirmé.

    Mesuré le 14/10/2026 sur cinq jobs : 91,8 % du total est du matériel. Le
    basculement est à 2,4 % de machine, très en deçà de toute allocation
    plausible d'un exécuteur partagé.
    """
    o = Observation(0.9, 0.11, 0.95)
    m = modele()
    assert incarne_g(o, m).bas > operationnel_g(o, m).haut, (
        "le matériel doit dominer même dans le cas le plus défavorable")


def test_le_sci_est_la_somme_de_loperationnel_et_de_lincarne():
    o = Observation(420.0, 900.0, 180.0)
    m = modele()
    total = sci_g(o, m)
    attendu_bas = operationnel_g(o, m).bas + incarne_g(o, m).bas
    assert total.bas == pytest.approx(attendu_bas)


def test_lunite_fonctionnelle_divise():
    o, m = Observation(420.0, 900.0, 180.0), modele()
    assert sci_g(o, m, unites=4).bas == pytest.approx(sci_g(o, m).bas / 4)


def test_un_travail_sans_consommation_ne_coute_que_du_temps_machine():
    """Zéro processeur et zéro mémoire laissent quand même le carbone incarné."""
    sci = sci_g(Observation(0.0, 0.0, 600.0), modele())
    assert sci.bas > 0, "le matériel vieillit même quand il ne calcule pas"


@pytest.mark.parametrize("champ", ["secondes_processeur", "gigaoctets_secondes",
                                   "secondes_horloge"])
def test_une_observation_negative_est_refusee(champ):
    args = {"secondes_processeur": 1.0, "gigaoctets_secondes": 1.0,
            "secondes_horloge": 1.0, champ: -1.0}
    with pytest.raises(ValueError, match=champ):
        Observation(**args)


@pytest.mark.parametrize("bas,haut", [(0.0, 0.5), (0.5, 1.5)])
def test_une_part_de_machine_hors_bornes_est_refusee(bas, haut):
    with pytest.raises(ValueError, match="part de machine"):
        modele(part_de_machine=Coefficient(bas, haut, "part", "posé"))


# --------------------------------------------------------------------------- #
# La porte : la correction décide
# --------------------------------------------------------------------------- #
PIPELINE = [
    Travail("tests", ("src/*.py", "tests/*.py")),
    Travail("lint", ("src/*.py",)),
    Travail("docs", ("docs/*.md",)),
    Travail("scan-securite", toujours=True),
    Travail("build-image", ("Dockerfile", "src/*.py")),
]

MESURES = {
    "tests": Observation(420.0, 900.0, 180.0),
    "lint": Observation(30.0, 60.0, 25.0),
    "docs": Observation(55.0, 110.0, 40.0),
    "scan-securite": Observation(140.0, 300.0, 95.0),
    "build-image": Observation(260.0, 700.0, 150.0),
}


def test_un_job_marque_toujours_ne_saute_jamais():
    """Même quand rien de ce qu'il surveille n'a changé, et quel que soit son coût."""
    d = decider(PIPELINE, ["README.md"], MESURES, modele())
    assert "scan-securite" in [t.nom for t in d.a_lancer]


def test_un_job_sans_motif_declare_depend_de_tout():
    """Le défaut sûr : un job oublié qui tourne coûte des grammes, un job
    oublié qui ne tourne pas coûte un incident."""
    d = decider([Travail("mystere")], ["quoi-que-ce-soit.txt"], {}, modele())
    assert [t.nom for t in d.a_lancer] == ["mystere"]


def test_le_carbone_ne_fait_sauter_aucun_job():
    """La règle qui gouverne le projet, éprouvée plutôt qu'affirmée.

    On rend un job absurdement cher et on vérifie que la décision ne bouge pas
    d'un pouce : seuls les fichiers changés la déterminent.
    """
    enorme = dict(MESURES, tests=Observation(1e9, 1e9, 1e9))
    sobre = decider(PIPELINE, ["src/a.py"], MESURES, modele())
    cher = decider(PIPELINE, ["src/a.py"], enorme, modele())
    assert [t.nom for t in sobre.a_lancer] == [t.nom for t in cher.a_lancer]
    assert "tests" in [t.nom for t in cher.a_lancer], "cher, et pourtant lancé"


def test_chaque_job_porte_la_raison_de_son_sort():
    d = decider(PIPELINE, ["docs/guide.md"], MESURES, modele())
    assert "docs/guide.md" in d.motifs["docs"]
    assert "aucun fichier" in d.motifs["tests"]
    assert "toujours" in d.motifs["scan-securite"]


def test_rien_de_change_ne_laisse_tourner_que_les_obligatoires():
    d = decider(PIPELINE, [], MESURES, modele())
    assert [t.nom for t in d.a_lancer] == ["scan-securite"]


# --------------------------------------------------------------------------- #
# La porte : le carbone mesure, et se tait quand il ne sait pas
# --------------------------------------------------------------------------- #
def test_une_economie_franche_est_annoncee():
    d = decider(PIPELINE, ["docs/guide.md"], MESURES, modele())
    assert d.economie_annoncable
    assert "gain net établi" in d.verdict()


def test_leconomie_brute_est_la_somme_des_sautes_et_non_une_difference():
    """Le défaut trouvé en regardant les nombres plutôt que les points verts.

    `complet - retenu` vaut la même chose en arithmétique ordinaire et pas du
    tout en arithmétique d'intervalles : la soustraction ignore que les jobs
    communs aux deux totaux sont les mêmes, donc leur incertitude s'ajoute au
    lieu de s'annuler. Sur un pipeline où un gros job domine, elle rendait
    [-3,59 ; 3,60] g là où l'économie vaut un millième de gramme et dont le
    signe est parfaitement certain.
    """
    d = decider(PIPELINE, ["docs/guide.md"], MESURES, modele())
    attendue = somme(sci_g(MESURES[t.nom], modele()) for t in d.sautes)
    assert d.economie_brute.bas == pytest.approx(attendue.bas)
    assert d.economie_brute.haut == pytest.approx(attendue.haut)

    # La soustraction est strictement plus large. De combien dépend du poids
    # des jobs communs aux deux totaux : ici 1,23 contre 0,74, et [-3,59 ; 3,60]
    # contre un millième de gramme sur le pipeline dominé par un gros job.
    par_difference = d.cout_complet - d.cout_retenu
    assert par_difference.largeur > d.economie_brute.largeur, (
        "la soustraction doit bien être la version trop large")


def test_sans_mesure_rien_nest_affirme_sur_le_carbone():
    d = decider(PIPELINE, ["docs/guide.md"])
    assert d.ecart is None
    assert "rien n'est affirmé" in d.verdict()
    assert [t.nom for t in d.sautes] == ["tests", "lint", "build-image"]


def test_un_job_sans_mesure_ne_cree_pas_deconomie_fantome():
    """Exclu des DEUX totaux : l'écart annoncé est un plancher, pas un espoir."""
    partielles = {k: v for k, v in MESURES.items() if k != "build-image"}
    complet = decider(PIPELINE, ["docs/guide.md"], MESURES, modele())
    partiel = decider(PIPELINE, ["docs/guide.md"], partielles, modele())
    assert "build-image" in partiel.non_mesures
    assert partiel.ecart.haut <= complet.ecart.haut, "ne doit jamais surestimer"
    assert "sans mesure, exclus du bilan" in partiel.verdict()


def test_tout_relancer_nannonce_aucune_economie():
    d = decider(PIPELINE, ["src/a.py", "docs/g.md", "Dockerfile", "tests/t.py"],
                MESURES, modele())
    assert not d.sautes
    assert "aucune économie à annoncer" in d.verdict()


def test_un_petit_job_saute_reste_une_economie_certaine():
    """Petit n'est pas douteux.

    Sauter un job minuscule pendant qu'un job énorme tourne épargne peu, mais
    le signe de ce peu est certain. Le taire serait la faute symétrique de
    celle d'inventer un gain.
    """
    travaux = [Travail("enorme", ("src/*.py",)), Travail("minuscule", ("docs/*.md",))]
    mesures = {"enorme": Observation(5000.0, 9000.0, 1200.0),
               "minuscule": Observation(0.4, 0.8, 0.3)}
    d = decider(travaux, ["src/a.py"], mesures, modele())
    assert [t.nom for t in d.sautes] == ["minuscule"]
    assert d.economie_annoncable
    assert d.ecart.haut < 0.01, "petite, et annoncée comme telle"


def test_une_porte_plus_chere_que_ce_quelle_epargne_lavoue():
    """Décider coûte. Si ça coûte plus que ça n'épargne, il faut le dire.

    C'est le seul endroit où l'incertitude a vraiment un mot à dire, parce que
    les deux grandeurs comparées sont cette fois indépendantes : ce qu'on
    épargne et ce que la porte consomme n'ont aucun job en commun.
    """
    travaux = [Travail("enorme", ("src/*.py",)), Travail("minuscule", ("docs/*.md",))]
    mesures = {"enorme": Observation(5000.0, 9000.0, 1200.0),
               "minuscule": Observation(0.4, 0.8, 0.3)}
    porte_gloutonne = Observation(90.0, 200.0, 45.0)
    d = decider(travaux, ["src/a.py"], mesures, modele(), porte_gloutonne)
    assert not d.economie_annoncable
    assert d.ecart.surcout_etabli
    assert "coûte plus qu'elle n'épargne" in d.verdict()


def test_une_porte_du_meme_ordre_que_leconomie_ne_conclut_pas():
    """Et quand les deux se recouvrent, la porte se tait sur le gain net."""
    travaux = [Travail("a", ("src/*.py",)), Travail("b", ("docs/*.md",))]
    mesures = {"a": Observation(100.0, 200.0, 60.0), "b": Observation(100.0, 200.0, 60.0)}
    d = decider(travaux, ["src/a.py"], mesures, modele(),
                Observation(100.0, 200.0, 60.0))
    assert d.ecart.contient_zero
    assert "Aucun gain net n'est annoncé" in d.verdict()
    assert "c'est le changement qui le justifie" in d.verdict()


def test_le_verdict_ne_donne_jamais_de_pourcentage():
    """Un pourcentage demanderait de diviser deux encadrements corrélés.

    L'arithmétique d'intervalles rend alors une plage si large qu'elle ne dit
    plus rien — et prendre les milieux pour la calculer serait exactement le
    mensonge que ce projet refuse. Le verdict reste en grammes absolus.
    """
    d = decider(PIPELINE, ["docs/guide.md"], MESURES, modele())
    assert "%" not in d.verdict()


# --------------------------------------------------------------------------- #
# Le rapport — non couvert jusqu'au 14/10/2026, et c'est lui qui a cassé
# --------------------------------------------------------------------------- #
def test_le_rapport_se_rend_et_cite_toutes_ses_sources():
    """Aucun test n'exerçait `rendre`, donc la suite est restée verte pendant
    que `python exemple.py` levait une AttributeError. Un module qu'aucun test
    n'appelle n'est pas couvert par la confiance que les autres inspirent."""
    from carbongate.rapport import rendre
    d = decider(PIPELINE, ["docs/guide.md"], MESURES, modele())
    texte = rendre(d, modele())
    assert "ISO/IEC 21031:2024" in texte
    for source in modele().sources():
        # Chaque provenance doit se retrouver dans le texte, repliage compris.
        assert source.split("(")[-1].rstrip(")")[:30] in " ".join(texte.split())
    assert "GAIN NET" in texte and "Verdict" in texte


def test_le_rapport_dit_ce_quil_ne_couvre_pas():
    partielles = {k: v for k, v in MESURES.items() if k != "build-image"}
    from carbongate.rapport import rendre
    d = decider(PIPELINE, ["docs/guide.md"], partielles, modele())
    texte = rendre(d, modele())
    assert "ne couvre pas" in texte and "build-image" in texte


def test_le_rapport_sans_mesure_ne_pretend_rien():
    from carbongate.rapport import rendre
    texte = rendre(decider(PIPELINE, ["docs/guide.md"]), modele())
    assert "rien n'est affirmé" in texte
    assert "GAIN NET" not in texte
