from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class TransformConfig:
    france_travail_input: Path
    wttj_input: Path
    output: Path


class OfferTransformer:
    def __init__(self, config: TransformConfig) -> None:
        self.config = config

    def run(self) -> Path:
        ft_payload = self._read_json(self.config.france_travail_input)
        wttj_payload = self._read_json(self.config.wttj_input)

        rows: list[dict[str, Any]] = []
        rows.extend(self._normalize_france_travail(ft_payload.get("offers", [])))
        rows.extend(self._normalize_wttj(wttj_payload.get("offers", [])))

        deduped = self._deduplicate(rows)
        output_payload = {
            "source": "job_market_unified",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "total": len(deduped),
            "offers": deduped,
        }

        self.config.output.parent.mkdir(parents=True, exist_ok=True)
        self.config.output.write_text(
            json.dumps(output_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self.config.output

    def _read_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _clean_text(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _alternance_flag(self, offer: dict[str, Any]) -> bool:
        value = offer.get("alternance")
        return value if isinstance(value, bool) else False

    def _normalize_france_travail(self, offers: Any) -> list[dict[str, Any]]:
        if not isinstance(offers, list):
            return []

        rows: list[dict[str, Any]] = []
        for offer in offers:
            if not isinstance(offer, dict):
                continue

            salary_raw = offer.get("salaire")
            salary_label = (
                self._clean_text(salary_raw.get("libelle"))
                if isinstance(salary_raw, dict)
                else self._clean_text(salary_raw)
            )

            contract_type = self._clean_text(offer.get("typeContrat"))
            alternance = self._alternance_flag(offer)

            rows.append(
                {
                    "source": "france_travail",
                    "source_offer_id": self._clean_text(offer.get("id")),
                    "external_url": self._clean_text(
                        (offer.get("origineOffre") or {}).get("urlOrigine")
                    ),
                    "title": self._clean_text(offer.get("intitule")),
                    "company_name": self._clean_text(
                        (offer.get("entreprise") or {}).get("nom")
                    ),
                    "city": self._clean_text(
                        (offer.get("lieuTravail") or {}).get("commune")
                    ),
                    "postal_code": self._clean_text(
                        (offer.get("lieuTravail") or {}).get("codePostal")
                    ),
                    "contract_type_raw": contract_type,
                    "contract_type": contract_type,
                    "alternance": alternance,
                    "description": self._clean_text(offer.get("description")),
                    "salary_label": salary_label,
                    "salary_min": None,
                    "salary_max": None,
                    "salary_currency": None,
                    "experience_required": self._clean_text(
                        offer.get("experienceExige")
                    ),
                    "education_level": self._clean_text(
                        offer.get("niveauFormationLibelle")
                    ),
                    "created_at": self._clean_text(offer.get("dateCreation")),
                    "raw_offer": offer,
                }
            )
        return rows

    def _normalize_wttj(self, offers: Any) -> list[dict[str, Any]]:
        if not isinstance(offers, list):
            return []

        rows: list[dict[str, Any]] = []
        for offer in offers:
            if not isinstance(offer, dict):
                continue

            salary_raw = (
                offer.get("salaire") if isinstance(offer.get("salaire"), dict) else {}
            )
            contract_type = self._clean_text(offer.get("typeContrat"))
            alternance = self._alternance_flag(offer)

            rows.append(
                {
                    "source": "welcome_to_the_jungle",
                    "source_offer_id": self._clean_text(offer.get("id")),
                    "external_url": self._clean_text(
                        (offer.get("origineOffre") or {}).get("urlOrigine")
                    ),
                    "title": self._clean_text(offer.get("intitule")),
                    "company_name": self._clean_text(
                        (offer.get("entreprise") or {}).get("nom")
                    ),
                    "city": self._clean_text(
                        (offer.get("lieuTravail") or {}).get("commune")
                    ),
                    "postal_code": self._clean_text(
                        (offer.get("lieuTravail") or {}).get("codePostal")
                    ),
                    "contract_type_raw": contract_type,
                    "contract_type": contract_type,
                    "alternance": alternance,
                    "description": self._clean_text(offer.get("description")),
                    "salary_label": self._clean_text(salary_raw.get("libelle")),
                    "salary_min": (
                        salary_raw.get("min")
                        if isinstance(salary_raw.get("min"), (int, float))
                        else None
                    ),
                    "salary_max": (
                        salary_raw.get("max")
                        if isinstance(salary_raw.get("max"), (int, float))
                        else None
                    ),
                    "salary_currency": self._clean_text(salary_raw.get("devise")),
                    "experience_required": self._clean_text(
                        offer.get("experienceRequise")
                    ),
                    "education_level": self._clean_text(offer.get("niveauEtude")),
                    "created_at": self._clean_text(offer.get("dateCreation")),
                    "raw_offer": offer,
                }
            )
        return rows

    def _deduplicate(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        dedup: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = (
                f"{row.get('source','')}|"
                f"{row.get('source_offer_id','')}|"
                f"{row.get('external_url','')}"
            )
            dedup[key] = row
        return list(dedup.values())


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transformation ETL: nettoyage + fusion FT/WTTJ."
    )
    parser.add_argument(
        "--france-travail-input",
        default="data/raw/france_travail_offers.json",
    )
    parser.add_argument(
        "--wttj-input",
        default="data/raw/wttj_offers.json",
    )
    parser.add_argument(
        "--output",
        default="data/processed/unified_offers.json",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    try:
        cfg = TransformConfig(
            france_travail_input=Path(args.france_travail_input),
            wttj_input=Path(args.wttj_input),
            output=Path(args.output),
        )
        output_path = OfferTransformer(cfg).run()
        print(f"Transformation terminee: {output_path}")
        return 0
    except Exception as exc:
        print(f"Erreur transformation ETL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
