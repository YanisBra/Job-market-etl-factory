# job-market-etl-factory

Pipeline data local pour analyser le marche de l'emploi data.

Stack:
- Orchestration: Airflow
- Data Lake: MinIO
- Base SQL: Postgres
- Monitoring: Prometheus + Grafana
- Ingestion API: France Travail
- Ingestion Scraping: Welcome To The Jungle (WTTJ)

## 1) Arborescence

```text
.
├── airflow/
│   └── dags/
│       ├── france_travail_raw_ingestion_dag.py
│       ├── wttj_raw_ingestion_dag.py
│       ├── hellowork_raw_ingestion_dag.py
│       └── job_market_etl_pipeline_dag.py
├── data/
│   └── raw/
├── docker-compose.yaml
├── scripts/
│   └── recover_airflow.sh
├── requirements.txt
├── .env.example
├── src/
│   ├── france_travail_collector.py
│   ├── welcome_to_the_jungle_scraper.py
│   ├── hellowork_scraper.py
│   ├── transform_offers.py
│   └── load_to_postgres.py
└── tests/
    ├── test_france_travail_collector.py
    ├── test_welcome_to_the_jungle_scraper.py
    └── test_hellowork_scraper.py
```

## 2) Prerequis

- Docker + Docker Compose
- Python 3.9+ (local)
- Acces API France Travail (client_id + client_secret)
- Git (optionnel, pour cloner/versionner)

Compatibilite:
- macOS: Docker Desktop + Terminal zsh/bash
- Linux: Docker Engine + plugin Compose
- Windows: Docker Desktop + PowerShell (ou WSL2 recommande)

## 3) Environnement local Python (venv)

Le venv est recommande pour eviter les conflits de dependances.

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Windows (PowerShell):

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Ensuite, utilise toujours:
- `.venv/bin/python ...`
- 
- ou active le venv avec `source .venv/bin/activate`
- Windows: `.venv\Scripts\python.exe ...`

## 4) Configuratioquen `.env`

Le projet lit automatiquement le fichier `.env`.

Copie d'abord le template reproductible:

macOS / Linux:

```bash
cp .env.example .env
```

Windows (PowerShell):

```powershell
Copy-Item .env.example .env
```

Variables critiques:
- MinIO: `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_BUCKET_RAW_API`, `MINIO_BUCKET_RAW_SCRAP`
- France Travail: `FRANCE_TRAVAIL_CLIENT_ID`, `FRANCE_TRAVAIL_CLIENT_SECRET`
- Airflow: `AIRFLOW_*`
- Postgres: `POSTGRES_*`
- Airflow deps: `AIRFLOW_ADDITIONAL_REQUIREMENTS` (recommande: `minio psycopg2-binary`)

Buckets MinIO utilises (noms actuels):
- API France Travail: `raw-api`
- Scraping WTTJ: `raw-scrap`

Important securite:
- Ne jamais commit des credentials reels.
- Utilise un `.env` local et rotate les secrets si exposes.

## 5) Lancer l'infrastructure (reproductible)

Commande identique sur macOS/Linux/Windows:

```bash
docker compose up -d
```

Verifier:

```bash
docker compose ps
```

Arreter:

```bash
docker compose down
```

Reset complet (volumes supprimes):

```bash
docker compose down -v
```

## 6) URLs des services

- Airflow: [http://localhost:8088](http://localhost:8088)
- MinIO Console: [http://localhost:9001](http://localhost:9001)
- MinIO API: [http://localhost:9000](http://localhost:9000)
- Prometheus: [http://localhost:9090](http://localhost:9090)
- Grafana: [http://localhost:3000](http://localhost:3000)
- Postgres: `localhost:5433`

## 7) Ingestion locale (sans Airflow)

### 7.1 France Travail -> `raw-api`

macOS / Linux:

```bash
.venv/bin/python src/france_travail_collector.py \
  --pages 3 \
  --page-size 50
```

Windows (PowerShell):

```powershell
.venv\Scripts\python.exe src\france_travail_collector.py --pages 3 --page-size 50
```

Sorties:
- Fichier local: `data/raw/france_travail_offers.json`
- MinIO: `raw-api/france_travail/YYYY/MM/DD/offers_<timestamp>.json`

Mode debug local sans upload MinIO:

macOS / Linux:

```bash
.venv/bin/python src/france_travail_collector.py --skip-minio
```

Windows (PowerShell):

```powershell
.venv\Scripts\python.exe src\france_travail_collector.py --skip-minio
```

### 7.2 WTTJ -> `raw-scrap`

macOS / Linux:

```bash
.venv/bin/python src/welcome_to_the_jungle_scraper.py \
  --query "" \
  --page 1 \
  --max-pages 3 \
  --detail-delay-seconds 0.1
```

Windows (PowerShell):

```powershell
.venv\Scripts\python.exe src\welcome_to_the_jungle_scraper.py --query "" --page 1 --max-pages 3 --detail-delay-seconds 0.1
```

Sorties:
- Fichier local: `data/raw/wttj_offers.json`
- MinIO: `raw-scrap/welcome_to_the_jungle/YYYY/MM/DD/offers_<timestamp>.json`

Mode debug local sans upload MinIO:

macOS / Linux:

```bash
.venv/bin/python src/welcome_to_the_jungle_scraper.py --skip-minio
```

### 7.3 HelloWork -> `raw-scrap`

macOS / Linux:

```bash
.venv/bin/python src/hellowork_scraper.py \
  --query "data" \
  --max-pages 2
```

Windows (PowerShell):

```powershell
.venv\Scripts\python.exe src\hellowork_scraper.py --query "data" --max-pages 2
```

Sorties:
- Fichier local: `data/raw/hellowork_offers.json`
- MinIO: `raw-scrap/hellowork/YYYY/MM/DD/offers_<timestamp>.json`

## 8) ETL local (processed -> Postgres)

### 8.1 Transformer les donnees brutes

```bash
.venv/bin/python src/transform_offers.py \
  --france-travail-input data/raw/france_travail_offers.json \
  --wttj-input data/raw/wttj_offers.json \
  --hellowork-input data/raw/hellowork_offers.json \
  --output data/processed/unified_offers.json
```

Sortie:
- `data/processed/unified_offers.json` (source unifiee nettoyee)

### 8.2 Charger dans Postgres

```bash
.venv/bin/python src/load_to_postgres.py --input data/processed/unified_offers.json
```

Resultat:
- table `offers_unified` creee si absente
- `UPSERT` sur la cle `(source, source_offer_id, external_url)`

## 9) Airflow DAGs

DAGs ingestion actifs:
- `france_travail_raw_ingestion`
- `wttj_raw_ingestion`
- `hellowork_raw_ingestion`

DAG ETL:
- `job_market_etl_pipeline`

Notes:
- Les DAGs ingestion executent directement les scripts de `src/` via `BashOperator`.
- Les variables sont injectees via `docker-compose` (pas besoin de Airflow Variables UI pour ingestion).
- Le DAG ETL attend la fin des 3 DAGs ingestion (`france_travail_raw_ingestion`, `wttj_raw_ingestion` et `hellowork_raw_ingestion`) via `ExternalTaskSensor`.
- Le DAG ETL execute ensuite `transform_offers.py` puis `load_to_postgres.py`.

## 10) Lancer et verifier un run Airflow

1. Ouvrir Airflow UI et activer les DAGs (`toggle ON`).
2. Trigger manuel du DAG (bouton Play).
3. Verifier les tasks en `success` dans Grid/Graph.
4. Verifier les objets dans MinIO (`raw-api` et `raw-scrap`).

Exemple verification CLI:

```bash
docker compose exec -T airflow-scheduler airflow dags list
docker compose exec -T airflow-scheduler airflow tasks states-for-dag-run france_travail_raw_ingestion <dag_run_id>
docker compose exec -T airflow-scheduler airflow tasks states-for-dag-run wttj_raw_ingestion <dag_run_id>
docker compose exec -T airflow-scheduler airflow tasks states-for-dag-run hellowork_raw_ingestion <dag_run_id>
```

Verification Postgres (nouvelles donnees + pas de doublons):

```bash
docker compose exec -T postgres psql -U airflow -d airflow -c "select count(*) as total from offers_unified;"
docker compose exec -T postgres psql -U airflow -d airflow -c "select source, count(*) from offers_unified group by source order by source;"
docker compose exec -T postgres psql -U airflow -d airflow -c "select source, source_offer_id, count(*) from offers_unified group by source, source_offer_id having count(*) > 1;"
docker compose exec -T postgres psql -U airflow -d airflow -c "select source, external_url, count(*) from offers_unified group by source, external_url having count(*) > 1;"
```

## 11) Tests

Lancer tous les tests:

macOS / Linux:

```bash
.venv/bin/python -m pytest -q
```

Windows (PowerShell):

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Couverture actuelle:
- Parsing France Travail
- Upload MinIO mocke France Travail
- Parsing/enrichissement WTTJ
- Parsing HelloWork (JSON-LD)
- Champs `salaire`, `experienceRequise`, `niveauEtude`
- Collecte multi-pages + dedup

## 12) CI GitLab (build + test)

Pipeline dans `.gitlab-ci.yml`:
- validation compose
- quality (`ruff`, `black --check`)
- tests (`pytest`)

## 12.1) Prometheus/Grafana - comparaison MinIO vs Postgres

Objectif:
- courbe 1: volume brut `raw-api` (FT)
- courbe 2: volume brut `raw-wttj`
- courbe 3: volume brut `raw-hw`
- courbe 4: volume en base `offers_unified`

Mise en place:
- `load_to_postgres.py` renseigne `etl_metrics` a chaque run ETL
- `postgres-exporter` expose ces compteurs a Prometheus via:
  - `monitoring/postgres_exporter/queries.yaml`
- Grafana est provisionne automatiquement via:
  - `monitoring/grafana/provisioning/datasources/prometheus.yaml`
  - `monitoring/grafana/provisioning/dashboards/dashboard.yaml`
  - `monitoring/grafana/dashboards/job_market_etl_overview.json`

Relancer les services apres modification:

```bash
docker compose up -d --force-recreate postgres-exporter prometheus grafana
```

Verifier que les metriques existent:

```bash
docker compose exec -T postgres-exporter sh -lc "wget -qO- http://localhost:9187/metrics | grep job_market_"
```

Le dashboard est cree automatiquement dans le dossier Grafana `Job Market`:
- `Job Market ETL - Monitoring`

Si tu veux tester manuellement dans Explore (datasource Prometheus):

```promql
job_market_raw_api_count_value
```

```promql
job_market_raw_scrap_count_value
```

```promql
job_market_postgres_count_value
```

## 12.2) Analyse métier (Market Analysis) - Dashboard Postgres

Contrairement au monitoring technique qui utilise Prometheus, l'analyse métier interroge directement la base **PostgreSQL** pour offrir une vision plus fine du marché.

Mise en place:
- Grafana est connecte directement a Postgres via `monitoring/grafana/provisioning/datasources/postgres.yaml`.
- Le dashboard métier est provisionne via `monitoring/grafana/dashboards/job_market_analysis.json`.

Indicateurs disponibles:
- **Répartition par Type de Contrat** (Pie Chart) : CDI, CDD, Intérim, Saisonnier, etc.
- **Top 10 des Villes** (Bar Chart) : Volume d'offres par localisation (codes INSEE).
- **Top 10 des Recruteurs** (Bar Chart) : Les entreprises les plus actives.
- **Volume d'offres par Jour** (Bar Chart) : Évolution quotidienne des publications.

Accès:
- Dans Grafana, ouvrir le dashboard : `Job Market Analysis - Métier`.

## 13) Troubleshooting rapide

### Aucun DAG visible dans Airflow UI

```bash
docker compose exec -T airflow-scheduler airflow dags list
docker compose exec -T airflow-webserver airflow sync-perm
docker compose restart airflow-webserver airflow-scheduler
```

Correction automatique (recommandee):

macOS / Linux:

```bash
./scripts/recover_airflow.sh
```

Windows:
- option recommandee: lancer ce script depuis WSL2/Git Bash
- sinon utiliser la sequence manuelle ci-dessous (PowerShell):

```powershell
docker compose up -d --force-recreate airflow-webserver airflow-scheduler
docker compose exec -T airflow-webserver airflow sync-perm
docker compose exec -T airflow-scheduler airflow dags list
```

Ce script:
- verifie les fichiers DAG locaux
- recree `airflow-webserver` et `airflow-scheduler` pour forcer le remount
- sync les permissions Airflow
- affiche la liste des DAGs detectes

### Task Airflow en echec

- Ouvrir les logs de la task dans l'UI.
- Verifier credentials dans `.env`.
- Verifier que MinIO est up: `docker compose ps`.

### MinIO ne montre pas le fichier attendu

- France Travail ecrit dans `raw-api`.
- WTTJ ecrit dans `raw-scrap`.
