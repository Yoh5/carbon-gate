"""Le pipeline et la configuration de la porte doivent parler des mêmes jobs.

`.gitlab-ci.yml` garde chaque job par une variable, et `carbongate.json`
décide de la valeur de cette variable. Les deux fichiers portent donc le même
nom de job, écrit deux fois — et deux écritures du même nom finissent toujours
par diverger.

La divergence est silencieuse et penche du mauvais côté : un job dont la
variable n'est jamais posée reste indéfini, la règle `== "true"` est fausse,
et **le job ne tourne pas**. Pas d'erreur, pas de rouge ; juste une
vérification qui a disparu du pipeline sans que personne le demande.

C'est exactement le défaut que ce projet existe pour empêcher — sauter du
travail pour une raison qui n'est pas « rien n'a changé ».
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
CONFIG = RACINE / "carbongate.json"


def pipeline() -> dict:
    return yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))


def jobs_du_pipeline() -> dict[str, dict]:
    d = pipeline()
    return {k: v for k, v in d.items()
            if k not in ("stages", "variables", "default")
            and not k.startswith(".") and isinstance(v, dict)}


def jobs_de_la_config() -> list[dict]:
    return json.loads(CONFIG.read_text(encoding="utf-8"))["jobs"]


def variable_gardee(job: dict) -> str | None:
    for regle in job.get("rules") or []:
        trouve = re.search(r"\$(CARBONGATE_[A-Z0-9_]+)", regle.get("if", ""))
        if trouve:
            return trouve.group(1)
    return None


def test_chaque_garde_du_pipeline_correspond_a_un_job_declare():
    """Une garde sans job derrière est une vérification qui ne tournera plus."""
    from carbongate.cli import variable
    declarees = {variable(j["nom"]) for j in jobs_de_la_config()}
    for nom, job in jobs_du_pipeline().items():
        gardee = variable_gardee(job)
        if gardee is None:
            continue
        assert gardee in declarees, (
            f".gitlab-ci.yml garde {nom} par {gardee}, que carbongate.json ne "
            f"pose jamais : ce job ne tournera plus jamais. Déclarées : "
            f"{sorted(declarees)}")


def test_chaque_job_declare_a_bien_un_job_dans_le_pipeline():
    """L'inverse : un job chiffré mais inexistant gonfle l'économie annoncée."""
    noms_pipeline = set(jobs_du_pipeline())
    for job in jobs_de_la_config():
        assert job["nom"] in noms_pipeline, (
            f"carbongate.json chiffre {job['nom']!r}, absent de .gitlab-ci.yml : "
            "le rapport compterait une économie sur un job qui n'existe pas")


def test_le_job_toujours_na_pas_de_garde_dans_le_pipeline():
    """`toujours: true` et une règle `if` seraient deux avis contradictoires,
    et c'est la règle du pipeline qui gagnerait."""
    inconditionnels = {j["nom"] for j in jobs_de_la_config() if j.get("toujours")}
    assert inconditionnels, "aucun job inconditionnel : le scan peut être sauté"
    for nom in inconditionnels:
        job = jobs_du_pipeline().get(nom)
        assert job is not None
        assert variable_gardee(job) is None, (
            f"{nom} est déclaré toujours exécuté mais le pipeline le garde "
            f"derrière {variable_gardee(job)}")


def test_la_porte_elle_meme_nest_jamais_gardee():
    """Si la porte était gardée par sa propre sortie, rien ne tournerait."""
    assert variable_gardee(jobs_du_pipeline()["porte"]) is None


def test_lhistorique_git_est_demande_en_entier():
    """Sans profondeur, `git diff` ne répond pas et la porte ne saute rien —
    sûr, mais le pipeline ne démontrerait plus rien."""
    assert str(pipeline()["variables"]["GIT_DEPTH"]) == "0"


def test_les_mesures_survivent_au_pipeline():
    """Une mesure sert au pipeline suivant : un artefact ne suffirait pas."""
    cache = pipeline()["default"]["cache"]
    assert "carbongate-mesures.json" in cache["paths"]
    assert "$CI_COMMIT_REF_SLUG" in cache["key"]


def test_les_gardes_sont_a_sur_defaut():
    """Un job tourne SAUF si la porte a dit non, jamais l'inverse.

    La première version gardait par `== "true"`. La première exécution réelle
    sur GitLab a montré le coût : `porte` est morte — git n'est pas dans
    `python:3.12-slim` — donc le dotenv n'a jamais existé, les variables sont
    restées vides, et les trois jobs gardés ont **disparu du pipeline**. Pas
    sautés : absents. Une porte en panne effaçait la vérification.

    `!= "false"` fait qu'une variable absente laisse le job tourner. C'est la
    même distinction que `changements_connus` côté code : « non » et « on ne
    sait pas » ne donnent pas la même décision.
    """
    for nom, job in jobs_du_pipeline().items():
        for regle in job.get("rules") or []:
            condition = regle.get("if", "")
            if "CARBONGATE_" not in condition:
                continue
            assert '!= "false"' in condition, (
                f'{nom} est gardé par {condition!r} : si la porte échoue, la '
                'variable est vide et le job disparaît au lieu de tourner')


def test_la_porte_installe_ce_dont_elle_a_besoin():
    """Elle interroge git ; l'image Python mince ne l'embarque pas."""
    preparation = " ".join(pipeline()["porte"].get("before_script") or [])
    assert "git" in preparation, (
        "sans git, la porte bascule sur son repli et ne saute plus rien")


def test_le_scan_ne_depend_daucun_etage_precedent():
    """Le seul job que ce projet jure de ne jamais sauter doit aussi survivre
    à l'échec d'un autre.

    GitLab marque **Skipped** tout un étage dès qu'un étage précédent échoue.
    Au premier pipeline réel, `lint` a échoué et l'historique affichait
    « scan-securite : Skipped » — la thèse du projet contredite à l'écran, là
    où un juré regarde. `needs: []` détache le job de l'ordre des étages.
    """
    scan = jobs_du_pipeline()["scan-securite"]
    assert scan.get("needs") == [], (
        "sans needs: [], un échec en amont fait apparaître le scan comme "
        "sauté, ce que ce dépôt affirme impossible")


def test_les_jobs_qui_mesurent_installent_de_quoi_tester():
    """`pip install -e .` n'installe pas pytest : le projet n'a aucune
    dépendance d'exécution, et c'est délibéré."""
    preparation = " ".join(pipeline()[".mesure"]["before_script"])
    assert "pytest" in preparation


def test_la_chaine_doutils_est_mise_a_jour_avant_detre_auditee():
    """`pip-audit` audite l'environnement, pas seulement ce projet.

    Un dépôt sans aucune dépendance d'exécution peut donc échouer sur sa
    propre chaîne d'outils, et c'est ce qui est arrivé au premier scan réel :
    l'image embarque pip 25.0.1, visé par PYSEC-2026-1795 et corrigé en 25.3.

    La réponse a été de mettre à jour. Si quelqu'un remplace un jour cette
    ligne par `--ignore-vuln`, ce test tombe — et faire taire un scan la
    première fois qu'il parle est la façon la plus sûre de n'en plus rien
    tirer ensuite.
    """
    preparation = pipeline()[".mesure"]["before_script"]
    assert any("--upgrade pip" in ligne for ligne in preparation), (
        "la chaîne d'outils n'est pas mise à jour avant l'audit")
    scan = " ".join(pipeline()["scan-securite"]["script"])
    assert "--ignore-vuln" not in scan, (
        "une vulnérabilité ignorée est une vulnérabilité présente")
