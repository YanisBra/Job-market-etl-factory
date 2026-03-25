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
│   └── welcome_to_the_jungle_scraper.py
└── tests/
    ├── test_france_travail_collector.py
    └── test_welcome_to_the_jungle_scraper.py
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
- ou active le venv avec `source .venv/bin/activate`
- Windows: `.venv\Scripts\python.exe ...`

## 4) Configuration `.env`

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
- Airflow deps: `AIRFLOW_ADDITIONAL_REQUIREMENTS` (par defaut: `minio`)

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
  --keywords "data scientist" \
  --location "75" \
  --range "0-49"
```

Windows (PowerShell):

```powershell
.venv\Scripts\python.exe src\france_travail_collector.py --keywords "data scientist" --location "75" --range "0-49"
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
  --query "data scientist" \
  --page 1 \
  --max-pages 1 \
  --detail-delay-seconds 0.1
```

Windows (PowerShell):

```powershell
.venv\Scripts\python.exe src\welcome_to_the_jungle_scraper.py --query "data scientist" --page 1 --max-pages 1 --detail-delay-seconds 0.1
```

Sorties:
- Fichier local: `data/raw/wttj_offers.json`
- MinIO: `raw-scrap/welcome_to_the_jungle/YYYY/MM/DD/offers_<timestamp>.json`

Mode debug local sans upload MinIO:

macOS / Linux:

```bash
.venv/bin/python src/welcome_to_the_jungle_scraper.py --skip-minio
```

Windows (PowerShell):

```powershell
.venv\Scripts\python.exe src\welcome_to_the_jungle_scraper.py --skip-minio
```

## 8) Airflow DAGs

DAGs ingestion actifs:
- `france_travail_raw_ingestion`
- `wttj_raw_ingestion`

DAG ETL (placeholder pour la suite):
- `job_market_etl_pipeline`

Notes:
- Les DAGs ingestion executent directement les scripts de `src/` via `BashOperator`.
- Les variables sont injectees via `docker-compose` (pas besoin de Airflow Variables UI pour ingestion).

## 9) Lancer et verifier un run Airflow

1. Ouvrir Airflow UI et activer les DAGs (`toggle ON`).
2. Trigger manuel du DAG (bouton Play).
3. Verifier les tasks en `success` dans Grid/Graph.
4. Verifier les objets dans MinIO (`raw-api` et `raw-scrap`).

Exemple verification CLI:

```bash
docker compose exec -T airflow-scheduler airflow dags list
docker compose exec -T airflow-scheduler airflow tasks states-for-dag-run france_travail_raw_ingestion <dag_run_id>
docker compose exec -T airflow-scheduler airflow tasks states-for-dag-run wttj_raw_ingestion <dag_run_id>
```

## 10) Tests

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
- Champs `salaire`, `experienceRequise`, `niveauEtude`
- Collecte WTTJ multi-pages + dedup

## 11) CI GitLab (build + test)

Pipeline dans `.gitlab-ci.yml`:
- validation compose
- quality (`ruff`, `black --check`)
- tests (`pytest`)

## 12) Troubleshooting rapide

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
