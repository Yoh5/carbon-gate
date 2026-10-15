"""Le flow Duo doit être valide avant d'être collé dans GitLab, pas après.

Un flow mal formé **échoue au chargement**, pas à l'exécution : le framework
vérifie les composants, les prompts et les routes au démarrage. Découvrir ça
dans l'interface coûte un aller-retour à chaque faute, et le hackathon se
compte en jours.

Les restrictions vérifiées ici viennent de deux sources distinctes, et c'est
pour ça qu'elles sont faciles à confondre : la spécification *flow registry
v1* dit ce qu'un flow peut contenir, et la page *custom flows* retire ensuite
plusieurs champs que la spécification autorise. `name`, `description`,
`product_group`, `model` et `stop` sont valides en v1 et **interdits** ici.
"""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML n'est pas une dépendance "
                                          "d'exécution : ce test est en plus")

FLOW = (Path(__file__).resolve().parent.parent
        / ".gitlab" / "duo" / "flows" / "apres-le-code.yaml")

#: Valides dans la spécification v1, retirés par les custom flows.
INTERDITS_AU_SOMMET = {"name", "description", "product_group"}


@pytest.fixture(scope="module")
def flow() -> dict:
    return yaml.safe_load(FLOW.read_text(encoding="utf-8"))


def test_le_fichier_est_la_ou_le_declencheur_le_cherchera(flow):
    """`Configuration path` pointe sur ce chemin exact dans l'interface."""
    assert FLOW.exists()
    assert FLOW.parent.as_posix().endswith(".gitlab/duo/flows")


def test_les_champs_interdits_en_custom_flow_sont_absents(flow):
    present = INTERDITS_AU_SOMMET & set(flow)
    assert not present, (
        f"{sorted(present)} est valide en v1 mais refusé en custom flow")


def test_lenvironnement_est_ambient(flow):
    """`chat` et `chat-partial` existent en v1 et sont refusés ici."""
    assert flow["version"] == "v1"
    assert flow["environment"] == "ambient"


def test_chaque_composant_reference_un_prompt_qui_existe(flow):
    ids = {p["prompt_id"] for p in flow["prompts"]}
    for composant in flow["components"]:
        assert composant["prompt_id"] in ids, (
            f"{composant['name']} référence {composant['prompt_id']!r}, absent "
            f"de prompts : {sorted(ids)}")


def test_aucun_prompt_nest_orphelin(flow):
    """Un prompt que rien n'appelle est du texte mort qu'on croit actif."""
    utilises = {c["prompt_id"] for c in flow["components"]}
    for prompt in flow["prompts"]:
        assert prompt["prompt_id"] in utilises, (
            f"{prompt['prompt_id']!r} n'est appelé par aucun composant")


def test_chaque_prompt_porte_ses_champs_requis(flow):
    for prompt in flow["prompts"]:
        for champ in ("prompt_id", "name", "unit_primitives", "prompt_template"):
            assert champ in prompt, f"{prompt.get('prompt_id')} : {champ} manquant"
        assert "system" in prompt["prompt_template"]
        assert "user" in prompt["prompt_template"]
        # `extra="forbid"` : une clé inconnue fait échouer le chargement.
        assert "model" not in prompt, "model n'est plus supporté"
        assert "stop" not in (prompt.get("params") or {}), \
            "stop est interdit dans les custom flows"


def test_les_noms_de_composants_nont_ni_deux_points_ni_point(flow):
    for composant in flow["components"]:
        assert ":" not in composant["name"] and "." not in composant["name"]


def test_le_graphe_se_termine_et_ne_laisse_personne_de_cote(flow):
    noms = {c["name"] for c in flow["components"]}
    sources = {r["from"] for r in flow["routers"]}
    cibles = {r["to"] for r in flow["routers"]}
    assert flow["flow"]["entry_point"] in noms
    assert "end" in cibles, "aucune route ne termine le flow"
    assert not noms - sources, f"sans route sortante : {sorted(noms - sources)}"
    inatteignables = noms - (cibles | {flow["flow"]["entry_point"]})
    assert not inatteignables, f"inatteignables : {sorted(inatteignables)}"


# --------------------------------------------------------------------------- #
# Le niveau d'autonomie déclaré doit être celui du fichier
# --------------------------------------------------------------------------- #
def test_aucun_composant_ne_peut_fusionner(flow):
    """Le niveau déclaré doit suivre l'outillage, pas l'inverse.

    `accept_merge_request` existe dans la boîte à outils : le donner à
    `verdict` ferait un flow Hands-off en une ligne. Mais `verdict` est un
    AgentComponent, et laisser un modèle décider d'une fusion est ce que ce
    dépôt refuse partout ailleurs. Supervised est donc la déclaration exacte,
    et ce test tombe si quelqu'un rend la fusion possible sans la redéclarer.
    """
    for composant in flow["components"]:
        assert "accept_merge_request" not in composant.get("toolset", []), (
            f"{composant['name']} peut fusionner : la soumission est déclarée "
            "Supervised, ce qui deviendrait faux")


def test_aucune_approbation_etape_par_etape(flow):
    """Un `HumanInputComponent` demanderait une approbation à chaque étape,
    ce qui serait le niveau Assisted — en dessous de ce qu'on déclare."""
    types = {c["type"] for c in flow["components"]}
    assert "HumanInputComponent" not in types


def test_chaque_composant_declare_son_outillage(flow):
    """Le catalogue l'exige — `toolset: field required` au premier essai — et
    le moindre privilège l'exige aussi."""
    for composant in flow["components"]:
        outils = composant.get("toolset")
        assert outils, f"{composant['name']} n'a pas de toolset"
        assert len(outils) <= 4, (
            f"{composant['name']} en demande {len(outils)} : un agent qui peut "
            "tout faire n'a pas de frontière")


def test_celui_qui_ecrit_ne_lit_rien_et_celui_qui_lit_necrit_rien(flow):
    """La séparation qui rend le commentaire final sûr."""
    par_nom = {c["name"]: c.get("toolset", []) for c in flow["components"]}
    assert par_nom["verdict"] == ["create_merge_request_note"], (
        "le rédacteur du verdict doit pouvoir écrire un commentaire, et rien d'autre")
    for lecteur in ("changement", "revue", "securite", "carbone"):
        assert not any(o.startswith(("create_", "add_", "edit_", "accept_"))
                       for o in par_nom[lecteur]), (
            f"{lecteur} peut écrire : un relecteur n'a aucune raison de le faire")


def test_aucun_tool_name_nest_invente(flow):
    """Un `tool_name` inexistant fait échouer le flow **au chargement**.

    Tant que la liste des outils n'a pas été lue dans l'AI Catalog, aucun
    `DeterministicStepComponent` n'entre ici. Ce test tombe le jour où
    quelqu'un en ajoute un, ce qui est le bon moment pour vérifier le nom.
    """
    for composant in flow["components"]:
        assert "tool_name" not in composant, (
            f"{composant['name']} déclare un tool_name : vérifier qu'il existe "
            "dans l'AI Catalog avant de garder cette ligne")


# --------------------------------------------------------------------------- #
# La règle du projet, portée jusque dans les prompts
# --------------------------------------------------------------------------- #
def prose(flow: dict, prompt_id: str) -> str:
    """Le `system` d'un prompt, sauts de ligne aplatis.

    Les blocs `|` du YAML coupent les phrases où la colonne tombe, pas où le
    sens s'arrête. Chercher une phrase sans aplatir, c'est écrire un test qui
    échoue au prochain reformatage sans que rien n'ait changé.
    """
    prompt = next(p for p in flow["prompts"] if p["prompt_id"] == prompt_id)
    return " ".join(prompt["prompt_template"]["system"].split()).lower()


def test_le_prompt_carbone_interdit_de_recommander_de_sauter(flow):
    """Le carbone rapporte, il n'arbitre pas. Si cette phrase disparaît du
    prompt, le projet a perdu sa règle à l'endroit le plus discret."""
    systeme = prose(flow, "lire_le_rapport_carbone")
    assert "never recommend skipping" in systeme
    assert "interval" in systeme, "rien n'interdit d'aplatir un intervalle"


def test_la_revue_sait_que_sauter_pour_le_prix_est_bloquant(flow):
    assert "never skipped because it is expensive" in prose(
        flow, "revue_de_correction")


def test_le_verdict_ne_peut_pas_annuler_un_blocage(flow):
    assert "never overturn" in prose(flow, "rendre_le_verdict")
