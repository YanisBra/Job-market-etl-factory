from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import psycopg2
from minio import Minio
from psycopg2.extras import Json, execute_batch


def load_dotenv_file(path: str = ".env") -> None:
    """Charge les variables d'environnement depuis un fichier .env."""
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def pg_connection_params() -> dict[str, Any]:
    """Récupère les paramètres de connexion PostgreSQL depuis l'environnement."""
    return {
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5433")),
        "dbname": os.environ.get(
            "PGDATABASE", os.environ.get("POSTGRES_DB", "airflow")
        ),
        "user": os.environ.get("PGUSER", os.environ.get("POSTGRES_USER", "airflow")),
        "password": os.environ.get(
            "PGPASSWORD", os.environ.get("POSTGRES_PASSWORD", "airflow")
        ),
    }


def create_table_sql() -> str:
    """Retourne le SQL pour créer la table des offres unifiées."""
    return """
    CREATE TABLE IF NOT EXISTS offers_unified (
        id BIGSERIAL PRIMARY KEY,
        source TEXT NOT NULL,
        source_offer_id TEXT NOT NULL,
        external_url TEXT,
        title TEXT,
        company_name TEXT,
        city TEXT,
        postal_code TEXT,
        contract_type_raw TEXT,
        contract_type TEXT,
        alternance BOOLEAN NOT NULL DEFAULT FALSE,
        description TEXT,
        salary_label TEXT,
        salary_min DOUBLE PRECISION,
        salary_max DOUBLE PRECISION,
        salary_currency TEXT,
        experience_required TEXT,
        education_level TEXT,
        created_at TEXT,
        raw_offer JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (source, source_offer_id, external_url)
    );
    """


def create_metrics_table_sql() -> str:
    """Retourne le SQL pour créer la table de suivi des métriques ETL."""
    return """
    CREATE TABLE IF NOT EXISTS etl_metrics (
        id BIGSERIAL PRIMARY KEY,
        run_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        raw_api_count INTEGER NOT NULL,
        raw_scrap_count INTEGER NOT NULL,
        postgres_count INTEGER NOT NULL
    );
    """


def upsert_sql() -> str:
    """
    Retourne la requête UPSERT (Insert or Update) pour PostgreSQL.
    Gère les doublons basés sur la clé unique (source, source_offer_id, external_url).
    """
    return """
    INSERT INTO offers_unified (
        source,
        source_offer_id,
        external_url,
        title,
        company_name,
        city,
        postal_code,
        contract_type_raw,
        contract_type,
        alternance,
        description,
        salary_label,
        salary_min,
        salary_max,
        salary_currency,
        experience_required,
        education_level,
        created_at,
        raw_offer
    ) VALUES (
        %(source)s,
        %(source_offer_id)s,
        %(external_url)s,
        %(title)s,
        %(company_name)s,
        %(city)s,
        %(postal_code)s,
        %(contract_type_raw)s,
        %(contract_type)s,
        %(alternance)s,
        %(description)s,
        %(salary_label)s,
        %(salary_min)s,
        %(salary_max)s,
        %(salary_currency)s,
        %(experience_required)s,
        %(education_level)s,
        %(created_at)s,
        %(raw_offer)s
    )
    ON CONFLICT (source, source_offer_id, external_url)
    DO UPDATE SET
        title = EXCLUDED.title,
        company_name = EXCLUDED.company_name,
        city = EXCLUDED.city,
        postal_code = EXCLUDED.postal_code,
        contract_type_raw = EXCLUDED.contract_type_raw,
        contract_type = EXCLUDED.contract_type,
        alternance = EXCLUDED.alternance,
        description = EXCLUDED.description,
        salary_label = EXCLUDED.salary_label,
        salary_min = EXCLUDED.salary_min,
        salary_max = EXCLUDED.salary_max,
        salary_currency = EXCLUDED.salary_currency,
        experience_required = EXCLUDED.experience_required,
        education_level = EXCLUDED.education_level,
        created_at = EXCLUDED.created_at,
        raw_offer = EXCLUDED.raw_offer,
        updated_at = NOW();
    """


def insert_metrics_sql() -> str:
    """Requête SQL pour insérer un nouveau point de métriques."""
    return """
    INSERT INTO etl_metrics (raw_api_count, raw_scrap_count, postgres_count)
    VALUES (%(raw_api_count)s, %(raw_scrap_count)s, %(postgres_count)s);
    """


def minio_client() -> Minio:
    """Initialise et retourne un client Minio pour le suivi des métriques."""
    endpoint = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
    access_key = os.environ.get("MINIO_ROOT_USER", "admin")
    secret_key = os.environ.get("MINIO_ROOT_PASSWORD", "change_me")
    secure = os.environ.get("MINIO_SECURE", "false").lower() == "true"
    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
    )


def count_bucket_objects(client: Minio, bucket_name: str) -> int:
    """Compte récursivement le nombre d'objets dans un bucket MinIO."""
    try:
        return sum(1 for _ in client.list_objects(bucket_name, recursive=True))
    except Exception:
        return 0


def count_raw_minio_objects() -> tuple[int, int]:
    """Récupère le nombre d'objets bruts stockés dans MinIO (API vs Scraping)."""
    bucket_api = os.environ.get("MINIO_BUCKET_RAW_API", "raw-api")
    bucket_scrap = os.environ.get("MINIO_BUCKET_RAW_SCRAP", "raw-scrap")
    try:
        client = minio_client()
        raw_api_count = count_bucket_objects(client, bucket_api)
        raw_scrap_count = count_bucket_objects(client, bucket_scrap)
        return raw_api_count, raw_scrap_count
    except Exception:
        return 0, 0


def count_postgres_rows(cur: Any) -> int:
    """Compte le nombre total d'offres actuellement en base de données."""
    cur.execute("SELECT COUNT(*) FROM offers_unified;")
    row = cur.fetchone()
    return int(row[0]) if row else 0


def build_rows(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prépare les données JSON pour l'insertion SQL (mapping champs + conversion JSONB)."""
    rows: list[dict[str, Any]] = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        row = {
            "source": offer.get("source") or "",
            "source_offer_id": offer.get("source_offer_id") or "",
            "external_url": offer.get("external_url"),
            "title": offer.get("title"),
            "company_name": offer.get("company_name"),
            "city": offer.get("city"),
            "postal_code": offer.get("postal_code"),
            "contract_type_raw": offer.get("contract_type_raw"),
            "contract_type": offer.get("contract_type"),
            "alternance": bool(offer.get("alternance", False)),
            "description": offer.get("description"),
            "salary_label": offer.get("salary_label"),
            "salary_min": offer.get("salary_min"),
            "salary_max": offer.get("salary_max"),
            "salary_currency": offer.get("salary_currency"),
            "experience_required": offer.get("experience_required"),
            "education_level": offer.get("education_level"),
            "created_at": offer.get("created_at"),
            "raw_offer": Json(offer.get("raw_offer", {})),
        }
        rows.append(row)
    return rows


def main() -> int:
    """
    Point d'entrée principal pour le chargement vers Postgres.
    
    Étapes :
    1. Lecture du fichier unifié JSON.
    2. Récupération des métriques MinIO.
    3. Connexion à Postgres.
    4. Création des tables si nécessaire.
    5. Insertion en masse via UPSERT.
    6. Enregistrement des métriques ETL.
    """
    parser = argparse.ArgumentParser(
        description="Charge les offres transformees vers Postgres (UPSERT)."
    )
    parser.add_argument(
        "--input",
        default="data/processed/unified_offers.json",
        help="Fichier JSON transforme a charger.",
    )
    args = parser.parse_args()

    try:
        load_dotenv_file()
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        offers = payload.get("offers", [])
        rows = build_rows(offers)
        
        # Collecte des métriques avant insertion
        raw_api_count, raw_scrap_count = count_raw_minio_objects()

        conn = psycopg2.connect(**pg_connection_params())
        try:
            with conn, conn.cursor() as cur:
                # Préparation du schéma
                cur.execute(create_table_sql())
                cur.execute(create_metrics_table_sql())
                
                # Insertion en batch pour la performance
                execute_batch(cur, upsert_sql(), rows, page_size=200)
                
                # Mise à jour des métriques finalisées
                postgres_count = count_postgres_rows(cur)
                cur.execute(
                    insert_metrics_sql(),
                    {
                        "raw_api_count": raw_api_count,
                        "raw_scrap_count": raw_scrap_count,
                        "postgres_count": postgres_count,
                    },
                )
        finally:
            conn.close()

        print(
            "Chargement Postgres termine: "
            f"{len(rows)} lignes upsert | "
            f"raw_api_count={raw_api_count} raw_scrap_count={raw_scrap_count} "
            f"postgres_count={postgres_count}"
        )
        return 0
    except Exception as exc:
        print(f"Erreur chargement Postgres: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
