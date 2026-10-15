"""L'intensité carbone du réseau, heure par heure — et le second levier.

## Pourquoi une moyenne annuelle ne suffit pas

Un kilowattheure consommé à 3 h du matin un jour de vent n'a pas le même coût
qu'un kilowattheure consommé à 19 h un jour sans vent. Sur beaucoup de réseaux
l'écart entre l'heure la plus propre et la plus sale d'une même journée dépasse
un facteur deux — c'est-à-dire **plus que tout ce qu'on peut espérer gagner en
optimisant le code**.

Une moyenne annuelle efface exactement le levier le plus efficace du domaine.
`sci.py` accepte donc une intensité constante parce qu'il faut bien commencer
quelque part, mais c'est une approximation, et ce module est sa correction.

## Le second levier, et pourquoi il ne viole pas la règle du projet

`porte.py` pose que **la correction décide, le carbone mesure** : un job n'est
jamais sauté parce qu'il est cher.

Décaler n'est pas sauter. Un job sans échéance — une analyse nocturne, une
reconstruction d'index, un rapport hebdomadaire — peut attendre trois heures
sans que personne ne perde rien. Ce n'est pas une question de justesse, c'est
une question de latence, et le carbone a parfaitement le droit d'en décider.

D'où la séparation qui gouverne tout le projet :

| décision | qui décide |
|---|---|
| **si** un job tourne | la correction, jamais le carbone |
| **quand** il tourne, s'il n'a pas d'échéance | le carbone |

## Mesure et prévision ne sont pas la même chose

Le passé d'un réseau est mesuré ; son avenir est prévu. Les traiter pareil
reviendrait à annoncer une économie future avec l'assurance d'un relevé. Chaque
point porte donc son origine, et une prévision est élargie d'une marge **qui est
déclarée, pas devinée** — comme les coefficients de `sci.py`, elle exige sa
source.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Sequence

from .intervalle import Ecart, Intervalle

Origine = Literal["mesure", "prevision"]


@dataclass(frozen=True)
class Point:
    """L'intensité du réseau sur une heure, et d'où ce chiffre vient."""

    debut: datetime
    intensite: Intervalle
    origine: Origine

    def __post_init__(self) -> None:
        if self.debut.tzinfo is None:
            # Une heure sans fuseau est une heure dont on ne sait pas quand elle
            # a eu lieu ; sur un sujet dont tout l'intérêt est l'heure, c'est
            # une erreur et pas une commodité.
            raise ValueError(f"instant sans fuseau horaire : {self.debut}")
        if self.origine not in ("mesure", "prevision"):
            raise ValueError(f"origine inconnue : {self.origine}")


@dataclass(frozen=True)
class HorsSerie(Exception):
    """Demandé une heure que la série ne couvre pas.

    Une exception plutôt qu'une extrapolation : prolonger une courbe d'intensité
    au-delà de ce qu'on sait est précisément la façon de produire un chiffre qui
    a l'air d'une mesure et n'en est pas une.
    """

    instant: datetime
    debut: datetime | None
    fin: datetime | None

    def __str__(self) -> str:
        if self.debut is None:
            return f"série vide : rien à dire sur {self.instant}"
        return (f"{self.instant.isoformat()} est hors de la série "
                f"[{self.debut.isoformat()} ; {self.fin.isoformat()}]")


class Serie:
    """Une suite de points horaires, triés, sans trou toléré en silence."""

    def __init__(self, points: Sequence[Point], *, pas: timedelta | None = None,
                 source: str = "") -> None:
        if not source.strip():
            raise ValueError("une série d'intensité sans source n'est pas une mesure")
        self.points = sorted(points, key=lambda p: p.debut)
        self.pas = pas or timedelta(hours=1)
        self.source = source
        self._debuts = [p.debut for p in self.points]
        self._verifier_la_continuite()

    def _verifier_la_continuite(self) -> None:
        """Un trou dans la série doit se voir à la construction.

        Une heure manquante au milieu ferait silencieusement attribuer à un job
        l'intensité de l'heure d'avant. Mieux vaut refuser la série que rendre
        un chiffre dont personne ne saura qu'il est faux.
        """
        for avant, apres in zip(self.points, self.points[1:]):
            ecart = apres.debut - avant.debut
            if ecart != self.pas:
                raise ValueError(
                    f"trou ou doublon dans la série entre {avant.debut.isoformat()} "
                    f"et {apres.debut.isoformat()} : {ecart} au lieu de {self.pas}")

    @property
    def debut(self) -> datetime | None:
        return self.points[0].debut if self.points else None

    @property
    def fin(self) -> datetime | None:
        return self.points[-1].debut + self.pas if self.points else None

    def point_a(self, instant: datetime) -> Point:
        """Le point qui couvre `instant`, ou `HorsSerie`."""
        if instant.tzinfo is None:
            raise ValueError(f"instant sans fuseau horaire : {instant}")
        if not self.points or not (self.debut <= instant < self.fin):
            raise HorsSerie(instant, self.debut, self.fin)
        return self.points[bisect_right(self._debuts, instant) - 1]

    def intensite_a(self, instant: datetime) -> Intervalle:
        return self.point_a(instant).intensite

    def fenetres(self, duree: timedelta, *, depuis: datetime,
                 avant: datetime) -> list[tuple[datetime, Intervalle, Origine]]:
        """Toutes les heures de départ possibles, avec l'intensité moyenne subie.

        Une fenêtre n'est retenue que si elle tient **entièrement** dans
        [depuis, avant] et entièrement dans la série : un job dont la seconde
        moitié tombe hors de ce qu'on sait n'a pas d'intensité connue.

        L'origine rendue est `prevision` dès qu'une seule heure traversée est
        une prévision — une fenêtre n'est mesurée que si tout ce qu'elle
        traverse l'est.
        """
        sorties: list[tuple[datetime, Intervalle, Origine]] = []
        for point in self.points:
            if point.debut < depuis:
                continue
            if point.debut + duree > avant:
                continue
            try:
                traverses = self._traverses(point.debut, duree)
            except HorsSerie:
                continue
            moyenne = _moyenne([p.intensite for p in traverses])
            origine: Origine = ("mesure"
                                if all(p.origine == "mesure" for p in traverses)
                                else "prevision")
            sorties.append((point.debut, moyenne, origine))
        return sorties

    def _traverses(self, depart: datetime, duree: timedelta) -> list[Point]:
        points, instant = [], depart
        fin = depart + duree
        while instant < fin:
            points.append(self.point_a(instant))
            instant += self.pas
        # Un job plus court qu'un pas ne traverse qu'un point, et c'est correct.
        return points or [self.point_a(depart)]

    # -- chargement --------------------------------------------------------
    @classmethod
    def depuis_json(cls, chemin: str | Path, *, marge_prevision: float,
                    source_de_la_marge: str) -> "Serie":
        """Lit une série JSON et élargit les prévisions de la marge déclarée.

        `marge_prevision` est une proportion (0.25 = ±25 %), et
        `source_de_la_marge` dit d'où elle sort. Les deux sont obligatoires :
        une prévision traitée comme une mesure annoncerait un gain futur avec
        l'assurance d'un relevé.

        Format attendu, volontairement banal pour qu'un pipeline puisse le
        produire avec `curl` et `jq` :

            {"source": "...", "pas_minutes": 60,
             "points": [{"debut": "2026-10-15T00:00:00+00:00",
                         "gco2e_par_kwh": 412.0, "origine": "mesure"}, ...]}
        """
        if not 0 <= marge_prevision < 1:
            raise ValueError(f"marge dans [0, 1[ attendue : {marge_prevision}")
        if not source_de_la_marge.strip():
            raise ValueError("la marge de prévision doit dire d'où elle vient")

        brut = json.loads(Path(chemin).read_text(encoding="utf-8"))
        pas = timedelta(minutes=brut.get("pas_minutes", 60))
        points = []
        for ligne in brut["points"]:
            valeur = float(ligne["gco2e_par_kwh"])
            origine: Origine = ligne.get("origine", "mesure")
            intensite = (Intervalle.exact(valeur) if origine == "mesure"
                         else Intervalle.autour(valeur, marge_prevision))
            points.append(Point(datetime.fromisoformat(ligne["debut"]),
                                intensite, origine))
        # L'espace avant le signe pour cent est la règle en français, et un
        # rapport qui se veut lisible par un auditeur n'a pas de raison de la
        # casser. `:.0%` colle le signe au nombre, d'où le détour.
        part = f"{marge_prevision * 100:.0f} %"
        source = (f"{brut.get('source', '')} ; marge de prévision ±{part} "
                  f"({source_de_la_marge.strip()})")
        return cls(points, pas=pas, source=source)


# --------------------------------------------------------------------------- #
# Le second levier : quand lancer ce qui peut attendre
# --------------------------------------------------------------------------- #
@dataclass
class Decalage:
    """Faut-il décaler ce job, et qu'est-ce que ça rapporterait ?"""

    maintenant: datetime
    intensite_maintenant: Intervalle
    quand: datetime | None
    intensite_alors: Intervalle | None
    origine: Origine | None
    #: Le gain, en gCO2e. Négatif ou enjambant zéro → on ne décale pas.
    gain: Ecart | None
    raison: str

    @property
    def vaut_le_coup(self) -> bool:
        return self.quand is not None and self.gain is not None and self.gain.economie_etablie

    @property
    def attente(self) -> timedelta:
        return (self.quand - self.maintenant) if self.quand else timedelta(0)


def quand_lancer(serie: Serie, energie_kwh: Intervalle, duree: timedelta, *,
                 maintenant: datetime, echeance: datetime | None = None) -> Decalage:
    """L'heure la plus propre pour un job qui peut attendre.

    `echeance` est le moment avant lequel le job doit être **terminé**. Sans
    échéance, on ne cherche pas indéfiniment : on se limite à ce que la série
    couvre, parce qu'au-delà on ne sait rien.

    Le gain n'est annoncé que s'il est établi de bout en bout. Deux façons de ne
    rien annoncer, et elles ne disent pas la même chose : aucune fenêtre
    disponible, ou une fenêtre dont le gain enjambe zéro.
    """
    try:
        ici = serie.intensite_a(maintenant)
    except HorsSerie as souci:
        return Decalage(maintenant, Intervalle.exact(0.0), None, None, None, None,
                        f"l'heure actuelle n'est pas couverte : {souci}")

    limite = echeance or (serie.fin or maintenant)
    fenetres = serie.fenetres(duree, depuis=maintenant, avant=limite)
    if not fenetres:
        return Decalage(maintenant, ici, None, None, None, None,
                        "aucune fenêtre ne tient entre maintenant et l'échéance")

    quand, alors, origine = min(fenetres, key=lambda f: f[1].milieu)
    if quand <= maintenant:
        return Decalage(maintenant, ici, None, None, None, None,
                        "l'heure actuelle est déjà la plus propre disponible")

    # Le gain est E × (I_ici − I_alors). Les deux intensités sont des grandeurs
    # indépendantes — ce ne sont pas les mêmes heures — donc la soustraction est
    # légitime ici, contrairement à celle que `porte.py` a dû abandonner.
    gain_bas = energie_kwh.bas * (ici.bas - alors.haut)
    gain_haut = energie_kwh.haut * (ici.haut - alors.bas)
    gain = Ecart(min(gain_bas, gain_haut), max(gain_bas, gain_haut))

    if not gain.economie_etablie:
        return Decalage(maintenant, ici, quand, alors, origine, gain,
                        "le gain du décalage n'est pas distinguable de zéro")
    attente = quand - maintenant
    return Decalage(maintenant, ici, quand, alors, origine, gain,
                    f"décaler de {_duree_lisible(attente)} épargne {gain} gCO2e"
                    + (" (sur prévision, pas sur relevé)"
                       if origine == "prevision" else ""))


def _moyenne(intensites: Sequence[Intervalle]) -> Intervalle:
    if not intensites:
        raise ValueError("aucune intensité à moyenner")
    n = len(intensites)
    return Intervalle(sum(i.bas for i in intensites) / n,
                      sum(i.haut for i in intensites) / n)


def _duree_lisible(duree: timedelta) -> str:
    minutes = int(duree.total_seconds() // 60)
    heures, reste = divmod(minutes, 60)
    if heures and reste:
        return f"{heures} h {reste:02d}"
    return f"{heures} h" if heures else f"{reste} min"


def maintenant_utc() -> datetime:
    return datetime.now(timezone.utc)
