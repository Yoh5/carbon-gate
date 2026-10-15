"""La porte, appelable depuis un pipeline.

Jusqu'ici `carbongate` était une bibliothèque : elle savait décider et ne
savait pas être appelée. `exemple.py` faisait la démonstration sur de vrais
processus, mais aucun pipeline ne pouvait s'en servir sans écrire du Python.

Trois sous-commandes, et rien de plus :

    python -m carbongate decide    qui tourne, qui ne tourne pas, et ce que ça épargne
    python -m carbongate mesure -- <commande>   lance et enregistre ce qu'elle a coûté
    python -m carbongate rapport   relit les mesures et rend le bilan

## Comment un pipeline s'en sert vraiment

`decide` écrit un fichier d'environnement — une ligne `CARBONGATE_<JOB>=true`
ou `false` par job. GitLab le lit en artefact `dotenv`, et chaque job porte
une règle `if: $CARBONGATE_TESTS == "true"`. Le job sauté **apparaît comme
sauté dans l'historique du pipeline** au lieu de disparaître, ce qui est la
moitié de l'intérêt : une économie qu'on ne voit pas n'est pas vérifiable.

## Ce que ce module refuse de faire

**Il ne saute rien à cause du carbone.** La décision vient de `porte.decider`,
qui regarde ce qui a changé ; le carbone ne fait que chiffrer après coup. Le
jour où quelqu'un ajoutera un seuil d'émission ici, le test
`test_cli.py::test_un_job_absurdement_cher_tourne_quand_meme` tombera.

**Il n'invente pas de mesure.** Un job dont on n'a pas de relevé est compté
dans les deux totaux, donc il ne rapporte rien. Le fichier de mesures est
produit par `mesure`, jamais par une estimation.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from .intervalle import Intervalle
from .mesure import mesurer
from .porte import Travail, decider
from .rapport import rendre
from .sci import Coefficient, Modele, Observation

CONFIG = "carbongate.json"
MESURES = "carbongate-mesures.json"
SORTIE_ENV = "carbongate.env"


# ── Configuration ────────────────────────────────────────────────────────
def charger_config(chemin: Path) -> tuple[list[Travail], Modele]:
    """Les jobs et le modèle, lus dans un fichier que l'équipe possède.

    Pas de valeurs par défaut pour les coefficients : `Coefficient` lève si la
    source manque, et c'est voulu jusqu'ici. Un modèle livré avec le code
    serait un modèle que personne ne relit.
    """
    if not chemin.exists():
        raise SystemExit(
            f"{chemin} est introuvable. Ce fichier déclare les jobs, ce dont "
            "chacun dépend, et les coefficients avec leurs sources. "
            "`python -m carbongate exemple-config` en écrit un à remplir.")
    brut = json.loads(chemin.read_text(encoding="utf-8"))

    travaux = [Travail(nom=j["nom"],
                       motifs=tuple(j.get("motifs", ())),
                       toujours=bool(j.get("toujours", False)))
               for j in brut["jobs"]]

    def coefficient(cle: str) -> Coefficient:
        c = brut["modele"][cle]
        return Coefficient(float(c["bas"]), float(c["haut"]), c["unite"],
                           c["source"])

    modele = Modele(
        watts_par_coeur=coefficient("watts_par_coeur"),
        watts_par_gigaoctet=coefficient("watts_par_gigaoctet"),
        intensite_reseau=coefficient("intensite_reseau"),
        carbone_incarne_machine=coefficient("carbone_incarne_machine"),
        duree_de_vie=coefficient("duree_de_vie"),
        part_de_machine=coefficient("part_de_machine"),
    )
    return travaux, modele


def charger_mesures(chemin: Path) -> dict[str, Observation]:
    """Ce que chaque job a coûté la dernière fois qu'il a tourné.

    Un fichier absent n'est pas une erreur : au premier passage personne n'a
    encore rien mesuré, et la porte décide très bien sans chiffrer.
    """
    if not chemin.exists():
        return {}
    brut = json.loads(chemin.read_text(encoding="utf-8"))
    return {nom: Observation(**valeurs) for nom, valeurs in brut.items()}


# ── Fichiers changés ─────────────────────────────────────────────────────
def fichiers_changes(reference: str | None) -> tuple[list[str], bool]:
    """Ce que ce changement touche, demandé à git.

    Rend `(fichiers, connus)`. `connus` est faux quand git n'a pas pu
    répondre, et ce n'est pas la même chose qu'une liste vide : une liste vide
    veut dire « rien n'a changé », donc tout peut être sauté. Les confondre
    saute un pipeline entier sur un clone superficiel.

    `reference` est la base de comparaison : sur GitLab,
    `$CI_MERGE_REQUEST_DIFF_BASE_SHA` pour une merge request, sinon
    `$CI_COMMIT_BEFORE_SHA`. Sans référence, le dernier commit — ce qui est
    le bon défaut en local et le mauvais en CI, donc le pipeline la passe.
    """
    commande = (["git", "diff", "--name-only", f"{reference}...HEAD"]
                if reference else ["git", "diff", "--name-only", "HEAD~1", "HEAD"])
    fini = subprocess.run(commande, capture_output=True, text=True)
    if fini.returncode != 0:
        # Un premier commit n'a pas de HEAD~1, et un clone superficiel n'a pas
        # la base. Tout faire tourner est le repli sûr : se tromper en
        # lançant coûte des grammes, se tromper en sautant coûte un incident.
        print(f"  git n'a pas pu lister les changements "
              f"({fini.stderr.strip().splitlines()[0][:80] if fini.stderr.strip() else '?'}) :"
              " tous les jobs tournent", file=sys.stderr)
        return [], False
    return [l.strip() for l in fini.stdout.splitlines() if l.strip()], True


# ── Nom de variable ──────────────────────────────────────────────────────
def variable(nom_du_job: str) -> str:
    """`construire-image` devient `CARBONGATE_CONSTRUIRE_IMAGE`.

    Un nom de job est libre ; un nom de variable d'environnement ne l'est pas.
    La conversion est ici, une seule fois, pour que le `.gitlab-ci.yml` et ce
    module ne puissent pas diverger sur la casse ou les tirets.
    """
    propre = re.sub(r"[^A-Za-z0-9]+", "_", nom_du_job).strip("_").upper()
    return f"CARBONGATE_{propre or 'JOB'}"


# ── Sous-commandes ───────────────────────────────────────────────────────
def commande_decide(args: argparse.Namespace) -> int:
    travaux, modele = charger_config(Path(args.config))
    mesures = charger_mesures(Path(args.mesures))
    changes, connus = fichiers_changes(args.depuis)

    print(f"{len(changes)} fichier(s) changé(s)"
          + (f" depuis {args.depuis}" if args.depuis else "")
          if connus else "ce qui a changé n'a pas pu être établi")
    decision = decider(travaux, changes, mesures or None,
                       modele if mesures else None,
                       changements_connus=connus)

    lignes = [f"{variable(t.nom)}={'true' if t in decision.a_lancer else 'false'}"
              for t in travaux]
    Path(args.env).write_text("\n".join(lignes) + "\n", encoding="utf-8")

    print(rendre(decision, modele, None))
    print(f"\n{args.env} écrit :")
    for ligne in lignes:
        print(f"  {ligne}")
    return 0


def commande_mesure(args: argparse.Namespace) -> int:
    if not args.commande:
        raise SystemExit("rien à mesurer : `mesure --nom tests -- pytest -q`")
    mesure = mesurer(args.commande)

    chemin = Path(args.mesures)
    tout: dict[str, Any] = (json.loads(chemin.read_text(encoding="utf-8"))
                            if chemin.exists() else {})
    if mesure.reserves:
        # Une mesure sous réserve n'entre pas dans le fichier : elle servirait
        # de base à une économie annoncée, et une économie fondée sur un
        # échantillonnage trop maigre est exactement ce que ce projet refuse.
        for reserve in mesure.reserves:
            print(f"  réserve : {reserve}", file=sys.stderr)
        print(f"  {args.nom} n'est pas enregistré : la mesure ne porte pas.",
              file=sys.stderr)
    else:
        tout[args.nom] = asdict(mesure.observation)
        chemin.write_text(json.dumps(tout, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
        o = mesure.observation
        print(f"  {args.nom} : {o.secondes_processeur:.3f} s processeur, "
              f"{o.gigaoctets_secondes:.4f} Go·s, {o.secondes_horloge:.3f} s "
              f"d'horloge, {mesure.echantillons} échantillons")
    # Le code de sortie est celui de la commande mesurée : une porte qui
    # avalerait l'échec du job qu'elle observe serait pire qu'inutile.
    return mesure.code_de_sortie


def commande_rapport(args: argparse.Namespace) -> int:
    travaux, modele = charger_config(Path(args.config))
    mesures = charger_mesures(Path(args.mesures))
    if not mesures:
        print(f"aucune mesure dans {args.mesures} : rien à rapporter.")
        return 0
    decision = decider(travaux, ["*"], mesures, modele)
    print(rendre(decision, modele, None))
    return 0


def commande_exemple_config(args: argparse.Namespace) -> int:
    """Un squelette à remplir — avec les sources, jamais sans."""
    chemin = Path(args.config)
    if chemin.exists() and not args.ecraser:
        raise SystemExit(f"{chemin} existe déjà ; --ecraser pour le remplacer.")
    ANNEE = 365 * 24 * 3600
    modele = {
        "watts_par_coeur": {"bas": 0.74, "haut": 3.50, "unite": "W/vCPU",
                            "source": "Cloud Carbon Footprint, moyennes x86 au repos et en charge"},
        "watts_par_gigaoctet": {"bas": 0.30, "haut": 0.42, "unite": "W/Go",
                                "source": "Cloud Carbon Footprint, coefficient mémoire"},
        "intensite_reseau": {"bas": 380.0, "haut": 480.0, "unite": "gCO2e/kWh",
                             "source": "moyenne annuelle du réseau — à remplacer par une mesure horaire"},
        "carbone_incarne_machine": {"bas": 1.2e6, "haut": 1.8e6, "unite": "gCO2e",
                                    "source": "analyses de cycle de vie de serveurs, fourchette publiée"},
        "duree_de_vie": {"bas": 3 * ANNEE, "haut": 6 * ANNEE, "unite": "s",
                         "source": "durée d'amortissement usuelle d'un serveur, 3 à 6 ans"},
        "part_de_machine": {"bas": 0.02, "haut": 0.25, "unite": "part",
                            "source": "part d'un exécuteur partagé attribuée à un job — à mesurer chez soi"},
    }
    squelette = {
        "_lisez_moi": ("Chaque coefficient porte sa source : le type lève si "
                       "elle manque. Remplacez-les par les vôtres plutôt que "
                       "de garder celles-ci, qui sont des ordres de grandeur."),
        "jobs": [
            {"nom": "tests", "motifs": ["*.py", "tests/*"]},
            {"nom": "lint", "motifs": ["*.py"]},
            {"nom": "construire-doc", "motifs": ["docs/*", "*.md"]},
            {"nom": "scan-securite", "toujours": True},
        ],
        "modele": modele,
    }
    chemin.write_text(json.dumps(squelette, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    print(f"{chemin} écrit. Remplacez les sources par les vôtres avant de vous "
          "en servir pour annoncer quoi que ce soit.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="carbongate", description=__doc__.split("\n")[0])
    sous = ap.add_subparsers(dest="quoi", required=True)

    d = sous.add_parser("decide", help="qui tourne, et ce que sauter épargne")
    d.add_argument("--config", default=CONFIG)
    d.add_argument("--mesures", default=MESURES)
    d.add_argument("--env", default=SORTIE_ENV,
                   help="fichier dotenv pour le pipeline")
    d.add_argument("--depuis", default=None,
                   help="base de comparaison git (ex. $CI_MERGE_REQUEST_DIFF_BASE_SHA)")
    d.set_defaults(fonction=commande_decide)

    m = sous.add_parser("mesure", help="lance une commande et enregistre son coût")
    m.add_argument("--nom", required=True)
    m.add_argument("--mesures", default=MESURES)
    m.add_argument("commande", nargs=argparse.REMAINDER)
    m.set_defaults(fonction=commande_mesure)

    r = sous.add_parser("rapport", help="le bilan des mesures enregistrées")
    r.add_argument("--config", default=CONFIG)
    r.add_argument("--mesures", default=MESURES)
    r.set_defaults(fonction=commande_rapport)

    e = sous.add_parser("exemple-config", help="écrit un carbongate.json à remplir")
    e.add_argument("--config", default=CONFIG)
    e.add_argument("--ecraser", action="store_true")
    e.set_defaults(fonction=commande_exemple_config)

    args = ap.parse_args(argv)
    if args.quoi == "mesure" and args.commande and args.commande[0] == "--":
        args.commande = args.commande[1:]
    return args.fonction(args)
