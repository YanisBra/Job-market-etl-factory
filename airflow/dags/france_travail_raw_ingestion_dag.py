from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator


default_args = {
    "owner": "yanis",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
}


with DAG(
    dag_id="france_travail_raw_ingestion",
    description="Collecte brute France Travail puis upload MinIO (raw-api)",
    start_date=datetime(2026, 3, 23),
    schedule="0 */3 * * *",
    catchup=False,
    default_args=default_args,
    max_active_runs=1,
    tags=["job-market", "raw", "france-travail", "api", "minio"],
) as dag:
    start = EmptyOperator(task_id="start")

    collect_france_travail = BashOperator(
        task_id="collect_france_travail",
        bash_command="""
        set -euo pipefail

        echo "Starting France Travail ingestion..."
        cd /opt/airflow/src
        python france_travail_collector.py --pages 3 --page-size 50
        echo "France Travail ingestion finished."
        """,
        execution_timeout=timedelta(minutes=10),
    )

    end = EmptyOperator(task_id="end")

    start >> collect_france_travail >> end
