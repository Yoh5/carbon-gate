"""La porte : ce qu'on fait tourner, pourquoi, et ce que ça a coûté.

## La règle qui gouverne tout ce fichier

**La correction décide, le carbone mesure.**

Un job n'est jamais sauté parce qu'il est cher. Il est sauté parce que **rien de
ce dont il dépend n'a changé** — et le carbone sert ensuite à dire ce que cette
décision a épargné. Jamais l'inverse.

L'inverse serait un outil qui troque de la justesse contre des grammes de CO2e,
et il n'existe aucun seuil d'émission auquel il devient raisonnable de ne pas
lancer le scan de sécurité d'une livraison. Un système qui laisserait le carbone
arbitrer ça finirait par être désactivé le premier jour où il aurait tort, et
c'est la pire façon de ne rien économiser du tout.

## Et la seconde règle, qui est celle du projet

**Une économie qu'on ne peut pas distinguer de zéro n'est pas annoncée.**

Le SCI d'un job est une estimation large (voir `sci.py` : l'énergie d'un
exécuteur partagé ne se mesure pas). Quand l'écart entre « tout faire tourner »
et « faire tourner ce qui est nécessaire » est plus étroit que l'incertitude du
calcul, la porte le dit — elle ne publie pas un gain qu'elle a inventé en
prenant le milieu de deux intervalles.

C'est la différence entre un tableau de bord qui fait plaisir et une mesure
qu'on peut défendre devant quelqu'un qui connaît le sujet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Mapping, Sequence

from .intervalle import Ecart, Intervalle, somme
from .sci import Modele, Observation, sci_g


@dataclass(frozen=True)
class Travail:
    """Un job du pipeline, et ce dont il dépend réellement.

    `motifs` est une liste de motifs de chemins. Un job qui ne déclare aucun
    motif dépend de tout : c'est le défaut sûr, parce qu'un job oublié qui
    tourne pour rien coûte des grammes, tandis qu'un job oublié qui ne tourne
    pas coûte un incident.
    """

    nom: str
    motifs: tuple[str, ...] = ()
    #: `True` pour un job qu'on ne saute jamais, quoi qu'il arrive — une
    #: vérification de licence, un contrôle de conformité, une signature.
    toujours: bool = False

    def concerne(self, fichiers: Sequence[str]) -> bool:
        if self.toujours or not self.motifs:
            return True
        return any(fnmatch(f, m) for f in fichiers for m in self.motifs)

    def raison(self, fichiers: Sequence[str]) -> str:
        if self.toujours:
            return "marqué comme toujours exécuté"
        if not self.motifs:
            return "ne déclare aucune dépendance, donc dépend de tout"
        touches = sorted({f for f in fichiers
                          for m in self.motifs if fnmatch(f, m)})
        if touches:
            apercu = ", ".join(touches[:3])
            reste = f" (+{len(touches) - 3})" if len(touches) > 3 else ""
            return f"touché par {apercu}{reste}"
        return "aucun fichier dont il dépend n'a changé"


@dataclass
class Decision:
    """Ce que la porte a décidé, et de quoi elle peut répondre."""

    a_lancer: list[Travail] = field(default_factory=list)
    sautes: list[Travail] = field(default_factory=list)
    motifs: dict[str, str] = field(default_factory=dict)

    #: Rempli seulement quand des mesures sont fournies.
    cout_complet: Intervalle | None = None
    cout_retenu: Intervalle | None = None
    #: La somme des jobs sautés : ce qu'on a épargné avant de payer la porte.
    economie_brute: Intervalle | None = None
    #: Ce que décider a coûté. La porte se compte elle-même.
    cout_de_la_porte: Intervalle | None = None
    #: Le gain NET. C'est lui qui décide de ce qu'on annonce.
    ecart: Ecart | None = None
    #: Les jobs qu'aucune mesure ne couvre, donc exclus du bilan. Rendus
    #: visibles plutôt que tus : un bilan dont on ignore la couverture n'est
    #: pas un bilan.
    non_mesures: list[str] = field(default_factory=list)

    @property
    def economie_annoncable(self) -> bool:
        """Faux quand l'écart enjambe zéro — et il faut alors se taire."""
        return self.ecart is not None and self.ecart.economie_etablie

    def verdict(self) -> str:
        """La phrase que le pipeline écrit dans son journal.

        Écrite ici plutôt que dans l'appelant pour qu'il n'y ait **qu'une**
        formulation possible de chaque cas : un rapport qui dit « ~12 % »
        quelque part et « indéterminé » ailleurs pour le même chiffre a déjà
        perdu la confiance qu'il cherchait.
        """
        reserve = (f" {len(self.non_mesures)} job(s) sans mesure, exclus du bilan."
                   if self.non_mesures else "")
        if self.ecart is None:
            return (f"{len(self.a_lancer)} job(s) à lancer, {len(self.sautes)} sauté(s). "
                    "Aucune mesure fournie : rien n'est affirmé sur le carbone.")
        if not self.sautes:
            return ("Tous les jobs sont nécessaires : rien n'est sauté, "
                    "et il n'y a donc aucune économie à annoncer.")
        if self.economie_annoncable:
            return (f"{len(self.sautes)} job(s) sauté(s) — gain net établi : "
                    f"{self.ecart} gCO2e (brut {self.economie_brute}, "
                    f"porte {self.cout_de_la_porte}), sur un coût complet de "
                    f"{self.cout_complet}." + reserve)
        if self.ecart.surcout_etabli:
            # Et il faut pouvoir le dire : décider coûte, et si la porte coûte
            # plus qu'elle n'épargne, l'outil doit l'avouer plutôt que de
            # facturer son propre fonctionnement au bilan de quelqu'un d'autre.
            return (f"{len(self.sautes)} job(s) sauté(s), mais la porte coûte plus "
                    f"qu'elle n'épargne : {self.ecart} gCO2e nets "
                    f"(brut {self.economie_brute}, porte {self.cout_de_la_porte})."
                    + reserve)
        return (f"{len(self.sautes)} job(s) sauté(s). **Aucun gain net n'est "
                f"annoncé** : {self.ecart} gCO2e contient zéro — ce que la porte "
                f"épargne ({self.economie_brute}) ne se distingue pas de ce "
                f"qu'elle coûte ({self.cout_de_la_porte}). Les jobs restent "
                "sautés : c'est le changement qui le justifie, pas le carbone."
                + reserve)


def decider(travaux: Sequence[Travail], fichiers_changes: Sequence[str],
            mesures: Mapping[str, Observation] | None = None,
            modele: Modele | None = None,
            cout_de_decider: Observation | None = None) -> Decision:
    """Qui tourne, qui ne tourne pas, et ce que ça aura coûté.

    `mesures` associe un nom de job à ce qu'il a consommé la dernière fois.
    Un job sans mesure connue est compté dans le coût complet **et** dans le
    coût retenu : ne pas savoir ce qu'il coûte ne doit pas se traduire par un
    gain gratuit. Omettre un inconnu du total, c'est exactement la manière dont
    un tableau de bord finit par flatter celui qui l'a écrit.
    """
    decision = Decision()
    for travail in travaux:
        garde = travail.concerne(fichiers_changes)
        (decision.a_lancer if garde else decision.sautes).append(travail)
        decision.motifs[travail.nom] = travail.raison(fichiers_changes)

    if mesures is None or modele is None:
        return decision

    def cout(liste: Sequence[Travail]) -> Intervalle:
        return somme(sci_g(mesures[t.nom], modele) for t in liste if t.nom in mesures)

    # Un job dont on n'a aucune mesure est absent des DEUX totaux. C'est le seul
    # traitement qui ne peut pas surestimer l'économie : un job sauté dont le
    # coût est inconnu ne rapporte rien au compteur, donc l'écart annoncé est
    # un plancher et jamais un espoir. Le sous-estimer est acceptable ; le
    # surestimer ruinerait la seule chose que ce projet vend.
    decision.non_mesures = [t.nom for t in travaux if t.nom not in mesures]
    for nom in decision.non_mesures:
        decision.motifs[nom] += " ; coût inconnu, exclu du bilan"

    decision.cout_complet = cout(travaux)
    decision.cout_retenu = cout(decision.a_lancer)

    # L'économie brute est la somme des jobs sautés, et NON `complet - retenu`.
    #
    # Les deux sont égaux en arithmétique ordinaire. Ils ne le sont pas en
    # arithmétique d'intervalles : la différence ignore que les jobs communs
    # aux deux totaux sont les mêmes, et leur incertitude s'ajoute au lieu de
    # s'annuler. Mesuré sur un pipeline où un gros job domine, la soustraction
    # rendait [-3,59 ; 3,60] g là où l'économie réelle vaut un millième de
    # gramme et est parfaitement certaine dans son signe.
    #
    # J'avais écrit que ce pessimisme « joue du bon côté » parce qu'il rend la
    # porte plus prudente. C'était faux comme consolation : taire une économie
    # réelle est une faute symétrique de celle d'en inventer une.
    brute = somme(sci_g(mesures[t.nom], modele)
                  for t in decision.sautes if t.nom in mesures)
    decision.economie_brute = brute

    # Et la porte se compte elle-même. Décider coûte quelque chose : si ce
    # coût mord sur ce qu'on épargne, il n'y a pas de gain net et il faut le
    # dire. C'est ici que l'incertitude a un vrai mot à dire, parce que les
    # deux grandeurs sont cette fois indépendantes.
    decision.cout_de_la_porte = (sci_g(cout_de_decider, modele)
                                 if cout_de_decider is not None
                                 else Intervalle.exact(0.0))
    decision.ecart = brute - decision.cout_de_la_porte
    return decision
