"""Le score SCI d'un travail de CI : un encadrement, jamais un nombre.

## La formule, et ce qu'elle cache

ISO/IEC 21031:2024 — la spécification *Software Carbon Intensity* — définit :

    SCI = (O + M) par R,  avec  O = E × I

- **E** l'électricité consommée, en kWh ;
- **I** l'intensité carbone du réseau qui l'a fournie, en gCO2e/kWh ;
- **M** le carbone incarné du matériel, amorti sur la part de temps et de
  ressource que ce travail a occupée ;
- **R** l'unité fonctionnelle : ici une exécution de pipeline.

La formule est simple. Le piège est ailleurs : **sur un exécuteur de CI
partagé, E ne se mesure pas.** Les compteurs d'énergie du processeur ne sont pas
exposés, et même s'ils l'étaient, rien ne dirait quelle part du matériel nous
revient. Ce qu'on observe vraiment, c'est du temps processeur et de la mémoire.

Tout le reste est une **conversion par coefficient publié**, et ces coefficients
sont larges : la puissance d'un cœur virtuel varie d'un facteur trois entre un
serveur au repos et le même en charge, et personne ne sait sur quel matériel
tourne un exécuteur partagé.

## La décision de conception qui en découle

**Un coefficient ne peut pas être construit sans sa source.** Pas de valeur
nue dans le code : `Coefficient` exige un intervalle *et* une chaîne qui dit
d'où il vient. Un nombre dont on a perdu la provenance n'est plus une mesure,
c'est une rumeur avec des décimales — et le rapport final cite chaque source.

Le résultat est donc un intervalle. C'est moins flatteur qu'un chiffre net, et
c'est la seule chose qu'on puisse défendre.
"""

from __future__ import annotations

from dataclasses import dataclass

from .intervalle import Intervalle, somme

#: Joules par kilowattheure. 1 kWh = 1000 W pendant 3600 s.
_JOULES_PAR_KWH = 3_600_000.0


@dataclass(frozen=True)
class Coefficient:
    """Une grandeur de conversion, son unité, et d'où elle vient.

    `source` est obligatoire et non vide, exprès. Un coefficient énergétique
    sans provenance ne peut être ni contesté ni mis à jour, et un score bâti
    dessus ne peut pas être refait par quelqu'un d'autre — ce qui est la seule
    chose qui distingue une mesure d'une affirmation.
    """

    bas: float
    haut: float
    unite: str
    source: str

    def __post_init__(self) -> None:
        if not self.source or not self.source.strip():
            raise ValueError(
                "un coefficient sans source n'est pas une mesure : dire d'où vient "
                f"la valeur [{self.bas}, {self.haut}] {self.unite}"
            )
        if not self.unite or not self.unite.strip():
            raise ValueError(f"unité manquante pour [{self.bas}, {self.haut}]")
        # Valide les bornes en passant par le type qui sait le faire.
        self.plage()

    def plage(self) -> Intervalle:
        return Intervalle(self.bas, self.haut)

    def __str__(self) -> str:
        return f"{self.plage()} {self.unite}  ({self.source})"


@dataclass(frozen=True)
class Observation:
    """Ce qu'un exécuteur donne réellement, et rien d'autre.

    Trois nombres qu'on lit, par opposition à tout ce qu'on en déduira ensuite.
    Les garder séparés des coefficients est ce qui permet de rejouer un calcul
    avec d'autres coefficients sans refaire tourner le job.
    """

    #: Temps processeur cumulé, en secondes de cœur.
    secondes_processeur: float
    #: Mémoire occupée × durée, en gigaoctets-secondes.
    gigaoctets_secondes: float
    #: Durée réelle, en secondes. Sert à amortir le carbone incarné.
    secondes_horloge: float

    def __post_init__(self) -> None:
        for nom in ("secondes_processeur", "gigaoctets_secondes", "secondes_horloge"):
            valeur = getattr(self, nom)
            if valeur < 0:
                raise ValueError(f"{nom} négatif : {valeur}")


@dataclass(frozen=True)
class Modele:
    """Les coefficients à appliquer aux observations. Tous sourcés.

    Séparé d'`Observation` parce que les deux vieillissent différemment : une
    observation est datée et définitive, un coefficient se révise quand une
    meilleure publication sort. Pouvoir recalculer d'anciens scores avec de
    nouveaux coefficients est précisément ce qu'une estimation doit permettre.
    """

    #: Puissance d'un cœur virtuel occupé, en watts.
    watts_par_coeur: Coefficient
    #: Puissance d'un gigaoctet de mémoire occupé, en watts.
    watts_par_gigaoctet: Coefficient
    #: Intensité carbone du réseau électrique, en gCO2e/kWh.
    intensite_reseau: Coefficient
    #: Carbone incarné de la machine entière, en gCO2e.
    carbone_incarne_machine: Coefficient
    #: Durée de vie du matériel, en secondes.
    #:
    #: `Coefficient` et non un simple nombre, et c'est une correction. Ces deux
    #: derniers paramètres étaient des `float` nus : ils échappaient à la règle
    #: « pas de coefficient sans source » que ce module impose à tous les
    #: autres. Or ce sont eux qui gouvernent le résultat le plus fort du
    #: projet — la part du matériel dans le total — et les traiter comme exacts
    #: pendant qu'on exige une provenance pour la puissance d'un cœur était
    #: incohérent. Une durée de vie de serveur n'est pas « 4 ans », c'est
    #: « 3 à 6 ans selon qui l'exploite ».
    duree_de_vie: Coefficient
    #: La part de la machine que ce travail occupe, dans ]0, 1].
    part_de_machine: Coefficient

    def __post_init__(self) -> None:
        if self.duree_de_vie.bas <= 0:
            raise ValueError("la durée de vie du matériel doit être positive")
        part = self.part_de_machine.plage()
        # Les parenthèses comptent : `not 0 < bas and haut <= 1` se lit
        # `(not 0 < bas) and (haut <= 1)` et laissait passer [0,5 ; 1,5].
        if not (0 < part.bas and part.haut <= 1):
            raise ValueError(
                f"la part de machine doit être dans ]0, 1] : {part}")

    def sources(self) -> list[str]:
        """Toutes les provenances, pour que le rapport les cite sans en oublier."""
        return [str(c) for c in (self.watts_par_coeur, self.watts_par_gigaoctet,
                                 self.intensite_reseau, self.carbone_incarne_machine,
                                 self.duree_de_vie, self.part_de_machine)]


def energie_kwh(observation: Observation, modele: Modele) -> Intervalle:
    """E — l'électricité, estimée depuis le temps processeur et la mémoire.

    Le mot *estimée* est le seul qui compte dans cette phrase.
    """
    joules_processeur = modele.watts_par_coeur.plage() * observation.secondes_processeur
    joules_memoire = (modele.watts_par_gigaoctet.plage()
                      * observation.gigaoctets_secondes)
    return somme((joules_processeur, joules_memoire)) / _JOULES_PAR_KWH


def operationnel_g(observation: Observation, modele: Modele) -> Intervalle:
    """O = E × I, en grammes de CO2e."""
    return energie_kwh(observation, modele) * modele.intensite_reseau.plage()


def incarne_g(observation: Observation, modele: Modele) -> Intervalle:
    """M — le carbone de fabrication, amorti sur le temps et la ressource.

    La spécification demande une part de temps **et** une part de ressource :
    occuper un quart d'une machine pendant une minute n'engage pas le même
    matériel qu'occuper la machine entière pendant la même minute.

    C'est le terme qui domine. Mesuré le 14/10/2026 sur cinq jobs de CI :
    **91,8 % du total**, et il reste au-dessus de l'opérationnel tant que le
    job est facturé plus de 2,4 % de la machine. Autant dire toujours.
    """
    part_de_vie = _inverse(modele.duree_de_vie.plage()) * observation.secondes_horloge
    return (modele.carbone_incarne_machine.plage()
            * part_de_vie * modele.part_de_machine.plage())


def _inverse(plage: Intervalle) -> Intervalle:
    """1/x sur un intervalle strictement positif : les bornes s'échangent."""
    if plage.bas <= 0:
        raise ValueError(f"inversion impossible : {plage} touche ou passe zéro")
    return Intervalle(1.0 / plage.haut, 1.0 / plage.bas)


def sci_g(observation: Observation, modele: Modele, *,
          unites: float = 1.0) -> Intervalle:
    """SCI = (O + M) par R, en grammes de CO2e par unité fonctionnelle.

    `unites` est R. Le défaut de 1 rend le score d'une seule exécution, ce qui
    est l'usage courant ici : on compare deux façons de faire tourner le même
    pipeline, donc l'unité est l'exécution.
    """
    total = somme((operationnel_g(observation, modele), incarne_g(observation, modele)))
    return total / unites


def cumuler(observations: list[Observation], modele: Modele, *,
            unites: float = 1.0) -> Intervalle:
    """Le SCI d'un pipeline entier : la somme de celui de ses jobs.

    C'est la règle d'agrégation de la spécification — le score d'une application
    est la somme des scores de ses composants — et elle vaut pour les bornes
    comme pour les valeurs.
    """
    return somme(sci_g(o, modele) for o in observations) / unites
