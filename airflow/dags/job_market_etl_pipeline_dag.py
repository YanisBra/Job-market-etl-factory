from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.models import DagRun
from airflow.operators.empty import EmptyOperator
from airflow.operators.bash import BashOperator
from airflow.sensors.external_task import ExternalTaskSensor
from airflow.utils.session import provide_session
from airflow.utils.state import DagRunState


default_args = {
    "owner": "yanis",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=2),
}


@provide_session
def _latest_success_logical_date(
    logical_date,
    external_dag_id: str,
    session=None,
    **_,
):
    dag_run = (
        session.query(DagRun)
        .filter(
            DagRun.dag_id == external_dag_id,
            DagRun.state == DagRunState.SUCCESS,
        )
        .order_by(DagRun.execution_date.desc())
        .first()
    )
    return dag_run.execution_date if dag_run else None


def _latest_france_travail_success(logical_date, **kwargs):
    return _latest_success_logical_date(
        logical_date=logical_date,
        external_dag_id="france_travail_raw_ingestion",
        **kwargs,
    )


def _latest_hellowork_success(logical_date, **kwargs):
    return _latest_success_logical_date(
        logical_date=logical_date,
        external_dag_id="hellowork_raw_ingestion",
        **kwargs,
    )


with DAG(
    dag_id="job_market_etl_pipeline",
    description="Pipeline ETL: attente ingestion FT+HW puis nettoyage raw + chargement Postgres",
    start_date=datetime(2026, 3, 23),
    schedule="10 */3 * * *",
    catchup=False,
    default_args=default_args,
    max_active_runs=1,
    tags=["job-market", "etl", "postgres"],
) as dag:
    start = EmptyOperator(task_id="start")

    wait_france_travail_ingestion = ExternalTaskSensor(
        task_id="wait_france_travail_ingestion",
        external_dag_id="france_travail_raw_ingestion",
        external_task_id="end",
        execution_date_fn=_latest_france_travail_success,
        allowed_states=["success"],
        failed_states=["failed", "upstream_failed"],
        mode="reschedule",
        poke_interval=60,
        timeout=60 * 60,
    )

    wait_hellowork_ingestion = ExternalTaskSensor(
        task_id="wait_hellowork_ingestion",
        external_dag_id="hellowork_raw_ingestion",
        external_task_id="end",
        execution_date_fn=_latest_hellowork_success,
        allowed_states=["success"],
        failed_states=["failed", "upstream_failed"],
        mode="reschedule",
        poke_interval=60,
        timeout=60 * 60,
    )

    normalize_raw = BashOperator(
        task_id="normalize_raw",
        bash_command=(
            "cd /opt/airflow/src && "
            "python transform_offers.py "
            "--france-travail-input data/raw/france_travail_offers.json "
            "--hellowork-input data/raw/hellowork_offers.json "
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

    (
        start
        >> [
            wait_france_travail_ingestion,
            wait_hellowork_ingestion,
        ]
        >> normalize_raw
        >> load_to_postgres
        >> end
    )
