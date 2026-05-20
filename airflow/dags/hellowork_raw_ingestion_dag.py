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
    dag_id="hellowork_raw_ingestion",
    description="Scraping brut HelloWork puis upload MinIO (raw-scrap)",
    start_date=datetime(2026, 3, 23),
    schedule="0 */3 * * *",
    catchup=False,
    default_args=default_args,
    max_active_runs=1,
    tags=["job-market", "raw", "hellowork", "scraping", "minio"],
) as dag:
    start = EmptyOperator(task_id="start")

    scrape_hellowork = BashOperator(
        task_id="scrape_hellowork",
        bash_command="""
        set -euo pipefail

        echo "Starting HelloWork scraping ingestion..."
        cd /opt/airflow/src
        python hellowork_scraper.py --max-pages 3
        echo "HelloWork scraping ingestion finished."
        """,
        execution_timeout=timedelta(minutes=15),
    )

    end = EmptyOperator(task_id="end")

    start >> scrape_hellowork >> end
