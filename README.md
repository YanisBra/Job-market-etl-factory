# job-market-etl-factory

Pipeline data local pour analyser le marche de l'emploi data.

Stack:
- Orchestration: Airflow
- Data Lake: MinIO
- Base SQL: Postgres
- Monitoring: Prometheus + Grafana
- Ingestion API: France Travail
- Ingestion Scraping: HelloWork

## 1) Arborescence

```text
.
├── airflow/
│   └── dags/
│       ├── france_travail_raw_ingestion_dag.py
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
│   ├── hellowork_scraper.py
│   ├── transform_offers.py
│   └── load_to_postgres.py
└── tests/
    ├── test_france_travail_collector.py
    └── test_hellowork_scraper.py
```

## 2) Prerequis

- Docker + Docker Compose
- Python 3.9+ (local)
- Acces API France Travail (client_id + client_secret)
- Git (optionnel, pour cloner/versionner)

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

Buckets MinIO utilises (noms actuels):
- API France Travail: `raw-api`
- Scraping HelloWork: `raw-scrap`

## 5) Lancer l'infrastructure

```bash
docker compose up -d
```

## 6) URLs des services

- Dashboard: [http://localhost:8501](http://localhost:8501)
- Airflow: [http://localhost:8088](http://localhost:8088)
- MinIO Console: [http://localhost:9001](http://localhost:9001)
- Prometheus: [http://localhost:9090](http://localhost:9090)
- Grafana: [http://localhost:3000](http://localhost:3000)
- Postgres: `localhost:5433`

## 7) Ingestion locale (sans Airflow)

### 7.1 France Travail -> `raw-api`

```bash
.venv/bin/python src/france_travail_collector.py --pages 3 --page-size 50
```

Sorties:
- Fichier local: `data/raw/france_travail_offers.json`
- MinIO: `raw-api/france_travail/YYYY/MM/DD/offers_<timestamp>.json`

### 7.2 HelloWork -> `raw-scrap`

```bash
.venv/bin/python src/hellowork_scraper.py --query "data" --max-pages 2
```

Sorties:
- Fichier local: `data/raw/hellowork_offers.json`
- MinIO: `raw-scrap/hellowork/YYYY/MM/DD/offers_<timestamp>.json`

## 8) ETL local (processed -> Postgres)

### 8.1 Transformer les donnees brutes

```bash
.venv/bin/python src/transform_offers.py \
  --france-travail-input data/raw/france_travail_offers.json \
  --hellowork-input data/raw/hellowork_offers.json \
  --output data/processed/unified_offers.json
```

### 8.2 Charger dans Postgres

```bash
.venv/bin/python src/load_to_postgres.py --input data/processed/unified_offers.json
```

## 9) Airflow DAGs

DAGs ingestion actifs:
- `france_travail_raw_ingestion`
- `hellowork_raw_ingestion`

DAG ETL:
- `job_market_etl_pipeline`

## 10) Tests

Lancer tous les tests:

```bash
.venv/bin/python -m pytest -q
```

## 11) Prometheus/Grafana

Objectif:
- courbe 1: volume brut `raw-api` (FT)
- courbe 2: volume brut `raw-hw`
- courbe 3: volume en base `offers_unified`

Le dashboard est cree automatiquement dans Grafana: `Job Market ETL - Monitoring`.
