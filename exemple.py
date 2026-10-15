"""La chaîne entière sur de vrais processus : mesurer, décider, rendre compte.

    python exemple.py

Rien n'est saisi à la main ici. Les jobs sont de vraies commandes, leur
consommation est mesurée pendant qu'elles tournent, et le coût de la porte
elle-même est mesuré comme le reste — parce qu'un outil qui ne se compte pas
facture son fonctionnement au bilan de quelqu'un d'autre.
"""

from __future__ import annotations

import sys
import time

from carbongate.mesure import mesurer
from carbongate.porte import Travail, decider
from carbongate.rapport import rendre
from carbongate.sci import Coefficient, Modele

ANNEE = 365 * 24 * 3600

#: Les coefficients. Chacun porte sa provenance, et le type refuse d'exister
#: sans. Les fourchettes sont larges parce que la réalité l'est : personne ne
#: sait sur quel matériel tourne un exécuteur partagé.
MODELE = Modele(
    watts_par_coeur=Coefficient(
        0.74, 3.50, "W/vCPU",
        "Cloud Carbon Footprint, moyennes x86 au repos et en charge"),
    watts_par_gigaoctet=Coefficient(
        0.30, 0.42, "W/Go",
        "Cloud Carbon Footprint, coefficient mémoire"),
    intensite_reseau=Coefficient(
        380.0, 480.0, "gCO2e/kWh",
        "moyenne annuelle du réseau — à remplacer par une mesure horaire"),
    carbone_incarne_machine=Coefficient(
        1.2e6, 1.8e6, "gCO2e",
        "analyses de cycle de vie de serveurs, fourchette publiée"),
    duree_de_vie=Coefficient(
        3 * ANNEE, 6 * ANNEE, "s",
        "durée d'exploitation d'un serveur : 3 à 6 ans selon l'exploitant"),
    part_de_machine=Coefficient(
        0.20, 0.30, "part",
        "un exécuteur partagé occupe de l'ordre du quart d'une machine"),
)


def travail_factice(nom: str, millisecondes: int, mega_octets: int) -> list[str]:
    """Un job qui consomme de façon reproductible, pour que l'exemple tourne
    partout sans dépendre d'une chaîne d'outils installée."""
    return [sys.executable, "-c",
            f"import time\n"
            f"bloc = bytearray({mega_octets} * 1024 * 1024)\n"
            f"fin = time.monotonic() + {millisecondes} / 1000\n"
            f"x = 0\n"
            f"while time.monotonic() < fin: x += 1\n"
            f"del bloc\n"]


PIPELINE = [
    (Travail("tests-unitaires", ("src/*.py", "tests/*.py")), 900, 120),
    (Travail("lint", ("src/*.py", "pyproject.toml")), 350, 40),
    (Travail("construire-doc", ("docs/*.md",)), 400, 60),
    (Travail("scan-securite", toujours=True), 500, 80),
    (Travail("construire-image", ("Dockerfile", "src/*.py")), 1100, 200),
]

#: Ce que la fusion a changé. Ici : de la documentation, et rien d'autre.
CHANGES = ["docs/guide.md", "docs/index.md"]


def main() -> int:
    debut = time.monotonic()
    print("Mesure des jobs, un par un...", flush=True)

    mesures, observations = {}, {}
    for travail, ms, mo in PIPELINE:
        mesure = mesurer(travail_factice(travail.nom, ms, mo))
        mesures[travail.nom] = mesure
        # Un job dont la mesure porte une réserve n'entre pas dans le bilan.
        # L'exclure est honnête ; lui inventer une consommation ne le serait pas.
        if mesure.utilisable:
            observations[travail.nom] = mesure.observation
        o = mesure.observation
        etat = "ok" if mesure.utilisable else "RÉSERVE"
        print(f"  {travail.nom:<18} {o.secondes_processeur:6.3f} s cpu  "
              f"{o.gigaoctets_secondes:7.4f} Go.s  "
              f"{mesure.echantillons:3d} éch.  {etat}", flush=True)

    # La porte se mesure elle-même : le temps passé à décider est du temps
    # machine comme un autre.
    cout_porte = mesurer([sys.executable, "-c",
                          "import time; time.sleep(0.12)"], periode=0.01)

    decision = decider([t for t, _, _ in PIPELINE], CHANGES,
                       observations, MODELE,
                       cout_porte.observation if cout_porte.utilisable else None)

    print()
    print(rendre(decision, MODELE, mesures))
    print(f"(exemple complet en {time.monotonic() - debut:.1f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
