#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[1/6] Checking local DAG files..."
if [ ! -d "airflow/dags" ]; then
  echo "ERROR: airflow/dags directory not found in project root: $ROOT_DIR"
  exit 1
fi
find airflow/dags -maxdepth 1 -name "*_dag.py" -print

echo "[2/6] Checking docker compose services..."
docker compose ps >/dev/null

echo "[3/6] Recreating Airflow services to refresh bind mounts..."
docker compose up -d --force-recreate airflow-webserver airflow-scheduler

echo "[4/6] Verifying DAG folder inside scheduler container..."
docker compose exec -T airflow-scheduler ls -la /opt/airflow/dags

echo "[5/6] Syncing Airflow permissions (RBAC)..."
docker compose exec -T airflow-webserver airflow sync-perm >/dev/null || true

echo "[6/6] Listing DAGs from scheduler..."
docker compose exec -T airflow-scheduler airflow dags list

echo ""
echo "Airflow recovery completed."
echo "If UI still shows no DAGs, hard refresh browser: Cmd+Shift+R."
