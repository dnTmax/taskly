# Journal de décisions

[English](DECISIONS.md) | Français

Chaque décision indique ce qui a été choisi, pourquoi, et ce que cela coûte. Les dernières
sections listent les problèmes rencontrés pendant le projet et les limites connues.

## 1. Pourquoi des instances spot pour l'entraînement

**Décision.** Le modèle est entraîné sur une instance EC2 spot, lancée uniquement pour
l'entraînement et supprimée juste après.

**Pourquoi.** L'entraînement est court (environ 4 minutes), relançable et ne conserve aucun état
à préserver : c'est exactement le type de tâche adapté à la capacité spot. Les instances spot
sont bien moins chères que les instances à la demande, et rien ne tourne (ni n'est facturé)
entre deux entraînements. L'instance n'est jamais oubliée allumée, car le script de lancement la
supprime dans tous les cas.

**Compromis.** AWS peut refuser une demande spot (plus de capacité) ou reprendre l'instance à
tout moment.

## 2. Que se passe-t-il si l'entraînement est interrompu

| Situation | Ce que fait le système |
|---|---|
| Plus de capacité spot dans une zone (vu pour de vrai, `InsufficientInstanceCapacity`) | Le script essaie le second sous-réseau privé, puis d'autres types de machine (`t3a.large`, `m5.large`, `m6i.large`) |
| L'instance spot est reprise pendant l'entraînement | Le script détecte que l'instance a disparu et en démarre une nouvelle, pour 2 tentatives au total (`--attempts 2`) |
| Toute erreur ou interruption | L'instance est toujours supprimée (bloc `finally`) : rien ne continue à être facturé |
| Toutes les tentatives échouent | Le pipeline échoue avant l'étape de promotion : staging et production ne sont pas touchés |

Pourquoi une interruption ne peut pas endommager la production : le modèle n'est enregistré dans
MLflow qu'à la toute fin de l'entraînement, et une nouvelle version n'atteint `staging` qu'après
avoir passé la règle de validation, puis `production` après une approbation humaine.

Limites connues : un entraînement interrompu peut rester affiché `RUNNING` dans le suivi MLflow
(une exécution orpheline, sans effet sur le registre) ; il n'y a pas de points de reprise
(checkpoints), car relancer un entraînement de 4 minutes coûte moins que d'en construire ; il n'y
a pas encore de repli automatique vers une instance à la demande (une suite naturelle si la
capacité spot reste rare).

## 3. Décisions de conception

| # | Décision | Pourquoi | Compromis |
|---|---|---|---|
| 1 | Instance d'entraînement pilotée par SSM, sans SSH | Aucun port ouvert, aucune clé à conserver, chaque commande est tracée par AWS | Nécessite l'agent SSM et un rôle d'instance |
| 2 | Postgres temporaire dans Docker sur l'instance d'entraînement, mot de passe aléatoire, lié à 127.0.0.1 | Le code d'entraînement lit ses données dans Postgres ; la base disparaît avec l'instance | Les données sont régénérées à chaque exécution (données synthétiques) |
| 3 | MLflow et Postgres dans le cluster plutôt que des services managés | Coût plus faible, simple à reconstruire | Un seul exemplaire, pas de haute disponibilité, pas de sauvegarde automatique |
| 4 | Mode proxy des artefacts MLflow (`--serve-artifacts`) avec S3 | Les clients (entraînement, API) n'ont pas besoin d'identifiants S3 ; seul le serveur MLflow a un rôle IAM (IRSA) | Tout le trafic des artefacts passe par le serveur |
| 5 | Alias MLflow (`staging`, `production`) plutôt que les stages | Les stages sont dépréciés ; les alias sont souples et visibles dans l'interface | Les alias doivent être déplacés explicitement |
| 6 | L'API charge le modèle au démarrage, depuis le registre, par un alias résolu en numéro de version | Le modèle n'est jamais intégré à l'image ; changer de version ne demande aucune modification de manifeste ; pas de course si l'alias bouge pendant le chargement | Un redémarrage est nécessaire pour prendre une nouvelle version (assuré par la mise à jour progressive) |
| 7 | Une base Kustomize et deux surcharges, un namespace par environnement | Mêmes manifestes pour les deux environnements, seuls l'alias, le nombre de réplicas et l'alerte diffèrent | Un outil de plus à connaître |
| 8 | Règle de promotion déterministe (F1 macro d'au moins 0,70 et pas plus de 0,02 sous la production) | Prévisible, testable, vérifiable ; la décision n'est jamais déléguée à un modèle | Les seuils devront être ajustés avec des données réelles |
| 9 | Secrets créés au déploiement à partir de valeurs aléatoires, jamais dans Git | Rien de sensible dans le dépôt | Les Secrets Kubernetes ne sont qu'encodés en base64 ; le chiffrement EKS avec KMS n'est pas activé |
| 10 | GitHub Actions s'authentifie auprès d'AWS par OIDC | Aucune clé durable dans GitHub | La politique de confiance doit correspondre au format d'identité de GitHub (voir problèmes) |
| 11 | Moindre privilège pour le rôle du pipeline | La suppression est limitée aux instances étiquetées du projet, et le rôle transmis à l'instance est limité au rôle d'entraînement | `RunInstances`, `SendCommand` et `CreateTags` restent sur `*`, car AWS ne permet pas de les restreindre davantage |
| 12 | Mêmes versions de dépendances épinglées pour l'entraînement et le service | Un modèle doit se recharger avec les versions de bibliothèques qui l'ont créé | Les versions doivent être mises à jour ensemble |
| 13 | Mise à jour progressive avec `maxUnavailable: 0`, une sonde de disponibilité, un PodDisruptionBudget et une pause `preStop` de 10 secondes | Aucune requête perdue pendant une mise à jour, mesuré (voir problèmes) | Un pod met 10 secondes de plus à s'arrêter |
| 14 | Injection de pannes avec `SIMULATE_ERROR_RATE` | Permet de tester l'alerte à 5 % à la demande | Doit être retirée après le test |
| 15 | Alerte seulement en production | Les erreurs de staging sont attendues et ne doivent alerter personne | Staging n'a pas d'alerte |
| 16 | Une seule NAT gateway, aucun load balancer, accès par port-forward et NodePort restreint | Coût plus faible pour un environnement d'apprentissage | Pas de haute disponibilité ; pas destiné à une vraie production |

## 4. À propos des données

Le jeu de données est synthétique (généré par le code du dépôt) : une précision de 1,0 signifie
seulement que les classes sont faciles à séparer. Ce n'est pas une affirmation sur les
performances réelles, et les seuils de validation devraient être ajustés avec des données réelles.

## 5. Problèmes rencontrés et résolus

| Problème | Cause | Solution |
|---|---|---|
| GitHub ne pouvait pas assumer le rôle AWS (`Not authorized to perform sts:AssumeRoleWithWebIdentity`) | GitHub envoie désormais l'identité sous la forme `repo:propriétaire@id/dépôt@id:...` (avec des identifiants numériques immuables) au lieu de `repo:propriétaire/dépôt:...` ; les identifiants protègent contre quelqu'un qui reprendrait un nom supprimé ou renommé | Un workflow de diagnostic a affiché les vraies informations d'identité ; la politique de confiance autorise maintenant les deux formats (`github_repo_sub`). Déjà rencontré sur un projet précédent |
| La mise à jour « sans coupure » a perdu 9 requêtes sur 2021 (0,45 %) | Un pod en cours d'arrêt fermait son port avant que le service le retire de la rotation | Pause `preStop` de 10 secondes ; le même test de charge a ensuite donné 0 erreur sur 2011 |
| Capacité spot indisponible | Comportement normal du spot | Repli multi-zones et multi-types, observé à nouveau dans le vrai pipeline |
| Instance spot disparue d'elle-même | Les instances spot peuvent être reprises | Gestion des interruptions et suppression inconditionnelle |
| `InvalidInstanceID.NotFound` juste après le lancement d'une instance | AWS met quelques secondes à propager une nouvelle instance | Le script traite ce cas comme « en attente » |
| Le pipeline ne pouvait pas lister les buckets | Moindre privilège voulu | Le nom du bucket est calculé à partir du numéro de compte, sans permission supplémentaire |
| Autoscaler affichant `cpu: unknown`, et namespace bloqué en `Terminating` pendant un jour | Le metrics-server géré par EKS utilise le port 10251, que le groupe de sécurité des nœuds n'autorisait pas | Règle de groupe de sécurité dans Terraform ; repéré grâce à la colonne `TARGETS` de l'autoscaler |
| Ressource Terraform en double pour les permissions du pipeline | Les permissions existaient déjà dans un autre fichier | Doublon supprimé ; seul `ec2:DescribeSubnets` manquait |
| AWS CLI 2.31 sous Python 3.14 : « badly formed help string » | L'argparse de Python 3.14 refuse un `%` dans le texte d'aide | Appels faits avec boto3 sous Python 3.12 |
| Règle de groupe de sécurité refusée | Une apostrophe n'est pas autorisée dans une description | Description réécrite (rencontré deux fois) |
| MLflow en boucle de plantage, puis manque de mémoire | Incompatibilité SQLAlchemy ; trop de workers | SQLAlchemy épinglé à 2.0.36 ; 2 workers et 1 Gio |
| Formats de journaux mélangés | L'API écrivait déjà du JSON avec la version du modèle, mais uvicorn écrivait un journal d'accès en texte brut (sondes de santé) | Une ligne JSON par requête depuis l'API, et uvicorn lancé avec `--no-access-log` |

## 6. Suites possibles

Repli vers une instance à la demande quand il n'y a pas de capacité spot ; un agent IA de
validation qui lit les métriques d'entraînement avant la promotion (la règle déterministe
resterait la barrière finale) ; chiffrement des Secrets Kubernetes avec KMS ; sauvegardes de
Postgres ; données d'entraînement réelles.

---

Dally NTOLO, DevOps & Cloud
