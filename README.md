# Carbon Gate

Un pipeline d'intégration continue qui mesure son propre coût carbone — et qui
**refuse d'annoncer une économie qu'il ne peut pas prouver**.

```
python exemple.py                 # la chaîne entière sur de vrais processus
python -m pytest -q               # 102 tests
```

Aucune dépendance en dehors de la bibliothèque standard.

---

## Les deux règles

### La correction décide, le carbone mesure

Un job n'est **jamais** sauté parce qu'il est cher. Il est sauté parce que rien
de ce dont il dépend n'a changé ; le carbone sert ensuite à dire ce que cette
décision a épargné. Jamais l'inverse.

Décaler n'est pas sauter, et c'est pourquoi le second levier ne viole pas la
règle : un job **sans échéance** peut attendre l'heure propre sans que personne
ne perde rien. Ce n'est pas une question de justesse, c'est une question de
latence, et le carbone a le droit d'en décider.

| décision | qui décide |
|---|---|
| **si** un job tourne | la correction, jamais le carbone |
| **quand** il tourne, s'il n'a pas d'échéance | le carbone |

Un outil qui laisserait le carbone arbitrer troquerait de la justesse contre des
grammes, et il n'existe aucun seuil d'émission auquel il devient raisonnable de
ne pas lancer le scan de sécurité d'une livraison. Un test le vérifie : on rend
un job absurdement coûteux et la décision ne bouge pas d'un pouce.

### Un gain qu'on ne distingue pas de zéro n'est pas annoncé

```
3 job(s) sauté(s) — gain net établi : [0.00591 ; 0.0107] gCO2e
  (brut [0.00680 ; 0.0112], porte [0.000565 ; 0.000886])
```

Et quand la porte coûte autant qu'elle épargne :

```
1 job(s) sauté(s). **Aucun gain net n'est annoncé** : [-0.00247 ; 0.00251]
gCO2e contient zéro — ce que la porte épargne ne se distingue pas de ce
qu'elle coûte. Les jobs restent sautés : c'est le changement qui le
justifie, pas le carbone.
```

---

## Le résultat : 93 % de l'empreinte n'est pas de l'électricité

Le discours du domaine est « déplacez vos calculs vers les heures où le réseau
est propre ». Mesuré sur cinq jobs de CI réels, le 14/10/2026, en gCO2e :

| job | O — électricité | M — matériel | M/O |
|---|---|---|---|
| tests-unitaires | [0,0000795 ; 0,000459] | [0,00124 ; 0,00562] | 12,7x |
| lint | [0,0000311 ; 0,000184] | [0,000535 ; 0,00241] | 13,7x |
| construire-doc | [0,0000339 ; 0,000199] | [0,000594 ; 0,00268] | 14,1x |
| scan-securite | [0,0000430 ; 0,000251] | [0,000712 ; 0,00321] | 13,4x |
| construire-image | [0,0000966 ; 0,000546] | [0,00152 ; 0,00687] | 13,1x |

**93,0 % du total est le matériel amorti, pas l'électricité consommée.**

Donc déplacer un job vers une heure propre n'agit que sur **7 %** de son
empreinte. Mesuré sur le même pipeline : décaler de 19 h à 23 h épargne
`[0,0000212 ; 0,0000983]` gCO2e, là où sauter trois jobs inutiles épargne
`[0,00351 ; 0,0161]` — **environ cent soixante fois plus.**

La conclusion est brutale et elle tient : **ne pas lancer bat attendre.** Ne pas
lancer efface O *et* M ; attendre ne touche que O.

### Et ce résultat résiste à ses propres paramètres

| part de machine facturée | part du matériel |
|---|---|
| 100 % | 98.0 % |
| 25 % | 92.3 % |
| 5 % | 70.7 % |
| 1 % | 32.5 % |

Le matériel domine dès que le job est facturé plus de **2.1 % de la machine**,
très en deçà de toute allocation plausible d'un exécuteur partagé. Sur des
durées de vie de 2 à 8 ans, le rapport va de 23.5x à 5.9x : il ne bascule jamais.

`python verifier_readme.py` recalcule tout ce qui précède et sort en erreur si le
texte a dérivé. Il a servi dès sa première exécution : les chiffres ci-dessus
dataient d'avant que la durée de vie et la part de machine deviennent des
intervalles, et tous avaient bougé.

> **Comment j'ai failli publier l'inverse.** Ma première comparaison utilisait
> une énergie que j'avais **inventée** — 0,0008 kWh, un ordre de grandeur pris
> au jugé. Elle disait que le décalage horaire rapportait dix fois plus que les
> jobs sautés. L'énergie réellement mesurée est mille fois plus petite, et la
> conclusion s'inverse. Un nombre plausible saisi à la main vaut moins que pas
> de nombre du tout, parce qu'on le défend.

## Pourquoi tout est un intervalle

La formule est celle d'ISO/IEC 21031:2024 — *Software Carbon Intensity* :

```
SCI = (O + M) par R,  avec  O = E × I
```

Le piège n'est pas la formule, il est dans **E**. Sur un exécuteur de CI
partagé, l'électricité consommée ne se mesure pas : les compteurs d'énergie du
processeur ne sont pas exposés, et on ignore sur quel matériel on tourne. Ce
qu'on observe vraiment, c'est du temps processeur et de la mémoire. Tout le
reste est une conversion par coefficient publié, et ces coefficients varient
d'un facteur trois.

Un score SCI est donc une **estimation**, pas une mesure. Le rendre à quatre
décimales serait un mensonge de précision.

D'où trois décisions :

| décision | ce qu'elle empêche |
|---|---|
| **Un coefficient ne se construit pas sans sa source** — le type lève si elle manque | un nombre dont on a perdu l'origine, qu'on ne peut ni contester ni mettre à jour |
| **L'affichage arrondit vers l'extérieur** | `[0,3078 ; 1,2341]` affiché `[0.308 ; 1.23]`, qui ne contient plus les bornes réelles |
| **Un job sans mesure est exclu des deux totaux** | une économie fantôme, offerte par ce qu'on ignore |

---

## Ce que la mesure mesure vraiment

`mesure.py` échantillonne le processus pendant qu'il tourne — `/proc` sous
Linux, `ctypes` sous Windows — et déclare ce qu'il n'a pas pu voir.

**La mémoire est intégrée, pas majorée.** `ru_maxrss` donne un *pic* ;
multiplier le pic par la durée surestime, parfois beaucoup pour un processus qui
ne monte qu'à la fin. On intègre par trapèzes sur les relevés. Un test vérifie
que le résultat passe bien **sous** pic × durée.

**La période d'échantillonnage a été choisie par la mesure** (relevé complet
dans le module) :

| période | échantillons | temps processeur lu |
|---|---|---|
| 0,2 s | 5 | 0,797 s ← sous-estimé |
| 0,1 s | 9 | 0,797 s ← sous-estimé |
| 0,05 s | 19 | 0,906 s |
| **0,01 s** | **86** | **0,906 s** |
| 0,002 s | 333 | 0,938 s |

Ce que coûte la sonde à cette finesse, **je ne sais pas le dire** : sept courses
à 0,2 s donnent une horloge médiane de 0,891 s (écart-type 0,031) contre 0,937 s
(écart-type 0,016) à 0,01 s. Les plages se recouvrent, donc l'écart de 46 ms
n'est pas distinguable de la variation d'une course à l'autre. Affirmer « la
sonde ne coûte rien » serait tirer d'une mesure une conclusion qu'elle ne porte
pas.

**Un échantillonnage trop maigre n'est pas une mesure**, et le module le dit
plutôt que de rendre un chiffre :

```
lint : période d'échantillonnage (0.05 s) trop grande devant la durée
(0.422 s) : le temps processeur est sous-estimé
```

### Étalonnage

Relevé du 14/10/2026, Windows :

| consigne | mesuré | écart |
|---|---|---|
| 0,5 s | 0,500 s | +0,0 % |
| 1,0 s | 0,984 s | −1,6 % |
| 2,0 s | 2,000 s | +0,0 % |

Un processus qui dort une seconde consomme 0,078 s de processeur — le démarrage
de l'interpréteur, et rien d'autre.

---

## Le défaut que les tests verts cachaient

L'économie était d'abord calculée comme `coût complet − coût retenu`. Les deux
sont égaux en arithmétique ordinaire et **pas du tout** en arithmétique
d'intervalles : la soustraction ignore que les jobs communs aux deux totaux sont
les mêmes, donc leur incertitude s'ajoute au lieu de s'annuler. Mesuré sur un
pipeline dominé par un gros job, elle rendait `[-3,59 ; 3,60]` g là où
l'économie vaut un millième de gramme **et dont le signe est certain**.

J'avais même écrit que ce pessimisme « jouait du bon côté », puisqu'il rend la
porte plus prudente. C'était faux comme consolation : **taire une économie
réelle est la faute symétrique d'en inventer une.**

L'économie brute est donc la somme des jobs sautés. Et l'incertitude a retrouvé
l'endroit où elle a vraiment un mot à dire : la porte se compte elle-même, et
comparer ce qu'elle épargne à ce qu'elle consomme oppose enfin deux grandeurs
indépendantes.

---

## Carte du dépôt

| fichier | ce qu'il tient |
|---|---|
| `carbongate/intervalle.py` | l'arithmétique d'intervalles, et sa vérification par Monte-Carlo |
| `carbongate/sci.py` | la formule ISO/IEC 21031:2024, et les coefficients qui exigent leur source |
| `carbongate/mesure.py` | la sonde : `/proc`, `ctypes`, et ce qu'elle déclare ne pas savoir |
| `carbongate/reseau.py` | l'intensité horaire du réseau, et le levier « quand lancer » |
| `carbongate/porte.py` | la décision, et le verdict |
| `carbongate/rapport.py` | le rapport, sources et réserves comprises |
| `exemple.py` | la chaîne entière sur de vrais processus |

## Licence

MIT.
