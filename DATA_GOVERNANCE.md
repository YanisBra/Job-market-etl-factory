# Data Governance - Job Market ETL Factory

## 1) Objectif
Assurer des donnees:
- fiables (qualite)
- tracables (on sait d'ou elles viennent)
- compréhensibles (avant/apres traitement)
- securisees (secrets et acces)

## 2) D'ou viennent les donnees
Sources du projet:
- API France Travail (API)
- Scraping Welcome to the Jungle (WTTJ) (Scraping)

Collecte brute:
- France Travail -> `data/raw/france_travail_offers.json`
- WTTJ -> `data/raw/wttj_offers.json`

Stockage brut Data Lake (MinIO):
- bucket `raw-api` pour API
- bucket `raw-scrap` pour scraping
- organisation par date (annee/mois/jour)

## 3) Comment retrouver les donnees
### Avant traitement (raw)
Local:
- `data/raw/france_travail_offers.json`
- `data/raw/wttj_offers.json`

MinIO:
- console: `http://localhost:9001`
- bucket `raw-api` et `raw-scrap`

### Apres traitement (processed + SQL)
Fichier transforme:
- `data/processed/unified_offers.json`

Table finale SQL:
- Postgres table `offers_unified`

Acces SQL exemple:
```sql
SELECT * FROM offers_unified ORDER BY id DESC LIMIT 50;
```

## 4) Comprendre la transformation
Script ETL:
- `src/transform_offers.py`

Ce qu'il fait:
- harmonise les deux sources vers un schema unique
- conserve la source (`france_travail` ou `welcome_to_the_jungle`)
- normalise les champs principaux (`title`, `company_name`, `city`, etc.)
- conserve le brut d'origine dans `raw_offer`
- deduplication sur:
  - `source + source_offer_id + external_url`

Chargement SQL:
- `src/load_to_postgres.py`
- upsert dans `offers_unified` (mise a jour si deja present)

## 5) Regles qualite obligatoires
- `source`, `source_offer_id`, `title` non vides
- `source` dans `{france_travail, welcome_to_the_jungle}`
- `alternance` booleen
- pas de doublons sur la cle metier:
  - `source + source_offer_id + external_url`

## 6) Controles apres chaque run
Volumes:
```sql
SELECT COUNT(*) AS total FROM offers_unified;

SELECT source, COUNT(*) AS total
FROM offers_unified
GROUP BY source
ORDER BY source;
```

Doublons:
```sql
SELECT source, source_offer_id, COUNT(*)
FROM offers_unified
GROUP BY source, source_offer_id
HAVING COUNT(*) > 1;
```

Comparaison brute vs final:
- MinIO = volume d'objets bruts collectes
- Postgres = volume d'offres nettoyees chargees
- l'ecart est normal si dedup/filtrage/nettoyage

## 7) Securite
- secrets uniquement dans `.env`
- `.env` exclu du Git
- `.env.example` sans credentials reels
- token expose = revoke immediat

## 8) Tracabilite et incidents
Traçabilite:
- DAGs Airflow + logs de tasks
- historique Git/GitLab CI

En cas d'echec:
1. Identifier la task en erreur (Airflow)
2. Lire le log
3. Corriger la cause
4. Relancer la task / le DAG
5. Refaire les controles SQL
