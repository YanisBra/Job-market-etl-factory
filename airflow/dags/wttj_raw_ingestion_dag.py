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
    dag_id="wttj_raw_ingestion",
    description="Scraping brut WTTJ puis upload MinIO (raw-scrap)",
    start_date=datetime(2026, 3, 23),
    schedule="0 */3 * * *",
    catchup=False,
    default_args=default_args,
    max_active_runs=1,
    tags=["job-market", "raw", "wttj", "scraping", "minio"],
) as dag:
    start = EmptyOperator(task_id="start")

    scrape_wttj = BashOperator(
        task_id="scrape_wttj",
        bash_command="""
        set -euo pipefail

        echo "Starting WTTJ scraping ingestion..."
        cd /opt/airflow/src
        python welcome_to_the_jungle_scraper.py --page 1 --max-pages 1 --detail-delay-seconds 0.1
        echo "WTTJ scraping ingestion finished."
        """,
        execution_timeout=timedelta(minutes=15),
    )

    end = EmptyOperator(task_id="end")

    start >> scrape_wttj >> end

#
