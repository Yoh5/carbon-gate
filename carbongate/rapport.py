"""Le rapport : ce qui a été décidé, ce que ça a coûté, et d'où viennent les nombres.

Un rapport de carbone qui ne cite pas ses coefficients n'est pas vérifiable, et
ce qui n'est pas vérifiable n'est pas une mesure. Les sources ne sont donc pas
une annexe facultative : `rendre` les écrit toujours, et il n'existe aucun
chemin de code qui produise le chiffre sans elles.

Même règle pour les réserves. Un job dont la mesure a échoué, une plateforme
sans sonde, un échantillonnage trop maigre : tout cela figure dans le rapport,
parce qu'un bilan dont on ignore la couverture n'est pas un bilan.
"""

from __future__ import annotations

from .mesure import Mesure
from .porte import Decision
from .sci import Modele


def rendre(decision: Decision, modele: Modele,
           mesures: dict[str, Mesure] | None = None, *,
           largeur: int = 78) -> str:
    """Le rapport entier, en texte, prêt pour un journal de pipeline."""
    lignes: list[str] = []

    def titre(texte: str) -> None:
        lignes.append("")
        lignes.append(texte)
        lignes.append("-" * min(len(texte), largeur))

    lignes.append("=" * largeur)
    lignes.append("CARBON GATE — ce qui tourne, et ce que ça coûte")
    lignes.append("=" * largeur)

    titre("Verdict")
    lignes.extend(_replier(decision.verdict(), largeur))

    titre("Décision, job par job")
    for travail in decision.a_lancer:
        lignes.append(f"  LANCÉ   {travail.nom:<16} {decision.motifs[travail.nom]}")
    for travail in decision.sautes:
        lignes.append(f"  sauté   {travail.nom:<16} {decision.motifs[travail.nom]}")

    if decision.ecart is not None:
        titre("Le compte, en gCO2e")
        lignes.append(f"  coût si tout tourne      {decision.cout_complet}")
        lignes.append(f"  coût de ce qui tourne    {decision.cout_retenu}")
        lignes.append(f"  épargné (jobs sautés)    {decision.economie_brute}")
        lignes.append(f"  coût de la porte         {decision.cout_de_la_porte}")
        lignes.append(f"  GAIN NET                 {decision.ecart}")

    titre("D'où viennent les nombres")
    lignes.append("  SCI = (O + M) par R, avec O = E × I — ISO/IEC 21031:2024.")
    lignes.append("  E n'est pas mesurée : un exécuteur partagé n'expose pas ses")
    lignes.append("  compteurs d'énergie. Elle est estimée depuis le temps")
    lignes.append("  processeur et la mémoire, par les coefficients suivants.")
    lignes.append("")
    # `sources()` les porte toutes, durée de vie et part de machine comprises
    # depuis qu'elles sont devenues des coefficients à part entière. Les
    # réécrire à la main ici, c'était la garantie d'en oublier une le jour où
    # le modèle en gagnerait une — ce qui est exactement ce qui est arrivé.
    for source in modele.sources():
        lignes.extend(_replier("  • " + source, largeur, suite="    "))

    reserves = _reserves(decision, mesures)
    titre("Ce que ce rapport ne couvre pas" if reserves else "Couverture")
    if reserves:
        for reserve in reserves:
            lignes.extend(_replier("  • " + reserve, largeur, suite="    "))
    else:
        lignes.append("  Tous les jobs ont été mesurés, sans réserve.")

    lignes.append("")
    return "\n".join(lignes)


def _reserves(decision: Decision, mesures: dict[str, Mesure] | None) -> list[str]:
    tout: list[str] = []
    for nom in decision.non_mesures:
        tout.append(f"{nom} : aucune mesure, exclu des deux totaux")
    for nom, mesure in (mesures or {}).items():
        for reserve in mesure.reserves:
            tout.append(f"{nom} : {reserve}")
        if mesure.code_de_sortie != 0:
            tout.append(f"{nom} : terminé avec le code {mesure.code_de_sortie}, "
                        "la mesure ne décrit pas une exécution réussie")
    return tout


def _replier(texte: str, largeur: int, suite: str = "  ") -> list[str]:
    """Replie sans couper un mot. Pas `textwrap` : il mange les deux-points
    alignés et les crochets d'intervalle qui portent tout le sens ici."""
    mots = texte.split()
    if not mots:
        return [""]
    lignes, courante = [], mots[0]
    for mot in mots[1:]:
        if len(courante) + 1 + len(mot) <= largeur:
            courante += " " + mot
        else:
            lignes.append(courante)
            courante = suite + mot
    lignes.append(courante)
    return lignes
