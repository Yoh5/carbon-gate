# Ce qu'un agent doit savoir avant de toucher à ce dépôt

GitLab Duo lit ce fichier pour donner du contexte à ses flows et à ses agents.
Il est donc écrit pour eux, et il dit d'abord ce qui est interdit : un agent
qui connaît les règles avant les commandes se trompe moins souvent.

## La règle qui prime sur toutes les autres

**Un job n'est jamais sauté parce qu'il est cher.** Il est sauté parce que rien
de ce dont il dépend n'a changé. Le carbone sert ensuite à dire ce que cette
décision a épargné — jamais à la prendre.

Il n'existe aucun seuil d'émission auquel il devient raisonnable de ne pas
lancer le scan de sécurité d'une livraison. `scan-securite` porte
`toujours: true` dans `carbongate.json` et aucune règle du pipeline ne le
garde ; `tests/test_pipeline.py` vérifie que ça reste vrai.

Si un changement laisse un chiffre d'émission influencer ce qui tourne, c'est
un défaut bloquant, et c'est la première chose à signaler.

## La deuxième règle : un gain qu'on ne distingue pas de zéro n'est pas annoncé

Tout est intervalle, et ce n'est pas de la prudence décorative. L'énergie d'un
exécuteur partagé **ne se mesure pas** : les compteurs du processeur ne sont
pas exposés et on ignore sur quel matériel on tourne. Les coefficients publiés
varient d'un facteur trois.

Donc :

- **Ne jamais aplatir un intervalle en un nombre.** C'est la seule déformation
  qui rend tout le chiffre indéfendable.
- Un coefficient sans source **ne se construit pas** : le type lève. C'est
  voulu, ne le contourne pas.
- Un job sans mesure est exclu des deux totaux. Ne pas savoir ce qu'il coûte ne
  doit pas se traduire par un gain gratuit.

## Deux distinctions que ce dépôt a déjà payées pour apprendre

**« Rien n'a changé » n'est pas « on ne sait pas ce qui a changé ».** Le premier
autorise à sauter, le second l'interdit. Elles ont été encodées un temps par la
même liste vide, et tous les jobs se faisaient sauter sur un clone superficiel.
`decider(..., changements_connus=False)` existe pour ça.

**Une liste vide parce qu'un contrôle a tourné n'est pas une liste vide parce
qu'il n'a rien cherché.** Quand un écart ne peut pas être vérifié, on le dit ;
on ne rend pas un résultat vide qui se lira « rien à signaler ».

## Comment lancer les choses

```
python -m pytest -q                      # 140 tests, aucune dépendance d'exécution
python verifier_readme.py                # échoue si un chiffre du README a dérivé
python -m carbongate decide              # qui tourne, et ce que sauter épargne
python -m carbongate mesure --nom X -- <commande>
python -m carbongate rapport
python exemple.py                        # la chaîne entière sur de vrais processus
```

`verifier_readme.py` fait autorité sur les chiffres publiés. Si tu changes un
coefficient ou une mesure, lance-le : il recalcule ce que le README affirme et
sort en erreur si le texte ne suit plus.

## La carte du dépôt

| fichier | ce qu'il tient |
|---|---|
| `carbongate/intervalle.py` | l'arithmétique d'intervalles |
| `carbongate/sci.py` | ISO/IEC 21031:2024, et les coefficients qui exigent leur source |
| `carbongate/mesure.py` | la sonde `/proc` et `ctypes`, et ce qu'elle déclare ne pas savoir |
| `carbongate/porte.py` | la décision |
| `carbongate/reseau.py` | l'intensité horaire du réseau, et le levier « quand lancer » |
| `carbongate/rapport.py` | le rapport, sources et réserves comprises |
| `carbongate/cli.py` | ce qu'un pipeline appelle |
| `.gitlab-ci.yml` | le pipeline qui se mesure lui-même |
| `.gitlab/duo/flows/` | le flow post-code |

## Le résultat, pour ne pas le réapprendre

**93,0 % de l'empreinte d'un job de CI est du matériel amorti, pas de
l'électricité.** Déplacer un job vers une heure propre n'agit donc que sur 7 %.
Sauter trois jobs épargne environ cent soixante fois plus que décaler de 19 h à
23 h. Ne pas lancer efface les deux ; attendre ne touche que l'électricité.

Le résultat tient : le matériel domine dès 2,1 % de la machine, et sur des
durées de vie de 2 à 8 ans le rapport va de 23,5× à 5,9× sans jamais basculer.

## Ce qui n'est pas fait, et qu'il ne faut pas prétendre

Pas de normalisation ni de mise à l'échelle. Pas de mesure d'énergie réelle —
c'est estimé, et le rapport le dit à chaque fois. Le levier horaire refuse
d'extrapoler au-delà de la série qu'on lui donne.
