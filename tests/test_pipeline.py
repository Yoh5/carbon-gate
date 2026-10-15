"""Le pipeline, la configuration de la porte et les modèles de jobs doivent
parler des mêmes jobs — et la décision doit réellement atteindre le pipeline.

Ce fichier a été réécrit après un pipeline **vert** qui ne démontrait rien.

La première version gardait chaque job par `rules: if: $CARBONGATE_X`, une
variable que la porte écrivait dans un artefact dotenv. Or `rules:` est évalué
à la **création** du pipeline, avant que le moindre job ait tourné : le dotenv
arrive toujours trop tard. La porte a écrit
`CARBONGATE_VERIFIER_README=false`, le job a tourné quand même, et les six
jobs sont passés au vert avec la décision de la porte débranchée.

Un pipeline vert peut cacher que le produit entier ne sert à rien. C'est
l'inverse de ce que ce dépôt vend, et c'est pourquoi les contrôles portent
maintenant sur le mécanisme — le pipeline enfant engendré — plutôt que sur la
présence de règles qui semblaient le faire.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML n'est pas une dépendance "
                                          "d'exécution : ce test est en plus")

RACINE = Path(__file__).resolve().parent.parent
PIPELINE = RACINE / ".gitlab-ci.yml"
MODELES = RACINE / ".gitlab" / "jobs.yml"
CONFIG = RACINE / "carbongate.json"


def pipeline() -> dict:
    return yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))


def modeles() -> dict:
    return yaml.safe_load(MODELES.read_text(encoding="utf-8"))


def jobs_de_la_config() -> list[dict]:
    return json.loads(CONFIG.read_text(encoding="utf-8"))["jobs"]


def engendre(fichiers: list[str], connus: bool = True) -> dict:
    """Le pipeline que la porte produirait pour ces fichiers changés."""
    from carbongate.cli import charger_config, pipeline_gitlab
    from carbongate.porte import decider
    travaux, _ = charger_config(CONFIG)
    decision = decider(travaux, fichiers, changements_connus=connus)
    return yaml.safe_load(pipeline_gitlab(decision))


# --------------------------------------------------------------------------- #
# La décision atteint-elle vraiment le pipeline ?
# --------------------------------------------------------------------------- #
def test_un_job_non_retenu_est_absent_du_pipeline_engendre():
    """Le contrôle que l'ancienne version ne pouvait pas faire."""
    enfant = engendre(["tests/test_pipeline.py"])
    assert "tests" in enfant
    assert "verifier-readme" not in enfant, (
        "le job a survécu à une décision qui l'excluait : la décision "
        "n'atteint pas le pipeline")


def test_un_changement_de_readme_ramene_sa_verification():
    assert "verifier-readme" in engendre(["README.md"])


def test_le_job_inconditionnel_est_dans_tous_les_pipelines():
    """`toujours: true` ne souffre aucune exception, y compris quand rien
    n'a changé."""
    inconditionnels = [j["nom"] for j in jobs_de_la_config() if j.get("toujours")]
    assert inconditionnels, "aucun job inconditionnel : le scan peut être sauté"
    for fichiers in ([], ["README.md"], ["docs/x.md"], ["tests/a.py"]):
        enfant = engendre(fichiers)
        for nom in inconditionnels:
            assert nom in enfant, f"{nom} absent pour {fichiers}"


def test_un_changement_inconnu_ramene_tout():
    enfant = engendre([], connus=False)
    for job in jobs_de_la_config():
        assert job["nom"] in enfant


def test_un_pipeline_sans_job_retenu_reste_valide():
    """GitLab refuse un pipeline vide : mieux vaut un job qui le dit qu'une
    erreur de syntaxe que personne ne saura lire."""
    from carbongate.cli import pipeline_gitlab
    from carbongate.porte import Decision
    enfant = yaml.safe_load(pipeline_gitlab(Decision()))
    assert enfant, "le YAML engendré est vide"
    assert any(k not in ("include", "stages") for k in enfant)


# --------------------------------------------------------------------------- #
# Le générateur n'écrit que des noms
# --------------------------------------------------------------------------- #
def test_le_pipeline_engendre_ne_contient_aucune_commande():
    """Un générateur qui produit du shell est un générateur à relire à chaque
    exécution. Les corps vivent dans `.gitlab/jobs.yml`."""
    enfant = engendre(["carbongate/porte.py"])
    for nom, corps in enfant.items():
        if nom in ("include", "stages") or not isinstance(corps, dict):
            continue
        assert set(corps) == {"extends"}, (
            f"{nom} porte autre chose qu'un `extends` : {sorted(corps)}")


def test_chaque_job_declare_a_son_modele():
    """Un `extends` vers un modèle absent fait échouer le pipeline enfant au
    chargement, et le parent ne le découvre qu'à l'exécution."""
    disponibles = set(modeles())
    for job in jobs_de_la_config():
        attendu = f".job-{job['nom']}"
        assert attendu in disponibles, (
            f"carbongate.json déclare {job['nom']!r} mais {attendu} n'existe "
            f"pas dans .gitlab/jobs.yml : {sorted(disponibles)}")


def test_aucun_modele_nest_orphelin():
    """Un modèle que rien n'étend est du travail mort qu'on croit actif."""
    declares = {f".job-{j['nom']}" for j in jobs_de_la_config()}
    for nom in modeles():
        if nom == ".base":
            continue
        assert nom in declares, f"{nom} n'est déclaré dans aucun job"


# --------------------------------------------------------------------------- #
# Le parent
# --------------------------------------------------------------------------- #
def test_le_parent_declenche_lenfant_et_depend_de_lui():
    """Sans `strategy: depend`, un test rouge dans l'enfant laisse le parent
    au vert — le genre de vert que ce fichier existe pour empêcher."""
    declencheur = pipeline()["retenu"]["trigger"]
    assert declencheur["strategy"] == "depend"
    assert declencheur["include"][0] == {"artifact": "retenu.yml", "job": "porte"}


def test_la_porte_publie_le_pipeline_quelle_engendre():
    assert "retenu.yml" in pipeline()["porte"]["artifacts"]["paths"], (
        "l'enfant ne peut pas être déclenché depuis un artefact non publié")


def test_la_porte_nest_jamais_gardee():
    assert "rules" not in pipeline()["porte"]


def test_lhistorique_git_est_demande_en_entier():
    """Sans profondeur, `git diff` ne répond pas et la porte ne saute rien."""
    assert str(pipeline()["variables"]["GIT_DEPTH"]) == "0"


def test_la_porte_installe_git():
    assert "git" in " ".join(pipeline()["porte"].get("before_script") or [])


# --------------------------------------------------------------------------- #
# La chaîne d'outils et le scan
# --------------------------------------------------------------------------- #
def test_la_chaine_doutils_est_mise_a_jour_avant_detre_auditee():
    """`pip-audit` audite l'environnement, pas seulement ce projet.

    Au premier scan réel, l'image embarquait pip 25.0.1, visé par
    PYSEC-2026-1795 et corrigé en 25.3. La réponse a été de mettre à jour ;
    si quelqu'un la remplace par `--ignore-vuln`, ce test tombe.
    """
    assert "--upgrade pip" in " ".join(modeles()[".base"]["before_script"])
    scan = " ".join(modeles()[".job-scan-securite"]["script"])
    assert "--ignore-vuln" not in scan, (
        "une vulnérabilité ignorée est une vulnérabilité présente")


def test_les_jobs_qui_mesurent_installent_de_quoi_tester():
    assert "pytest" in " ".join(modeles()[".base"]["before_script"])


def test_les_mesures_survivent_au_pipeline():
    """Une mesure sert au pipeline suivant : un artefact ne suffirait pas."""
    cache = modeles()[".base"]["cache"]
    assert "carbongate-mesures.json" in cache["paths"]
    assert "$CI_COMMIT_REF_SLUG" in cache["key"]


def test_chaque_job_passe_par_la_mesure():
    """Un job qui tourne sans être mesuré est absent du bilan, donc une
    économie que le rapport ne peut pas chiffrer."""
    for nom, corps in modeles().items():
        if nom == ".base":
            continue
        script = " ".join(corps.get("script") or [])
        assert re.search(r"carbongate mesure --nom \S+", script), (
            f"{nom} ne passe pas par `carbongate mesure`")


def test_la_base_depend_du_contexte_et_pas_du_dernier_push():
    """`CI_COMMIT_BEFORE_SHA` répond à « qu'est-ce que ce push a ajouté ».

    Sur la branche par défaut c'est la bonne question. Sur une branche de
    travail, non : après un rebase, l'ancien sommet n'est plus un ancêtre, et
    le diff ramasse tout ce que la branche par défaut a gagné entre-temps.
    Mesuré ici — un commit ne touchant que le README a relancé les quatre
    jobs, parce que la base était le sommet d'avant le rebase. Avec le point
    de fourche, deux jobs.

    La question qu'une branche doit poser est « qu'est-ce que CETTE branche
    change », et sa réponse est `merge-base`.
    """
    script = chr(10).join(pipeline()["porte"]["script"])
    assert "merge-base" in script, (
        "sans merge-base, une branche rebasée relance tout")
    assert "CI_DEFAULT_BRANCH" in script
    assert "git fetch" in script, (
        "la branche par défaut n'est pas dans un clone de branche")
    assert "0000000000000000000000000000000000000000" in script, (
        "le SHA nul d'un premier push doit encore être reconnu")


def test_les_trois_cas_sont_distingues():
    """Merge request, branche par défaut, branche de travail : trois
    questions différentes, trois bases différentes."""
    script = chr(10).join(pipeline()["porte"]["script"])
    assert script.index("CI_MERGE_REQUEST_DIFF_BASE_SHA") < script.index("merge-base"), (
        "le repli passerait avant la base exacte fournie par GitLab")
    assert "$CI_COMMIT_REF_NAME" in script and "$CI_DEFAULT_BRANCH" in script, (
        "rien ne distingue la branche par défaut d'une branche de travail")
