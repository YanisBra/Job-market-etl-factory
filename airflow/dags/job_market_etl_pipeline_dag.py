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
    description="Pipeline ETL (squelette) apres ingestion raw",
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
            "echo 'TODO: ajouter src/transform_offers.py pour nettoyage + dedup depuis MinIO raw'"
        ),
    )

    load_to_postgres = BashOperator(
        task_id="load_to_postgres",
        bash_command=(
            "echo 'TODO: ajouter src/load_offers_postgres.py avec UPSERT (UNIQUE source, source_offer_id)'"
        ),
    )

    end = EmptyOperator(task_id="end")

    start >> normalize_raw >> load_to_postgres >> end
