from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator


default_args = {
    "owner": "yanis",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=2),
}


with DAG(
    dag_id="job_market_etl_pipeline",
    description="Pipeline ETL: nettoyage raw + chargement Postgres",
    start_date=datetime(2026, 3, 23),
    schedule=None,
    catchup=False,
    default_args=default_args,
    tags=["job-market", "etl", "postgres"],
) as dag:
    start = EmptyOperator(task_id="start")

    normalize_raw = BashOperator(
        task_id="normalize_raw",
        bash_command=(
            "cd /opt/airflow/src && "
            "python transform_offers.py "
            "--france-travail-input data/raw/france_travail_offers.json "
            "--wttj-input data/raw/wttj_offers.json "
            "--output data/processed/unified_offers.json"
        ),
    )

    load_to_postgres = BashOperator(
        task_id="load_to_postgres",
        bash_command=(
            "cd /opt/airflow/src && "
            "python load_to_postgres.py --input data/processed/unified_offers.json"
        ),
    )

    end = EmptyOperator(task_id="end")

    start >> normalize_raw >> load_to_postgres >> end
