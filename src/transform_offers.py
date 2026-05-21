from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass  # Permet d'éviter le init manuel et d'avoir une classe de configuration simple
class TransformConfig:
    """Configuration pour le script de transformation."""

    france_travail_input: Path
    hellowork_input: Path
    output: Path


class OfferTransformer:
    """
    Responsable de la transformation, du nettoyage et de l'unification des offres
    provenant de différentes sources (France Travail et HelloWork).
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
        hw_payload = self._read_json(self.config.hellowork_input)

        # 2. Normalisation vers un schéma commun
        rows: list[dict[str, Any]] = (
            []
        )  # rows est une liste de dictionnaires, dont les clés sont des strings et les valeurs peuvent être n'importe quoi
        rows.extend(self._normalize_france_travail(ft_payload.get("offers", [])))
        rows.extend(self._normalize_hellowork(hw_payload.get("offers", [])))

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

    def _clean_job_title(self, title: str | None) -> str | None:
        """Nettoie l'intitulé du poste : minuscule, retrait des mentions H/F, F/H, etc."""
        if not title:
            return None
        import re

        t = title.lower()  # Supprime (h/f), h/f, (f/h), f/h, (m/f), m/f
        t = re.sub(
            r"\s*[\(\[]?\s*[hfm]\s*/\s*[hfm]\s*[\)\]]?\s*", " ", t
        )  # Supprime les tirets de fin et espaces multiples
        t = re.sub(r"\s*-\s*$", "", t)
        t = " ".join(t.split())
        return t or None

    def _annualize_salary(
        self,
        label: str | None,
        min_val: float | None,
        max_val: float | None,
        unit: str | None = "YEAR",
    ) -> float | None:
        """Estime un salaire annuel à partir des données brutes (heure, mois, an)."""
        # Priorité aux valeurs numériques si présentes
        val = None
        if min_val is not None and max_val is not None:
            val = (min_val + max_val) / 2
        elif min_val is not None:
            val = min_val
        elif max_val is not None:
            val = max_val

        if val is not None:
            u = (unit or "YEAR").upper()
            if "HOUR" in u or "HEURE" in u:
                return val * 35 * 52  # Base 35h
            if "MONTH" in u or "MOIS" in u:
                return val * 12
            if val < 100:  # Probablement horaire non détecté
                return val * 35 * 52
            if val < 5000:  # Probablement mensuel non détecté
                return val * 12
            return val

        # Sinon on essaie de parser le label
        if not label:
            return None
        import re

        nums = re.findall(r"(\d+(?:\.\d+)?)", label.replace(",", ".").replace(" ", ""))
        if not nums:
            return None

        val = float(nums[0])  # Heuristique simple sur la valeur
        if val < 100:
            return val * 35 * 52
        if val > 1000 and val < 6000:
            return val * 12
        if val > 15000:
            return val
        return None

    def _categorize_experience(self, exp_text: str | None, source: str) -> str:
        """Catégorise le niveau d'expérience (Junior, Intermédiaire, Sénior)."""
        if not exp_text:
            return "Non spécifié"

        # Tentative d'interprétation comme un nombre de mois (format HelloWork)
        try:
            months = int(float(str(exp_text)))
            years = months / 12
            if years < 2:
                return "Junior (0-2 ans)"
            if years >= 5:
                return "Sénior (5+ ans)"
            return "Intermédiaire (2-5 ans)"
        except (ValueError, TypeError):
            pass

        t = exp_text.lower()

        # Détection par mots-clés
        if any(
            w in t
            for w in [
                "senior",
                "sénior",
                "expert",
                "confirmé",
                "10 ans",
                "8 ans",
                "7 ans",
                "6 ans",
                "5 ans",
            ]
        ):
            return "Sénior (5+ ans)"
        if any(w in t for w in ["junior", "débutant", "jeune diplômé"]):
            return "Junior (0-2 ans)"

        # Détection par chiffres dans le texte (ex: "3 ans d'expérience")
        import re

        years_match = re.findall(r"(\d+)\s*an", t)
        if years_match:
            y = int(years_match[0])
            if y < 2:
                return "Junior (0-2 ans)"
            if y >= 5:
                return "Sénior (5+ ans)"
            return "Intermédiaire (2-5 ans)"

        return "Intermédiaire (2-5 ans)"

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

            # Priorité au libellé lisible pour la ville (France Travail met souvent le code INSEE dans 'commune')
            raw_city = self._clean_text(
                location_info.get("libelle")
            ) or self._clean_text(location_info.get("commune"))
            city = raw_city
            if raw_city and " - " in raw_city:
                # Nettoyage du format "75 - PARIS" -> "PARIS"
                parts = raw_city.split(" - ", 1)
                if parts[0].isdigit() and len(parts[0]) <= 3:
                    city = parts[1]

            title = self._clean_text(offer.get("intitule"))
            exp_text = self._clean_text(offer.get("experienceExige"))

            # Construction de la ligne unifiée selon le schéma cible
            rows.append(
                {
                    "source": "france_travail",
                    "source_offer_id": self._clean_text(offer.get("id")),
                    "external_url": self._clean_text(origin_info.get("urlOrigine")),
                    "title": title,
                    "title_clean": self._clean_job_title(title),
                    "company_name": self._clean_text(company_info.get("nom")),
                    "city": city,
                    "postal_code": self._clean_text(location_info.get("codePostal")),
                    "contract_type_raw": contract_type,
                    "contract_type": contract_type,
                    "alternance": alternance,
                    "description": self._clean_text(offer.get("description")),
                    "salary_label": salary_label,
                    "salary_annual": self._annualize_salary(salary_label, None, None),
                    "salary_min": None,
                    "salary_max": None,
                    "salary_currency": None,
                    "experience_required": exp_text,
                    "experience_level": self._categorize_experience(exp_text, "FT"),
                    "education_level": self._clean_text(
                        offer.get("niveauFormationLibelle")
                    ),
                    "created_at": self._clean_text(offer.get("dateCreation")),
                    "raw_offer": offer,  # On garde le brut pour d'éventuels futurs besoins
                }
            )
        return rows

    def _normalize_hellowork(self, offers: Any) -> list[dict[str, Any]]:
        """Transforme le format spécifique HelloWork vers le schéma unifié."""
        if not isinstance(offers, list):
            return []

        rows: list[dict[str, Any]] = []
        for offer in offers:
            if not isinstance(offer, dict):
                continue

            salary_raw = offer.get("salaire") or {}
            contract_type = self._clean_text(offer.get("typeContrat"))
            alternance = self._alternance_flag(offer)
            company_info = offer.get("entreprise") or {}
            location_info = offer.get("lieuTravail") or {}
            origin_info = offer.get("origineOffre") or {}

            title = self._clean_text(offer.get("intitule"))
            exp_text = self._clean_text(offer.get("experienceRequise"))
            sal_label = self._clean_text(salary_raw.get("libelle"))
            sal_min = (
                salary_raw.get("min")
                if isinstance(salary_raw.get("min"), (int, float))
                else None
            )
            sal_max = (
                salary_raw.get("max")
                if isinstance(salary_raw.get("max"), (int, float))
                else None
            )

            rows.append(
                {
                    "source": "hellowork",
                    "source_offer_id": self._clean_text(offer.get("id")),
                    "external_url": self._clean_text(origin_info.get("urlOrigine")),
                    "title": title,
                    "title_clean": self._clean_job_title(title),
                    "company_name": self._clean_text(company_info.get("nom")),
                    "city": self._clean_text(location_info.get("commune")),
                    "postal_code": self._clean_text(location_info.get("codePostal")),
                    "contract_type_raw": contract_type,
                    "contract_type": contract_type,
                    "alternance": alternance,
                    "description": self._clean_text(offer.get("description")),
                    "salary_label": sal_label,
                    "salary_annual": self._annualize_salary(
                        sal_label, sal_min, sal_max, salary_raw.get("unite")
                    ),
                    "salary_min": sal_min,
                    "salary_max": sal_max,
                    "salary_currency": self._clean_text(salary_raw.get("devise")),
                    "experience_required": exp_text,
                    "experience_level": self._categorize_experience(exp_text, "HW"),
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

    Définit les chemins par défaut pour les entrées France Travail et HelloWork,
    ainsi que pour le fichier de sortie unifié.

    Returns:
        argparse.ArgumentParser: Le parseur configuré.
    """
    parser = argparse.ArgumentParser(
        description="Transformation ETL: nettoyage + fusion FT/HW."
    )
    parser.add_argument(
        "--france-travail-input",
        default="data/raw/france_travail_offers.json",
        help="Chemin vers le JSON brut de France Travail",
    )
    parser.add_argument(
        "--hellowork-input",
        default="data/raw/hellowork_offers.json",
        help="Chemin vers le JSON brut de HelloWork",
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
            hellowork_input=Path(args.hellowork_input),
            output=Path(args.output),
        )

        # Création de l'objet et lancement de la transformation
        output_path = OfferTransformer(cfg).run()
        print(f"Transformation terminee: {output_path}")
        return 0
    except Exception as exc:
        # Capture et affichage des erreurs fatales
        print(f"Erreur transformation ETL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
