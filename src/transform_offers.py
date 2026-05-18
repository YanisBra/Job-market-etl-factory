from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class TransformConfig:
    """Configuration pour le script de transformation."""

    france_travail_input: Path  # Chemin vers les données brutes France Travail
    wttj_input: Path  # Chemin vers les données brutes Welcome to the Jungle
    output: Path  # Chemin du fichier de sortie unifié


class OfferTransformer:
    """
    Responsable de la transformation, du nettoyage et de l'unification des offres
    provenant de différentes sources (France Travail et Welcome to the Jungle).
    """

    def __init__(self, config: TransformConfig) -> None:
        """
        Initialise le transformateur avec la configuration spécifiée.

        Args:
            config: Objet de configuration contenant les chemins d'entrée et sortie.
        """
        self.config = config

    def run(self) -> Path:
        """Exécute le pipeline complet : lecture, normalisation, dédoublonnage et export."""
        # 1. Chargement des données brutes (JSON)
        ft_payload = self._read_json(self.config.france_travail_input)
        wttj_payload = self._read_json(self.config.wttj_input)

        # 2. Normalisation vers un schéma commun
        rows: list[dict[str, Any]] = []
        rows.extend(self._normalize_france_travail(ft_payload.get("offers", [])))
        rows.extend(self._normalize_wttj(wttj_payload.get("offers", [])))

        # 3. Suppression des doublons basés sur une clé métier
        deduped = self._deduplicate(rows)

        # 4. Préparation du fichier de sortie
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
        """Lit un fichier JSON et gère les fichiers inexistants."""
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _clean_text(self, value: Any) -> str | None:
        """Nettoie le texte : supprime les espaces inutiles et gère les valeurs nulles."""
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _alternance_flag(self, offer: dict[str, Any]) -> bool:
        """Extrait le flag alternance de manière sécurisée."""
        value = offer.get("alternance")
        return value if isinstance(value, bool) else False

    def _map_ft_contract_type(self, code: str | None) -> str | None:
        """
        Traduit les codes techniques de France Travail (MIS, SAI, etc.)
        en libellés lisibles pour l'analyse métier.
        """
        if not code:
            return None
        mapping = {
            "CDI": "CDI",
            "CDD": "CDD",
            "MIS": "Intérim",
            "SAI": "Saisonnier",
            "LIB": "Indépendant",
            "FRA": "Franchise",
            "CCE": "CDD Court",
            "DIN": "CDI (Inter)",
            "DDI": "CDD (Inter)",
        }
        return mapping.get(code.upper(), code)

    def _normalize_france_travail(self, offers: Any) -> list[dict[str, Any]]:
        """Transforme le format spécifique France Travail vers le schéma unifié."""
        if not isinstance(offers, list):
            return []

        rows: list[dict[str, Any]] = []
        for offer in offers:
            if not isinstance(offer, dict):
                continue

            # Gestion spécifique du salaire FT (qui peut être un dictionnaire ou une string)
            salary_raw = offer.get("salaire")
            salary_label = (
                self._clean_text(salary_raw.get("libelle"))
                if isinstance(salary_raw, dict)
                else self._clean_text(salary_raw)
            )

            # Normalisation du contrat et détection de l'alternance
            contract_type_raw = self._clean_text(offer.get("typeContrat"))
            contract_type = self._map_ft_contract_type(contract_type_raw)
            alternance = self._alternance_flag(offer)

            # Extraction des informations d'entreprise et de localisation
            company_info = offer.get("entreprise") or {}
            location_info = offer.get("lieuTravail") or {}
            origin_info = offer.get("origineOffre") or {}

            # Construction de la ligne unifiée selon le schéma cible
            rows.append(
                {
                    "source": "france_travail",
                    "source_offer_id": self._clean_text(offer.get("id")),
                    "external_url": self._clean_text(origin_info.get("urlOrigine")),
                    "title": self._clean_text(offer.get("intitule")),
                    "company_name": self._clean_text(company_info.get("nom")),
                    "city": self._clean_text(location_info.get("commune")),
                    "postal_code": self._clean_text(location_info.get("codePostal")),
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
                    "raw_offer": offer,  # On garde le brut pour d'éventuels futurs besoins
                }
            )
        return rows

    def _normalize_wttj(self, offers: Any) -> list[dict[str, Any]]:
        """Transforme le format spécifique Welcome to the Jungle vers le schéma unifié."""
        if not isinstance(offers, list):
            return []

        rows: list[dict[str, Any]] = []
        for offer in offers:
            if not isinstance(offer, dict):
                continue

            # Extraction sécurisée des données de salaire
            salary_raw = (
                offer.get("salaire") if isinstance(offer.get("salaire"), dict) else {}
            )

            # Normalisation des types de contrats
            contract_type = self._clean_text(offer.get("typeContrat"))
            alternance = self._alternance_flag(offer)

            # Extraction des informations d'entreprise et de localisation
            company_info = offer.get("entreprise") or {}
            location_info = offer.get("lieuTravail") or {}
            origin_info = offer.get("origineOffre") or {}

            # Construction de la ligne unifiée (alignée sur FT pour la consistance)
            rows.append(
                {
                    "source": "welcome_to_the_jungle",
                    "source_offer_id": self._clean_text(offer.get("id")),
                    "external_url": self._clean_text(origin_info.get("urlOrigine")),
                    "title": self._clean_text(offer.get("intitule")),
                    "company_name": self._clean_text(company_info.get("nom")),
                    "city": self._clean_text(location_info.get("commune")),
                    "postal_code": self._clean_text(location_info.get("codePostal")),
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
        """
        Élimine les doublons en utilisant une clé unique composite.
        Si plusieurs offres ont la même clé, la dernière trouvée l'emporte.

        La clé d'unicité est composée de la source, l'ID source et l'URL externe.
        """
        dedup: dict[str, dict[str, Any]] = {}
        for row in rows:
            # Construction d'une clé d'unicité robuste
            key = (
                f"{row.get('source','')}|"
                f"{row.get('source_offer_id','')}|"
                f"{row.get('external_url','')}"
            )
            dedup[key] = row
        return list(dedup.values())


def build_arg_parser() -> argparse.ArgumentParser:
    """
    Configure le parseur d'arguments pour le script de transformation.

    Définit les chemins par défaut pour les entrées France Travail et WTTJ,
    ainsi que pour le fichier de sortie unifié.

    Returns:
        argparse.ArgumentParser: Le parseur configuré.
    """
    parser = argparse.ArgumentParser(
        description="Transformation ETL: nettoyage + fusion FT/WTTJ."
    )
    parser.add_argument(
        "--france-travail-input",
        default="data/raw/france_travail_offers.json",
        help="Chemin vers le JSON brut de France Travail",
    )
    parser.add_argument(
        "--wttj-input",
        default="data/raw/wttj_offers.json",
        help="Chemin vers le JSON brut de Welcome to the Jungle",
    )
    parser.add_argument(
        "--output",
        default="data/processed/unified_offers.json",
        help="Chemin vers le fichier JSON unifié en sortie",
    )
    return parser


def main() -> int:
    """
    Point d'entrée principal du script de transformation.

    Initialise la configuration, exécute le transformateur et gère les erreurs.

    Returns:
        int: 0 si succès, 1 si erreur.
    """
    args = build_arg_parser().parse_args()
    try:
        # Initialisation de la configuration avec les arguments CLI
        cfg = TransformConfig(
            france_travail_input=Path(args.france_travail_input),
            wttj_input=Path(args.wttj_input),
            output=Path(args.output),
        )

        # Lancement de la transformation
        output_path = OfferTransformer(cfg).run()
        print(f"Transformation terminee: {output_path}")
        return 0
    except Exception as exc:
        # Capture et affichage des erreurs fatales
        print(f"Erreur transformation ETL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
