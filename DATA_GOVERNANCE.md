# Data Governance - Job Market ETL Factory

## 1) Objectif
Assurer des données :
- **fiables** (qualité)
- **traçables** (on sait d'où elles viennent)
- **compréhensibles** (avant/après traitement)
- **sécurisées** (secrets et accès)

## 2) D'où viennent les données
Sources du projet :
- API France Travail (API)
- Scraping HelloWork (Scraping)

Collecte brute :
- France Travail -> `data/raw/france_travail_offers.json`
- HelloWork -> `data/raw/hellowork_offers.json`

Stockage brut Data Lake (MinIO) :
- bucket `raw-api` pour l'API France Travail
- bucket `raw-scrap` pour le scraping HelloWork
- organisation par source et par date (année/mois/jour)

## 3) Comment retrouver les données
### Avant traitement (raw)
Local :
- `data/raw/france_travail_offers.json`
- `data/raw/hellowork_offers.json`

MinIO :
- console : `http://localhost:9001`
- buckets `raw-api` et `raw-scrap`

### Après traitement (processed + SQL)
Fichier transformé (unifié) :
- `data/processed/unified_offers.json`

Table finale SQL (Data Warehouse) :
- Postgres table `offers_unified`

Accès SQL exemple :
```sql
SELECT * FROM offers_unified ORDER BY id DESC LIMIT 50;
```

## 4) Comprendre la transformation
Script ETL :
- `src/transform_offers.py`

Ce qu'il fait :
- harmonise les deux sources vers un schéma unique
- conserve la source d'origine (`france_travail` ou `hellowork`)
- normalise les champs principaux (`title`, `company_name`, `city`, `experience_level`, `salary_annual`, etc.)
- calcule le salaire annuel estimé à partir des libellés et fourchettes (`salary_annual`)
- classe le niveau d'expérience (Junior, Intermédiaire, Sénior)
- conserve le JSON brut d'origine dans la colonne `raw_offer`
- effectue une déduplication basée sur une clé composite unique :
  - `source + source_offer_id + external_url`

### 4.1) Dictionnaire de données (Table `offers_unified`)

| Colonne | Type | Description | Mapping France Travail | Mapping HelloWork |
| :--- | :--- | :--- | :--- | :--- |
| `source` | TEXT | Origine de la donnée | 'france_travail' | 'hellowork' |
| `source_offer_id` | TEXT | ID technique chez le partenaire | `id` | `id` (format HW\_...) |
| `external_url` | TEXT | Lien direct vers l'offre | `origineOffre.urlOrigine` | `origineOffre.urlOrigine` |
| `title` | TEXT | Intitulé brut du poste | `intitule` | `intitule` |
| `title_clean` | TEXT | Intitulé nettoyé (minuscule, sans H/F) | Calculé (`_clean_job_title`) | Calculé (`_clean_job_title`) |
| `company_name` | TEXT | Nom de l'entreprise | `entreprise.nom` | `entreprise.nom` |
| `city` | TEXT | Ville du poste | `lieuTravail.commune`/`libelle` | `lieuTravail.commune` |
| `postal_code` | TEXT | Code postal | `lieuTravail.codePostal` | `lieuTravail.codePostal` |
| `contract_type_raw`| TEXT | Type de contrat d'origine | `typeContrat` | `typeContrat` |
| `contract_type` | TEXT | Type de contrat normalisé | Calculé (mapping) | Calculé |
| `alternance` | BOOL | Flag alternance/apprentissage | `alternance` (bool) | `alternance` (bool) |
| `description` | TEXT | Texte complet de l'offre | `description` | `description` |
| `salary_label` | TEXT | Salaire au format texte | `salaire.libelle` | `salaire.libelle` |
| `salary_annual` | FLOAT | Salaire annuel estimé (€) | Calculé (`_annualize_salary`) | Calculé (`_annualize_salary`) |
| `salary_min` | FLOAT | Salaire minimum brut | N/A | `salaire.min` |
| `salary_max` | FLOAT | Salaire maximum brut | N/A | `salaire.max` |
| `salary_currency` | TEXT | Devise du salaire | N/A | `salaire.devise` |
| `experience_required`| TEXT | Expérience brute (texte ou mois) | `experienceExige` | `experienceRequise` |
| `experience_level`| TEXT | Catégorie (Junior, Inter, Sénior) | Calculé (`_categorize_experience`) | Calculé (`_categorize_experience`) |
| `education_level` | TEXT | Niveau d'étude requis | `niveauFormationLibelle`| `niveauEtude` |
| `created_at` | TEXT | Date de création de l'offre | `dateCreation` | `dateCreation` |
| `raw_offer` | JSONB | Donnée brute complète (audit) | Objet complet | Objet complet |

### 4.2) Logique de Concaténation et Unification (ELT)
Le projet utilise une approche moderne :
1. **Extraction (Data Lake)** : Chaque source est stockée dans son format propre dans MinIO (fichiers JSON).
2. **Alignement (Transformation)** : Le script `transform_offers.py` charge les données brutes, calcule les nouvelles métriques (salaires annuels, niveaux d'expérience, nettoyage des titres) et mappe les champs vers une structure unique.
3. **Consolidation (Data Warehouse)** : Lors du chargement dans PostgreSQL (`load_to_postgres.py`), si un doublon est détecté par la clé unique, l'offre existante est mise à jour (UPSERT). Cela garantit une base de données toujours fraîche sans perte d'informations.

### 4.3) Règles de Normalisation (Mapping des Contrats)
Pour garantir la lisibilité des analyses, notamment sur le Dashboard Streamlit, les codes de France Travail et les statuts HelloWork sont normalisés.

Exemples de normalisation France Travail :
| Code Source | Libellé Unifié |
| :--- | :--- |
| `CDI` | CDI |
| `CDD` | CDD |
| `MIS` | Intérim |
| `SAI` | Saisonnier |
| `LIB` | Indépendant |

Exemples de normalisation HelloWork :
| Libellé Source | Libellé Unifié |
| :--- | :--- |
| `FULL_TIME` | CDI |
| `TEMPORARY` | CDD |
| `INTERN` | Stage |
| `CONTRACTOR` | Alternance |

## 5) Règles qualité obligatoires
- Les champs `source`, `source_offer_id`, `title` ne doivent pas être vides.
- `source` doit appartenir à `{france_travail, hellowork}`.
- `alternance` doit être un booléen (`TRUE` ou `FALSE`).
- L'unicité est garantie sur la clé : `source + source_offer_id + external_url`.

## 6) Contrôles après chaque run (Monitoring)
**Métriques automatisées via Prometheus et Grafana :**
Les scripts ETL alimentent la table `etl_metrics`. Ces données sont exposées par `postgres-exporter` et visualisées dans Grafana pour suivre :
- Le nombre d'objets bruts dans MinIO (API vs Scraping).
- Le nombre total d'offres unifiées dans Postgres.

**Contrôles manuels SQL (Doublons et Volumes) :**
```sql
-- Volumes totaux
SELECT COUNT(*) AS total FROM offers_unified;

-- Répartition par source
SELECT source, COUNT(*) AS total
FROM offers_unified
GROUP BY source
ORDER BY source;

-- Vérification de l'absence de doublons stricts
SELECT source, source_offer_id, COUNT(*)
FROM offers_unified
GROUP BY source, source_offer_id
HAVING COUNT(*) > 1;
```
L'écart entre le volume brut collecté (MinIO) et le volume final (Postgres) est normal puisqu'il résulte du dédoublonnage et du filtrage.

## 7) Sécurité
- Les secrets (MinIO, Postgres, API France Travail) sont stockés **uniquement** dans `.env`.
- Le fichier `.env` est exclu du gestionnaire de version Git (via `.gitignore`).
- Un fichier `.env.example` sert de template sans exposer les vrais identifiants.
- Tout token ou secret malencontreusement exposé (sur GitHub/GitLab) doit être révoqué immédiatement.

## 8) Traçabilité et incidents
**Traçabilité :**
- L'orchestration est pilotée par **Airflow** (les DAGs assurent l'ordre d'exécution et stockent les logs des tâches).
- Le pipeline CI/CD et l'historique du code sont suivis via **GitLab**.

**En cas d'échec :**
1. Identifier la tâche en erreur sur l'interface Airflow (Webserver).
2. Analyser les logs spécifiques à cette tâche.
3. Corriger le code ou la configuration.
4. "Clear" le statut de la tâche dans Airflow pour la relancer.
5. Vérifier les métriques Grafana ou lancer les requêtes SQL de contrôle pour s'assurer du retour à la normale.
