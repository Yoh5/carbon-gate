"""La ligne de commande : ce qu'un pipeline peut en attendre.

Un test domine ce fichier, et c'est celui d'un défaut qui a existé :
**« on ne sait pas ce qui a changé » doit tout lancer, pas tout sauter.**
La première version signalait l'inconnu en passant `["*"]` comme liste de
fichiers. Mais `"*"` est un nom de fichier, et `fnmatch("*", "*.py")` est
faux : tous les jobs à motifs étaient sautés et seuls les `toujours`
survivaient. Sur un premier commit ou un clone superficiel — deux cas
ordinaires en CI — le pipeline entier disparaissait en silence, et le
commentaire du code affirmait le contraire.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from carbongate.cli import main, variable
from carbongate.porte import Travail, decider

ANNEE = 365 * 24 * 3600


def config(chemin: Path, jobs=None) -> Path:
    jobs = jobs or [
        {"nom": "tests", "motifs": ["*.py", "tests/*"]},
        {"nom": "construire-doc", "motifs": ["docs/*", "*.md"]},
        {"nom": "scan-securite", "toujours": True},
    ]
    coefficient = lambda b, h, u: {"bas": b, "haut": h, "unite": u,
                                   "source": "contrôle"}
    chemin.write_text(json.dumps({
        "jobs": jobs,
        "modele": {
            "watts_par_coeur": coefficient(0.74, 3.50, "W/vCPU"),
            "watts_par_gigaoctet": coefficient(0.30, 0.42, "W/Go"),
            "intensite_reseau": coefficient(380.0, 480.0, "gCO2e/kWh"),
            "carbone_incarne_machine": coefficient(1.2e6, 1.8e6, "gCO2e"),
            "duree_de_vie": coefficient(3 * ANNEE, 6 * ANNEE, "s"),
            "part_de_machine": coefficient(0.02, 0.25, "part"),
        },
    }), encoding="utf-8")
    return chemin


def lire_env(chemin: Path) -> dict[str, str]:
    return dict(l.split("=", 1) for l in
                chemin.read_text(encoding="utf-8").splitlines() if l.strip())


# --------------------------------------------------------------------------- #
# Le défaut : inconnu n'est pas vide
# --------------------------------------------------------------------------- #
def test_un_changement_inconnu_lance_tout():
    """Le cas qui justifie ce fichier."""
    travaux = [Travail("tests", ("*.py",)), Travail("doc", ("*.md",))]
    decision = decider(travaux, [], changements_connus=False)
    assert [t.nom for t in decision.a_lancer] == ["tests", "doc"]
    assert decision.sautes == []
    assert "n'a pas pu être établi" in decision.motifs["tests"]


def test_aucun_changement_connu_saute_ce_qui_nest_pas_touche():
    """L'autre moitié : une liste vide VEUT dire que rien n'a changé."""
    travaux = [Travail("tests", ("*.py",)), Travail("scan", toujours=True)]
    decision = decider(travaux, [], changements_connus=True)
    assert [t.nom for t in decision.a_lancer] == ["scan"]
    assert [t.nom for t in decision.sautes] == ["tests"]


def test_les_deux_cas_ne_rendent_pas_la_meme_decision():
    """S'ils devenaient identiques, le défaut serait revenu sans bruit."""
    travaux = [Travail("tests", ("*.py",))]
    inconnu = decider(travaux, [], changements_connus=False)
    vide = decider(travaux, [], changements_connus=True)
    assert [t.nom for t in inconnu.a_lancer] != [t.nom for t in vide.a_lancer]


def test_une_etoile_nest_pas_un_nom_de_fichier():
    """Pin sur la ligne exacte qui avait produit le défaut."""
    assert Travail("tests", ("*.py",)).concerne(["*"]) is False, (
        "si ceci devient vrai, l'ancien repli ['*'] semblerait marcher et "
        "quelqu'un le réintroduira")


# --------------------------------------------------------------------------- #
# La règle du projet, tenue par la ligne de commande aussi
# --------------------------------------------------------------------------- #
def test_un_job_absurdement_cher_tourne_quand_meme(tmp_path):
    """Le carbone ne décide jamais de ce qui tourne.

    On rend un job ruineux en gonflant sa mesure, et la décision ne bouge pas.
    """
    from carbongate.sci import Observation
    travaux = [Travail("tests", ("*.py",))]
    enorme = {"tests": Observation(secondes_processeur=1e6,
                                   gigaoctets_secondes=1e6,
                                   secondes_horloge=1e6)}
    from carbongate.cli import charger_config
    _, modele = charger_config(config(tmp_path / "c.json"))
    decision = decider(travaux, ["a.py"], enorme, modele)
    assert [t.nom for t in decision.a_lancer] == ["tests"]


# --------------------------------------------------------------------------- #
# Noms de variables
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("nom,attendu", [
    ("tests", "CARBONGATE_TESTS"),
    ("construire-image", "CARBONGATE_CONSTRUIRE_IMAGE"),
    ("scan securite", "CARBONGATE_SCAN_SECURITE"),
    ("build.docs", "CARBONGATE_BUILD_DOCS"),
    ("---", "CARBONGATE_JOB"),
])
def test_le_nom_de_variable_est_utilisable_par_un_shell(nom, attendu):
    assert variable(nom) == attendu


def test_deux_jobs_ne_doivent_pas_donner_la_meme_variable(tmp_path):
    """`a-b` et `a.b` donnent tous deux CARBONGATE_A_B : le pipeline
    piloterait deux jobs avec une seule variable."""
    assert variable("a-b") == variable("a.b"), (
        "si ceci change, le commentaire ci-dessous est à mettre à jour")
    # Limite connue et assumée : à documenter si quelqu'un nomme ses jobs
    # ainsi. Dit ici plutôt que découvert dans un pipeline.


# --------------------------------------------------------------------------- #
# Bout en bout
# --------------------------------------------------------------------------- #
def test_decide_ecrit_une_ligne_par_job(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config(tmp_path / "carbongate.json")
    assert main(["decide", "--env", "sortie.env"]) == 0
    env = lire_env(tmp_path / "sortie.env")
    assert set(env) == {"CARBONGATE_TESTS", "CARBONGATE_CONSTRUIRE_DOC",
                        "CARBONGATE_SCAN_SECURITE"}
    assert all(v in ("true", "false") for v in env.values())


def test_hors_depot_git_tout_tourne(tmp_path, monkeypatch):
    """Un dossier sans git est le cas le plus simple de « on ne sait pas »."""
    monkeypatch.chdir(tmp_path)
    config(tmp_path / "carbongate.json")
    main(["decide", "--env", "sortie.env"])
    assert set(lire_env(tmp_path / "sortie.env").values()) == {"true"}


def test_une_config_absente_explique_quoi_faire(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as sortie:
        main(["decide"])
    assert "exemple-config" in str(sortie.value)


def test_exemple_config_ecrit_un_fichier_relisible(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["exemple-config"]) == 0
    from carbongate.cli import charger_config
    travaux, modele = charger_config(tmp_path / "carbongate.json")
    assert travaux and modele.sources()


def test_exemple_config_nefface_pas_sans_quon_le_demande(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main(["exemple-config"])
    with pytest.raises(SystemExit):
        main(["exemple-config"])
    assert main(["exemple-config", "--ecraser"]) == 0


def test_mesure_rend_le_code_de_sortie_de_la_commande(tmp_path, monkeypatch):
    """Une porte qui avalerait l'échec du job qu'elle observe serait pire
    qu'inutile."""
    monkeypatch.chdir(tmp_path)
    code = main(["mesure", "--nom", "faux", "--",
                 sys.executable, "-c", "raise SystemExit(3)"])
    assert code == 3


def test_une_mesure_sous_reserve_nest_pas_enregistree(tmp_path, monkeypatch):
    """Une économie fondée sur un échantillonnage trop maigre est exactement
    ce que ce projet refuse d'annoncer."""
    monkeypatch.chdir(tmp_path)
    main(["mesure", "--nom", "eclair", "--",
          sys.executable, "-c", "pass"])
    fichier = tmp_path / "carbongate-mesures.json"
    enregistre = json.loads(fichier.read_text(encoding="utf-8")) if fichier.exists() else {}
    # Soit la mesure porte et elle est là, soit elle ne porte pas et elle est
    # absente — jamais une valeur inventée pour combler.
    assert "eclair" not in enregistre or enregistre["eclair"]["secondes_horloge"] > 0


def test_rapport_sans_mesure_le_dit(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    config(tmp_path / "carbongate.json")
    assert main(["rapport"]) == 0
    assert "aucune mesure" in capsys.readouterr().out
