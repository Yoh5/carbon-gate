"""Le README dit-il encore ce que le code calcule ?

    python verifier_readme.py

Code de sortie 1 si un chiffre du texte ne correspond plus. Il existe parce que
le même dépôt a déjà vu, deux fois, un texte survivre à la mesure qui l'avait
produit.

## Ce qui est vérifié, et ce qui ne peut pas l'être

Le tableau des cinq jobs vient de **mesures** : il dépend de la machine et de sa
charge, et exiger les mêmes décimales ailleurs serait un test qui échoue chez
tout le monde sauf chez moi. Ce qui est vérifié, c'est ce qui ne dépend que des
coefficients et d'une observation de référence posée ici :

- le point de basculement entre matériel et électricité ;
- la table de sensibilité à la part de machine ;
- la table de sensibilité à la durée de vie ;
- et, sur le tableau mesuré, l'**affirmation** plutôt que les chiffres : le
  matériel domine, et le rapport reste dans la fourchette annoncée.

Un garde-fou qui exigerait l'irreproductible apprendrait à être ignoré.
"""
from __future__ import annotations

import dataclasses
import sys
import unicodedata
from pathlib import Path
from typing import Callable

from carbongate.sci import Coefficient, Observation, incarne_g, operationnel_g

RACINE = Path(__file__).resolve().parent
ANNEE = 365 * 24 * 3600

#: L'observation de référence. Posée, pas mesurée : c'est ce qui rend ce
#: contrôle reproductible d'une machine à l'autre. Les valeurs sont celles d'un
#: job de CI d'environ une seconde, relevées le 14/10/2026.
TEMOIN = Observation(secondes_processeur=0.90,
                     gigaoctets_secondes=0.11,
                     secondes_horloge=0.95)


def modele():
    from exemple import MODELE
    return MODELE


def normaliser(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    for a, b in ((" ", " "), (" ", " "), ("−", "-"), ("–", "-"), (",", ".")):
        s = s.replace(a, b)
    return " ".join(s.split())


def part_du_materiel(part: float) -> float:
    m = dataclasses.replace(
        modele(), part_de_machine=Coefficient(part, part, "part", "contrôle"))
    inc, op = incarne_g(TEMOIN, m).milieu, operationnel_g(TEMOIN, m).milieu
    return 100 * inc / (inc + op)


def rapport_selon_la_duree(ans: int) -> float:
    m = dataclasses.replace(
        modele(),
        duree_de_vie=Coefficient(ans * ANNEE, ans * ANNEE, "s", "contrôle"))
    return incarne_g(TEMOIN, m).milieu / operationnel_g(TEMOIN, m).milieu


def basculement() -> float:
    """La part de machine à laquelle le matériel rattrape l'électricité."""
    bas, haut = 1e-6, 1.0
    for _ in range(60):
        milieu = (bas + haut) / 2
        if part_du_materiel(milieu) < 50.0:
            bas = milieu
        else:
            haut = milieu
    return (bas + haut) / 2


def controles() -> list[tuple[str, str]]:
    """(ce qu'on vérifie, la chaîne qui doit figurer dans le README)."""
    c: list[tuple[str, str]] = []

    c.append(("point de basculement",
              f"plus de **{basculement() * 100:.1f} % de la machine**"))

    for part, _ in ((1.0, None), (0.25, None), (0.05, None), (0.01, None)):
        etiquette = f"{part * 100:.0f} %"
        c.append((f"sensibilité, part {etiquette}",
                  f"| {etiquette} | {part_du_materiel(part):.1f} % |"))

    bornes = (rapport_selon_la_duree(8), rapport_selon_la_duree(2))
    c.append(("sensibilité à la durée de vie",
              f"le rapport va de {bornes[1]:.1f}x à {bornes[0]:.1f}x"))
    return c


def affirmations(texte: str) -> list[tuple[str, bool, str]]:
    """Ce qui doit rester VRAI, même si les chiffres mesurés bougent."""
    m = modele()
    inc, op = incarne_g(TEMOIN, m), operationnel_g(TEMOIN, m)
    rapport = inc.milieu / op.milieu
    return [
        ("le matériel domine dans le pire cas", inc.bas > op.haut,
         f"M au plus bas {inc.bas:.6f} contre O au plus haut {op.haut:.6f}"),
        ("le rapport reste dans la fourchette annoncée", 12.0 <= rapport <= 15.0,
         f"rapport mesuré sur le témoin : {rapport:.1f}x"),
        ("le README annonce bien la domination", "93,0 %" in texte or "93.0 %" in texte,
         "la part mesurée est citée"),
    ]


def main() -> int:
    texte = normaliser((RACINE / "README.md").read_text(encoding="utf-8"))
    echecs = 0

    print("Chiffres déterministes")
    for quoi, attendu in controles():
        if normaliser(attendu) in texte:
            print(f"  ok     {quoi}")
        else:
            echecs += 1
            print(f"  ECHEC  {quoi}")
            print(f"         le README devrait contenir : {attendu}")

    print("\nAffirmations, que les mesures bougent ou non")
    for quoi, vrai, detail in affirmations(texte):
        print(f"  {'ok    ' if vrai else 'ECHEC '} {quoi} — {detail}")
        echecs += not vrai

    print()
    if echecs:
        print(f"{echecs} point(s) ne correspondent plus au code.")
        print("Corriger le texte — ou dire pourquoi la mesure a changé.")
        return 1
    print("Le README correspond au code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
