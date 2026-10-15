"""Mesurer ce qu'un job a réellement consommé, et dire ce qu'on n'a pas pu mesurer.

## Ce qu'on peut observer, et comment

Trois grandeurs entrent dans le calcul SCI : le temps processeur, l'intégrale
mémoire × temps, et la durée réelle. Aucune n'est donnée par le système sous une
forme directement utilisable, et chacune a son piège.

**Le temps processeur** est cumulatif et monotone, donc le dernier relevé pris
avant la fin du processus est le total — à ceci près qu'il manque ce qui a été
brûlé entre ce relevé et la sortie. Ce manque est borné par la période
d'échantillonnage, et il est déclaré plutôt que tu.

**La mémoire** est le vrai piège. `ru_maxrss` donne un **pic**, pas une
intégrale ; multiplier le pic par la durée surestime, parfois d'un facteur
important pour un processus qui ne monte qu'à la fin. On échantillonne donc la
mémoire résidente pendant la course et on intègre par trapèzes. C'est une
mesure, pas une majoration.

**La durée réelle** est la seule qu'on obtienne exactement.

## La règle de ce module

**Un échantillonnage trop maigre n'est pas une mesure.** Un processus qui dure
moins que quelques périodes ne produit pas assez de points pour qu'une intégrale
veuille dire quoi que ce soit. Le module le dit (`reserves`), et la porte peut
alors exclure ce job du bilan plutôt que de lui inventer une consommation.

Aucune dépendance : `/proc` sous Linux, `ctypes` sous Windows, et un repli
honnête ailleurs.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .sci import Observation

#: Période d'échantillonnage par défaut, en secondes. **Choisie par la mesure**,
#: pas au jugé.
#:
#: Relevé du 14/10/2026 sous Windows, sur une charge de 0,9 s et 80 Mo, trois
#: courses par période :
#:
#:     période   échantillons   temps processeur lu
#:       0,2 s         5             0,797 s        ← sous-estimé
#:       0,1 s         9             0,797 s        ← sous-estimé
#:      0,05 s        19             0,906 s
#:      0,01 s        86             0,906 s
#:     0,002 s       333             0,938 s
#:
#: Au-delà de 0,05 s le temps processeur décroche de plus de 10 % ; en deçà il
#: est stable. On prend 0,01 s, qui donne des dizaines de points même sur un job
#: d'une demi-seconde.
#:
#: **Et ce que coûte la sonde à cette finesse, je ne sais pas le dire.** Sept
#: courses à 0,2 s donnent une horloge médiane de 0,891 s (écart-type 0,031) et
#: sept à 0,01 s donnent 0,937 s (écart-type 0,016) : les plages se recouvrent,
#: donc l'écart de 46 ms n'est pas distinguable de la variation d'une course à
#: l'autre. Affirmer « la sonde ne coûte rien » serait tirer une conclusion d'une
#: mesure qui ne la porte pas. Ce qu'on peut dire : si surcoût il y a, il est de
#: l'ordre de quelques pour cent de l'horloge, et il est largement payé par les
#: 10 % de justesse regagnés sur le temps processeur.
PERIODE = 0.01

#: En deçà, l'intégrale mémoire ne veut rien dire et on le dit.
ECHANTILLONS_MINIMUM = 5

_OCTETS_PAR_GIGA = 1024.0 ** 3


@dataclass
class Mesure:
    """Ce qu'on a observé, ce que ça a donné, et ce qu'on n'a pas pu voir."""

    observation: Observation
    code_de_sortie: int
    echantillons: int
    #: Tout ce que la plateforme n'a pas permis de mesurer, en clair. Une
    #: réserve non vide doit se retrouver dans le rapport : un bilan dont on
    #: ignore la couverture n'est pas un bilan.
    reserves: list[str] = field(default_factory=list)

    @property
    def utilisable(self) -> bool:
        return self.code_de_sortie == 0 and not self.reserves


# --------------------------------------------------------------------------- #
# Les sondes, une par plateforme
# --------------------------------------------------------------------------- #
class _Sonde:
    """Lit le temps processeur (s) et la mémoire résidente (octets) d'un PID."""

    nom = "aucune"

    def lire(self, pid: int) -> tuple[float, float] | None:
        raise NotImplementedError

    def fermer(self) -> None:
        """Libère ce qui doit l'être. Appelé une fois, à la fin."""


def cpu_depuis_stat(stat: str, tics: float) -> float:
    """Le temps processeur, en secondes, depuis une ligne `/proc/<pid>/stat`.

    Extrait de la sonde pour être éprouvé partout, y compris là où `/proc`
    n'existe pas — c'est la ligne la plus facile à casser de ce module.

    Le deuxième champ est le nom de l'exécutable **entre parenthèses**, et ce
    nom peut contenir des espaces et des parenthèses : un simple `.split()`
    décalerait silencieusement toutes les colonnes suivantes, et le temps
    processeur lu serait un autre nombre, plausible et faux. On coupe donc
    après la **dernière** parenthèse fermante.

    Au-delà, les champs sont ceux de `proc(5)` : utime est le 14e et stime le
    15e, soit les index 11 et 12 une fois les trois premiers retirés.
    """
    apres = stat[stat.rindex(")") + 2:].split()
    return (float(apres[11]) + float(apres[12])) / tics


class _SondeProc(_Sonde):
    """Linux : `/proc/<pid>/stat` et `/proc/<pid>/statm`."""

    nom = "/proc"

    def __init__(self) -> None:
        self._tics = os.sysconf("SC_CLK_TCK")
        self._page = os.sysconf("SC_PAGE_SIZE")

    def lire(self, pid: int) -> tuple[float, float] | None:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            statm = Path(f"/proc/{pid}/statm").read_text(encoding="utf-8")
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            # Le processus s'est terminé entre deux lectures. Ce n'est pas une
            # erreur : c'est la fin normale de la boucle.
            return None
        # La deuxième colonne de statm est la mémoire résidente, en pages.
        return cpu_depuis_stat(stat, self._tics), float(statm.split()[1]) * self._page


class _SondeWindows(_Sonde):
    """Windows : `GetProcessTimes` et `GetProcessMemoryInfo` par ctypes."""

    nom = "ctypes/Windows"

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._psapi = ctypes.WinDLL("psapi", use_last_error=True)
        self._poignees: dict[int, int] = {}

        class _COMPTEURS(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        self._COMPTEURS = _COMPTEURS

    def _poignee(self, pid: int) -> int | None:
        if pid not in self._poignees:
            # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
            poignee = self._kernel.OpenProcess(0x0400 | 0x0010, False, pid)
            if not poignee:
                return None
            self._poignees[pid] = poignee
        return self._poignees[pid]

    def lire(self, pid: int) -> tuple[float, float] | None:
        poignee = self._poignee(pid)
        if poignee is None:
            return None
        ft = self._wintypes.FILETIME
        creation, sortie, noyau, utilisateur = ft(), ft(), ft(), ft()
        ok = self._kernel.GetProcessTimes(
            poignee, self._ctypes.byref(creation), self._ctypes.byref(sortie),
            self._ctypes.byref(noyau), self._ctypes.byref(utilisateur))
        if not ok:
            return None

        def secondes(valeur) -> float:
            # FILETIME compte par tranches de 100 nanosecondes.
            return ((valeur.dwHighDateTime << 32) | valeur.dwLowDateTime) / 1e7

        compteurs = self._COMPTEURS()
        compteurs.cb = self._ctypes.sizeof(self._COMPTEURS)
        if not self._psapi.GetProcessMemoryInfo(
                poignee, self._ctypes.byref(compteurs), compteurs.cb):
            return None
        return secondes(noyau) + secondes(utilisateur), float(compteurs.WorkingSetSize)

    def fermer(self) -> None:
        for poignee in self._poignees.values():
            self._kernel.CloseHandle(poignee)
        self._poignees.clear()


def sonde_de_la_plateforme() -> _Sonde | None:
    """La sonde utilisable ici, ou `None` s'il n'y en a pas.

    Rendre `None` plutôt que de bricoler un repli silencieux : un chiffre
    inventé sur une plateforme non gérée serait pire que l'absence de chiffre,
    parce qu'il aurait l'air d'une mesure.
    """
    if sys.platform.startswith("linux") and Path("/proc/self/stat").exists():
        return _SondeProc()
    if sys.platform == "win32":
        try:
            return _SondeWindows()
        except Exception:  # noqa: BLE001 - ctypes peut échouer de mille façons
            return None
    return None


# --------------------------------------------------------------------------- #
# La mesure
# --------------------------------------------------------------------------- #
def mesurer(commande: Sequence[str], *, periode: float = PERIODE,
            cwd: str | Path | None = None, silencieux: bool = False) -> Mesure:
    """Lance `commande` et mesure ce qu'elle consomme.

    Le processus est échantillonné pendant qu'il tourne : le temps processeur
    est monotone, donc le dernier relevé fait foi ; la mémoire est intégrée par
    trapèzes sur les relevés.

    **La sortie de la commande passe par défaut, et ce défaut a changé.** Elle
    était jetée dans `DEVNULL`, ce qui convenait à `exemple.py` — des charges
    synthétiques dont le bavardage n'apprend rien. Dès que `mesure` enveloppe
    les vrais jobs d'un pipeline, jeter la sortie veut dire qu'une suite de
    tests qui échoue n'affiche aucun échec, qu'un scan de sécurité n'affiche
    aucune trouvaille, et que le job meurt sur un code de sortie nu.

    C'est arrivé : `scan-securite` a échoué sur GitLab et le journal ne
    contenait que la ligne de mesure, puis `exit code 1`. L'outil avait avalé
    exactement ce qu'il était censé rendre visible. `silencieux=True` reste
    disponible pour la démonstration, mais il faut le demander.
    """
    if periode <= 0:
        raise ValueError(f"la période d'échantillonnage doit être positive : {periode}")

    sonde = sonde_de_la_plateforme()
    reserves: list[str] = []
    if sonde is None:
        reserves.append(
            f"aucune sonde pour {sys.platform} : ni temps processeur ni mémoire "
            "n'ont été mesurés")

    releves: list[tuple[float, float, float]] = []  # (instant, cpu, octets)
    arret = threading.Event()

    def echantillonner(pid: int) -> None:
        while not arret.is_set():
            lu = sonde.lire(pid) if sonde else None
            if lu is None:
                return
            releves.append((time.monotonic(), lu[0], lu[1]))
            arret.wait(periode)

    debut = time.monotonic()
    # `None` hérite des descripteurs du parent : la commande écrit droit dans
    # le journal, sans que rien ne soit bufferisé ni reconstitué après coup.
    canal = subprocess.DEVNULL if silencieux else None
    processus = subprocess.Popen(list(commande), cwd=cwd,
                                 stdout=canal, stderr=canal)
    fil = None
    if sonde is not None:
        fil = threading.Thread(target=echantillonner, args=(processus.pid,),
                               daemon=True)
        fil.start()
    code = processus.wait()
    arret.set()
    if fil is not None:
        fil.join(timeout=2 * periode + 1.0)
    if sonde is not None:
        sonde.fermer()
    horloge = time.monotonic() - debut

    cpu, giga_secondes = 0.0, 0.0
    if releves:
        cpu = releves[-1][1]
        giga_secondes = _integrer(releves) / _OCTETS_PAR_GIGA
        if len(releves) < ECHANTILLONS_MINIMUM:
            reserves.append(
                f"{len(releves)} échantillon(s) seulement pour {horloge:.3f} s : "
                "trop peu pour que l'intégrale mémoire veuille dire quelque chose")
        # Ce qui a été brûlé entre le dernier relevé et la sortie n'est pas
        # compté. Le manque est borné par une période ; au-delà de 10 % de la
        # durée, il cesse d'être négligeable et doit être dit.
        if periode > 0.10 * max(horloge, 1e-9):
            reserves.append(
                f"période d'échantillonnage ({periode} s) trop grande devant la "
                f"durée ({horloge:.3f} s) : le temps processeur est sous-estimé")
    elif sonde is not None:
        reserves.append(
            "aucun échantillon : le processus s'est terminé avant le premier relevé")

    return Mesure(
        observation=Observation(secondes_processeur=cpu,
                                gigaoctets_secondes=giga_secondes,
                                secondes_horloge=horloge),
        code_de_sortie=code,
        echantillons=len(releves),
        reserves=reserves,
    )


def _integrer(releves: list[tuple[float, float, float]]) -> float:
    """L'intégrale mémoire × temps, par trapèzes, en octets-secondes.

    Les trapèzes plutôt que les rectangles parce qu'un processus dont la mémoire
    monte régulièrement serait sous-estimé de moitié par un pas à gauche et
    surestimé d'autant par un pas à droite. Un seul relevé rend zéro : on ne
    sait rien de la durée qu'il couvre, et `reserves` le dit déjà.
    """
    total = 0.0
    for (t0, _, o0), (t1, _, o1) in zip(releves, releves[1:]):
        total += (o0 + o1) / 2 * (t1 - t0)
    return total
