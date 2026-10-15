"""Un nombre qu'on ne connaît pas exactement, et l'arithmétique qui va avec.

## Pourquoi ce module existe avant tout le reste

On ne peut pas **mesurer** l'énergie d'un job de CI. Un exécuteur partagé ne
donne pas accès aux compteurs RAPL du processeur, et même s'il les donnait, on
ne saurait pas quelle part du matériel nous revient. Ce qu'on mesure vraiment,
c'est du temps processeur et de la mémoire ; le passage aux kilowattheures se
fait par un coefficient publié, et ce coefficient porte une incertitude qui se
compte en dizaines de pour cent.

Un score SCI est donc une **estimation**, pas une mesure. Le rendre comme un
nombre à quatre décimales serait un mensonge de précision — et c'est exactement
ce que la plupart des outils de ce domaine font.

D'où ce module : toute quantité estimée est un intervalle, l'incertitude se
propage à travers les calculs, et une décision qui ne survit pas à la largeur
de l'intervalle n'est pas prise.

## Pourquoi l'arithmétique d'intervalles suffit ici

Elle est pessimiste dès que la même variable apparaît deux fois dans une
expression : `x - x` rend un intervalle large au lieu de zéro. C'est acceptable
dans ce calcul-ci parce que les grandeurs qu'on combine sont **indépendantes et
positives** — temps processeur, coefficient énergétique, intensité carbone du
réseau, carbone incarné — et qu'aucune n'apparaît deux fois dans la formule SCI.

La règle est écrite pour qu'on la vérifie plutôt que de la croire : voir
`test_intervalle.py`, où l'on compare la propagation à un tirage de Monte-Carlo.

Et quand elle ne suffira plus, le pessimisme joue du bon côté : il élargit
l'intervalle, donc il rend la porte **plus** prudente, jamais moins.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Intervalle:
    """Une grandeur positive comprise entre `bas` et `haut`, bornes incluses.

    Pas de distribution, pas d'écart-type : seulement un encadrement. Dire
    « entre 2 et 6 » est honnête quand on ne sait pas si c'est 3 ou 5 ; dire
    « 4 ± 1 » prétend connaître une forme qu'on n'a pas.
    """

    bas: float
    haut: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.bas) and math.isfinite(self.haut)):
            raise ValueError(f"un intervalle fini était attendu : [{self.bas}, {self.haut}]")
        if self.bas > self.haut:
            raise ValueError(f"bornes inversées : [{self.bas}, {self.haut}]")
        if self.bas < 0:
            # Toutes les grandeurs de ce calcul sont des quantités physiques
            # positives. Une borne basse négative est le signe qu'un coefficient
            # a été mal saisi, et l'attraper ici vaut mieux que de propager un
            # carbone négatif jusqu'au rapport.
            raise ValueError(f"grandeur négative : [{self.bas}, {self.haut}]")

    # -- construction ------------------------------------------------------
    @classmethod
    def exact(cls, valeur: float) -> "Intervalle":
        """Une grandeur connue sans incertitude : un comptage, une durée lue."""
        return cls(valeur, valeur)

    @classmethod
    def autour(cls, valeur: float, part: float) -> "Intervalle":
        """`valeur` à ± `part` près, en proportion (0.3 = ±30 %)."""
        if not 0 <= part < 1:
            raise ValueError(f"une part relative dans [0, 1[ était attendue : {part}")
        return cls(valeur * (1 - part), valeur * (1 + part))

    # -- arithmétique ------------------------------------------------------
    def __add__(self, autre: "Intervalle") -> "Intervalle":
        return Intervalle(self.bas + autre.bas, self.haut + autre.haut)

    def __mul__(self, autre: "Intervalle | float") -> "Intervalle":
        if isinstance(autre, (int, float)):
            autre = Intervalle.exact(float(autre))
        # Les quatre produits, et non seulement bas×bas et haut×haut : la forme
        # courte n'est juste que si les deux intervalles sont positifs, ce que
        # `__post_init__` garantit aujourd'hui. Écrire la forme générale coûte
        # trois caractères et survit à une borne négative si elle est un jour
        # autorisée.
        coins = (self.bas * autre.bas, self.bas * autre.haut,
                 self.haut * autre.bas, self.haut * autre.haut)
        return Intervalle(min(coins), max(coins))

    __rmul__ = __mul__

    def __truediv__(self, diviseur: float) -> "Intervalle":
        """Division par un nombre connu — typiquement l'unité fonctionnelle R.

        R est un comptage (des exécutions, des requêtes, des fusions), donc il
        est exact. Diviser par un intervalle demanderait de traiter le zéro et
        n'a pas de sens ici : on refuse plutôt que de deviner.
        """
        if diviseur <= 0:
            raise ValueError(f"l'unité fonctionnelle doit être positive : {diviseur}")
        return Intervalle(self.bas / diviseur, self.haut / diviseur)

    def __sub__(self, autre: "Intervalle") -> "Intervalle":
        """La différence, qui peut être négative — donc elle ne rend pas un
        `Intervalle`, mais un `Ecart`, lequel sait dire s'il contient zéro.

        C'est tout l'objet de ce module : la soustraction est l'endroit où une
        économie se calcule, et c'est là qu'on ment le plus facilement.
        """
        return Ecart(self.bas - autre.haut, self.haut - autre.bas)

    # -- lecture -----------------------------------------------------------
    @property
    def largeur(self) -> float:
        return self.haut - self.bas

    @property
    def milieu(self) -> float:
        """Le centre de l'encadrement.

        À n'utiliser que pour trier ou afficher, **jamais pour décider** : c'est
        un nombre qu'aucune mesure n'a produit.
        """
        return (self.bas + self.haut) / 2

    def __str__(self) -> str:
        return f"[{_lisible(self.bas, -1)} ; {_lisible(self.haut, +1)}]"


@dataclass(frozen=True)
class Ecart:
    """Une différence entre deux grandeurs estimées. Peut être négative.

    Le seul type de ce module qui sait répondre à la question qui compte :
    *est-ce que je peux affirmer qu'il y a une économie ?*
    """

    bas: float
    haut: float

    def __post_init__(self) -> None:
        if self.bas > self.haut:
            raise ValueError(f"bornes inversées : [{self.bas}, {self.haut}]")

    @property
    def contient_zero(self) -> bool:
        return self.bas <= 0 <= self.haut

    @property
    def economie_etablie(self) -> bool:
        """Vrai seulement si **toute** la plage est une économie.

        Un écart qui enjambe zéro n'est pas une petite économie : c'est une
        économie dont on ne sait pas si elle existe. Les deux se ressemblent
        dans un tableur et ne se ressemblent pas du tout dans un rapport.
        """
        return self.bas > 0

    @property
    def surcout_etabli(self) -> bool:
        return self.haut < 0

    @property
    def largeur(self) -> float:
        return self.haut - self.bas

    def __str__(self) -> str:
        return f"[{_lisible(self.bas, -1)} ; {_lisible(self.haut, +1)}]"


def somme(parties: Iterable[Intervalle]) -> Intervalle:
    """La somme d'un ensemble de grandeurs. Vide → zéro exact.

    Le SCI d'une application est la somme de celui de ses composants ; c'est la
    règle d'agrégation de la spécification, et elle vaut aussi pour les bornes.
    """
    total = Intervalle.exact(0.0)
    for partie in parties:
        total = total + partie
    return total


def _lisible(x: float, sens: int) -> str:
    """Un nombre lisible, arrondi **vers l'extérieur** de l'intervalle.

    Trois chiffres significatifs : les grammes de CO2e d'un job de CI se
    comptent en unités ou en dixièmes, et en afficher plus donnerait à une
    estimation l'air d'une mesure.

    Mais arrondir au plus proche rétrécirait parfois l'encadrement affiché —
    [0,3078 ; 1,2341] deviendrait [0,308 ; 1,23], qui ne contient plus la borne
    haute réelle. Un intervalle imprimé doit contenir celui qu'on a calculé,
    sinon l'affichage défait exactement ce que le calcul protège. La borne
    basse descend donc, la borne haute monte : `sens` vaut -1 ou +1.

    (L'arrondi au plus proche de Python est en prime celui du banquier :
    `f"{1234.5:.0f}"` rend `1234`. Une raison de plus de ne pas s'en remettre
    à lui pour une borne.)
    """
    if x == 0:
        return "0"
    magnitude = math.floor(math.log10(abs(x)))
    decimales = min(max(0, 2 - magnitude), 9)
    pas = 10.0 ** -decimales
    arrondi = (math.floor(x / pas) if sens < 0 else math.ceil(x / pas)) * pas
    return f"{arrondi:.{decimales}f}"
