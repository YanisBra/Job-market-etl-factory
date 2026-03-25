from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import psycopg2
from psycopg2.extras import Json, execute_batch


def load_dotenv_file(path: str = ".env") -> None:
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


def upsert_sql() -> str:
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


def build_rows(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
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

        conn = psycopg2.connect(**pg_connection_params())
        try:
            with conn, conn.cursor() as cur:
                cur.execute(create_table_sql())
                execute_batch(cur, upsert_sql(), rows, page_size=200)
        finally:
            conn.close()

        print(f"Chargement Postgres termine: {len(rows)} lignes upsert.")
        return 0
    except Exception as exc:
        print(f"Erreur chargement Postgres: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
