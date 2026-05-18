from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import time
from html import unescape
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib import error, parse, request


def load_dotenv_file(path: str = ".env") -> None:
    """Charge manuellement les variables d'environnement depuis un fichier .env."""
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


@dataclass
class MinioConfig:
    """Configuration pour la connexion à MinIO."""

    endpoint: str
    access_key: str
    secret_key: str
    bucket: str
    secure: bool = False

    @classmethod
    def from_env(cls) -> "MinioConfig":
        """Crée une configuration MinIO à partir des variables d'environnement."""
        env = os.environ
        access_key = env.get("MINIO_ROOT_USER", "").strip()
        secret_key = env.get("MINIO_ROOT_PASSWORD", "").strip()
        if not access_key or not secret_key:
            raise ValueError(
                "Variables MinIO manquantes: MINIO_ROOT_USER et/ou MINIO_ROOT_PASSWORD"
            )

        endpoint = env.get("MINIO_ENDPOINT", "").strip()
        if not endpoint:
            minio_port = env.get("MINIO_API_PORT", "9000").strip()
            endpoint = f"localhost:{minio_port}"

        secure_raw = env.get("MINIO_SECURE", "false").strip().lower()
        secure = secure_raw in {"1", "true", "yes", "on"}

        return cls(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            bucket=env.get("MINIO_BUCKET_RAW_SCRAP", "raw-scrap").strip()
            or "raw-scrap",
            secure=secure,
        )


class MinioRawStorage:
    """Gère le stockage des données brutes (JSON) dans MinIO."""

    def __init__(self, config: MinioConfig, minio_client: Any | None = None) -> None:
        self.config = config
        if minio_client is not None:
            self.client = minio_client
            return

        try:
            from minio import Minio  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "Le package 'minio' est requis. Installe-le avec: pip install minio"
            ) from exc

        self.client = Minio(
            endpoint=self.config.endpoint,
            access_key=self.config.access_key,
            secret_key=self.config.secret_key,
            secure=self.config.secure,
        )

    def _ensure_bucket(self) -> None:
        """Vérifie l'existence du bucket et le crée si nécessaire."""
        try:
            if not self.client.bucket_exists(self.config.bucket):
                self.client.make_bucket(self.config.bucket)
        except Exception as exc:
            raise RuntimeError(
                f"Echec creation/verification bucket MinIO: {exc}"
            ) from exc

    def upload_raw_json(
        self, payload: dict[str, Any], source: str = "welcome_to_the_jungle"
    ) -> str:
        """Upload un dictionnaire JSON vers MinIO avec un partitionnement par date."""
        now = datetime.now(timezone.utc)
        object_key = f"{source}/{now:%Y/%m/%d}/offers_{now:%Y%m%dT%H%M%SZ}.json"
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

        self._ensure_bucket()
        try:
            self.client.put_object(
                bucket_name=self.config.bucket,
                object_name=object_key,
                data=io.BytesIO(body),
                length=len(body),
                content_type="application/json",
            )
        except Exception as exc:
            raise RuntimeError(f"Echec upload MinIO: {exc}") from exc

        return object_key


@dataclass
class WTTJScraperConfig:
    """Configuration spécifique au scraper Welcome to the Jungle."""

    base_url: str
    timeout_seconds: int
    user_agent: str

    @classmethod
    def from_env(cls) -> "WTTJScraperConfig":
        env = os.environ
        return cls(
            base_url=env.get("WTTJ_BASE_URL", "https://www.welcometothejungle.com"),
            timeout_seconds=int(env.get("WTTJ_TIMEOUT_SECONDS", "20")),
            user_agent=env.get(
                "WTTJ_USER_AGENT",
                "Mozilla/5.0 (compatible; job-market-etl-factory/1.0)",
            ),
        )


class JsonLdParser(HTMLParser):
    """Parseur HTML spécialisé pour extraire les blocs <script type='application/ld+json'>."""

    def __init__(self) -> None:
        super().__init__()
        self._inside_target_script = False
        self.json_blobs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        attrs_map = {key.lower(): value for key, value in attrs}
        script_type = (attrs_map.get("type") or "").lower()
        if script_type == "application/ld+json":
            self._inside_target_script = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script":
            self._inside_target_script = False

    def handle_data(self, data: str) -> None:
        if self._inside_target_script and data.strip():
            self.json_blobs.append(data.strip())


class HtmlTextExtractor(HTMLParser):
    """Parseur utilitaire pour extraire le texte brut d'une chaîne HTML (nettoyage de balises)."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self._chunks.append(data)

    def text(self) -> str:
        raw = unescape(" ".join(self._chunks))
        # Compacte les espaces multiples et retours à la ligne.
        return " ".join(raw.split())


class WelcomeToTheJungleScraper:
    """Cœur du scraper : gère les requêtes HTTP et l'extraction des données JSON-LD."""

    def __init__(self, config: WTTJScraperConfig) -> None:
        self.config = config

    def fetch_search_page(self, query: str, page: int = 1) -> str:
        """Récupère le code HTML de la page de recherche pour une requête et une page données."""
        params = parse.urlencode({"query": query, "page": page})
        url = f"{self.config.base_url}/fr/jobs?{params}"
        headers = {"User-Agent": self.config.user_agent}
        req = request.Request(url, headers=headers)

        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                return response.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Echec scraping WTTJ ({exc.code}): {details[:300]}"
            ) from exc
        except error.URLError as exc:
            raise RuntimeError(f"Echec connexion scraping WTTJ: {exc.reason}") from exc
        except Exception as exc:
            raise RuntimeError(f"Echec technique scraping WTTJ: {exc}") from exc

    def fetch_job_page(self, job_url: str) -> str:
        """Récupère le code HTML de la page de détail d'une offre."""
        headers = {"User-Agent": self.config.user_agent}
        req = request.Request(job_url, headers=headers)

        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                return response.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Echec scraping detail WTTJ ({exc.code}) url={job_url}: {details[:200]}"
            ) from exc
        except error.URLError as exc:
            raise RuntimeError(
                f"Echec connexion detail WTTJ url={job_url}: {exc.reason}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"Echec technique detail WTTJ url={job_url}: {exc}"
            ) from exc

    def _normalize_jobposting(self, job: dict[str, Any]) -> dict[str, Any]:
        """Convertit un objet JSON-LD 'JobPosting' brut en dictionnaire normalisé pour le pipeline."""
        hiring_org = job.get("hiringOrganization") or {}
        location = job.get("jobLocation") or {}
        if isinstance(location, list):
            location = location[0] if location else {}
        postal_address = location.get("address") or {}

        description_raw = str(job.get("description") or "")
        description_clean = self._strip_html(description_raw)
        title = job.get("title")
        url = job.get("url")
        company_name = hiring_org.get("name")
        city = postal_address.get("addressLocality")
        country = postal_address.get("addressCountry")
        postal_code = postal_address.get("postalCode")
        date_creation = job.get("datePosted")
        employment_type = job.get("employmentType")
        type_contrat = self._map_contract_type(employment_type)
        alternance = self._is_alternance(employment_type)
        salaire = self._extract_salary(job.get("baseSalary"))
        experience_requise = self._extract_requirement_text(
            job.get("experienceRequirements")
        )
        niveau_etude = self._extract_requirement_text(job.get("educationRequirements"))
        lieu_libelle = self._build_lieu_libelle(city=city, country=country)
        offer_id = self._build_offer_id(url=url, title=title)

        return {
            "id": offer_id,
            "intitule": title,
            "description": description_clean or None,
            "dateCreation": date_creation,
            "lieuTravail": {
                "libelle": lieu_libelle,
                "codePostal": postal_code,
                "commune": city,
            },
            "entreprise": {
                "nom": company_name,
            },
            "typeContrat": type_contrat,
            "typeContratLibelle": type_contrat,
            "alternance": alternance,
            "salaire": salaire,
            "experienceRequise": experience_requise,
            "niveauEtude": niveau_etude,
            "origineOffre": {
                "origine": "2",
                "urlOrigine": url,
            },
            "source": "welcome_to_the_jungle",
        }

    def _build_offer_id(self, url: Any, title: Any) -> str:
        """Génère un ID stable basé sur le hash de l'URL."""
        raw = str(url or title or "")
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8].upper()
        return f"WTTJ_{digest}"

    def _build_lieu_libelle(self, city: Any, country: Any) -> str | None:
        """Formate le libellé de localisation."""
        city_s = str(city).strip() if city else ""
        country_s = str(country).strip() if country else ""
        if city_s and country_s:
            return f"{city_s} ({country_s})"
        if city_s:
            return city_s
        if country_s:
            return country_s
        return None

    def _map_contract_type(self, employment_type: Any) -> str | None:
        """Mappe les types d'emploi WTTJ vers des libellés standards (CDI, CDD, etc.)."""
        if not employment_type:
            return None
        value = str(employment_type).strip().upper()
        mapping = {
            "FULL_TIME": "CDI",
            "PART_TIME": "Temps partiel",
            "TEMPORARY": "CDD",
            "CONTRACTOR": "Alternance",
            "INTERN": "Stage",
        }
        return mapping.get(value, value)

    def _is_alternance(self, employment_type: Any) -> bool:
        """Détecte si l'offre est une alternance."""
        if isinstance(employment_type, str):
            return employment_type.strip().upper() == "CONTRACTOR"
        if isinstance(employment_type, list):
            return any(
                isinstance(item, str) and item.strip().upper() == "CONTRACTOR"
                for item in employment_type
            )
        return False

    def _extract_requirement_text(self, value: Any) -> str | None:
        """Extrait de manière récursive le texte des pré-requis (expérience, éducation)."""
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = self._strip_html(value)
            return cleaned or None
        if isinstance(value, list):
            parts = []
            for item in value:
                text = self._extract_requirement_text(item)
                if text:
                    parts.append(text)
            return " | ".join(parts) if parts else None
        if isinstance(value, dict):
            for key in ("name", "description", "value", "text"):
                raw = value.get(key)
                if isinstance(raw, str) and raw.strip():
                    cleaned = self._strip_html(raw)
                    if cleaned:
                        return cleaned
            months = value.get("monthsOfExperience")
            if isinstance(months, (int, float)) and months > 0:
                years = int(months) // 12
                if years > 0:
                    return f"{int(months)} mois (~{years} ans)"
                return f"{int(months)} mois"
            credential = value.get("credentialCategory")
            if isinstance(credential, str) and credential.strip():
                cleaned = self._strip_html(credential)
                if cleaned:
                    return self._normalize_education_level(cleaned)
            return None
        return None

    def _normalize_education_level(self, raw: str) -> str:
        """Mappe les niveaux d'études anglais vers le standard français (Bac+X)."""
        mapping = {
            "high school": "Bac",
            "associate degree": "Bac+2",
            "bachelor degree": "Bac+3",
            "master degree": "Bac+5",
            "postgraduate degree": "Bac+5+",
            "doctorate": "Doctorat",
        }
        key = raw.strip().lower()
        return mapping.get(key, raw)

    def _extract_salary(self, base_salary: Any) -> dict[str, Any] | None:
        """Extrait les informations de salaire du dictionnaire JSON-LD."""
        if not base_salary:
            return None

        if isinstance(base_salary, dict):
            currency = base_salary.get("currency")
            value = base_salary.get("value")
            if isinstance(value, dict):
                min_value = value.get("minValue")
                max_value = value.get("maxValue")
                unit_text = value.get("unitText")
                libelle_parts = []
                if min_value is not None and max_value is not None:
                    libelle_parts.append(f"{min_value}-{max_value}")
                elif min_value is not None:
                    libelle_parts.append(f">= {min_value}")
                elif max_value is not None:
                    libelle_parts.append(f"<= {max_value}")
                if currency:
                    libelle_parts.append(str(currency))
                if unit_text:
                    libelle_parts.append(str(unit_text))
                libelle = " ".join(libelle_parts).strip() or None
                return {
                    "libelle": libelle,
                    "min": min_value,
                    "max": max_value,
                    "devise": currency,
                    "unite": unit_text,
                }

            flat_text = self._extract_requirement_text(base_salary)
            if flat_text:
                return {"libelle": flat_text}

        if isinstance(base_salary, str):
            cleaned = self._strip_html(base_salary)
            if cleaned:
                return {"libelle": cleaned}

        return None

    def _strip_html(self, value: str) -> str:
        """Supprime les balises HTML d'une chaîne de caractères."""
        if not value:
            return ""
        parser = HtmlTextExtractor()
        parser.feed(value)
        parser.close()
        return parser.text()

    def _is_jobposting_type(self, type_value: Any) -> bool:
        """Vérifie si le type JSON-LD correspond à une offre d'emploi."""
        if isinstance(type_value, str):
            return type_value.strip().lower() == "jobposting"
        if isinstance(type_value, list):
            return any(
                isinstance(item, str) and item.strip().lower() == "jobposting"
                for item in type_value
            )
        return False

    def _nested_value(self, payload: dict[str, Any], path: str) -> Any:
        """Récupère une valeur dans un dictionnaire imbriqué via un chemin pointé (ex: 'lieu.commune')."""
        current: Any = payload
        for key in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current

    def extract_offers(self, html: str) -> list[dict[str, Any]]:
        """Extrait toutes les offres d'emploi détectées dans le code HTML (via JSON-LD)."""
        parser = JsonLdParser()
        parser.feed(html)

        offers: list[dict[str, Any]] = []
        for blob in parser.json_blobs:
            try:
                payload = json.loads(blob)
            except Exception:
                continue

            entries: list[dict[str, Any]] = []
            if isinstance(payload, dict):
                entries = [payload]
                if isinstance(payload.get("@graph"), list):
                    entries.extend(
                        [node for node in payload["@graph"] if isinstance(node, dict)]
                    )
            elif isinstance(payload, list):
                entries = [item for item in payload if isinstance(item, dict)]

            for item in entries:
                if self._is_jobposting_type(item.get("@type")):
                    offers.append(self._normalize_jobposting(item))

        # Fallback minimal si JSON-LD absent: on remonte les URLs jobs detectees.
        if not offers:
            offers = self._fallback_extract_links(html)

        dedup: dict[str, dict[str, Any]] = {}
        for offer in offers:
            key = self._nested_value(offer, "origineOffre.urlOrigine") or json.dumps(
                offer, sort_keys=True, ensure_ascii=False
            )
            dedup[key] = offer
        return list(dedup.values())

    def _extract_first_jobposting_from_html(self, html: str) -> dict[str, Any] | None:
        """Extrait la première offre d'emploi JobPosting d'un contenu HTML."""
        parser = JsonLdParser()
        parser.feed(html)

        for blob in parser.json_blobs:
            try:
                payload = json.loads(blob)
            except Exception:
                continue

            entries: list[dict[str, Any]] = []
            if isinstance(payload, dict):
                entries = [payload]
                if isinstance(payload.get("@graph"), list):
                    entries.extend(
                        [node for node in payload["@graph"] if isinstance(node, dict)]
                    )
            elif isinstance(payload, list):
                entries = [item for item in payload if isinstance(item, dict)]

            for item in entries:
                if self._is_jobposting_type(item.get("@type")):
                    return self._normalize_jobposting(item)
        return None

    def _needs_detail_enrichment(self, offer: dict[str, Any]) -> bool:
        """Détermine si une offre a besoin d'être enrichie via sa page de détail."""
        # Si plusieurs champs sont absents, on considere l'offre incomplète.
        missing_fields = [
            field
            for field in (
                "intitule",
                "entreprise.nom",
                "lieuTravail.libelle",
                "dateCreation",
                "typeContrat",
                "description",
                "salaire",
                "experienceRequise",
                "niveauEtude",
            )
            if not self._nested_value(offer, field)
        ]
        return len(missing_fields) >= 2 and bool(
            self._nested_value(offer, "origineOffre.urlOrigine")
        )

    def _merge_offer(
        self, base_offer: dict[str, Any], detail_offer: dict[str, Any]
    ) -> dict[str, Any]:
        """Fusionne les informations d'une offre de base avec celles de sa page de détail."""
        merged = {**base_offer}
        for key, value in detail_offer.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged_sub = {**merged[key]}
                for sub_key, sub_value in value.items():
                    if sub_value and not merged_sub.get(sub_key):
                        merged_sub[sub_key] = sub_value
                merged[key] = merged_sub
            else:
                if value and not merged.get(key):
                    merged[key] = value
        return merged

    def enrich_offers_from_detail_pages(
        self, offers: list[dict[str, Any]], delay_seconds: float = 0.2
    ) -> list[dict[str, Any]]:
        """Parcourt les offres et va chercher les détails sur leurs pages respectives si nécessaire."""
        enriched: list[dict[str, Any]] = []
        for offer in offers:
            if not self._needs_detail_enrichment(offer):
                enriched.append(offer)
                continue

            url = str(self._nested_value(offer, "origineOffre.urlOrigine"))
            try:
                html = self.fetch_job_page(url)
                details = self._extract_first_jobposting_from_html(html)
                if details:
                    enriched.append(self._merge_offer(offer, details))
                else:
                    enriched.append(offer)
            except Exception:
                # On garde l'offre telle quelle pour ne pas interrompre toute la collecte.
                enriched.append(offer)

            if delay_seconds > 0:
                time.sleep(delay_seconds)

        return enriched

    def _fallback_extract_links(self, html: str) -> list[dict[str, Any]]:
        """Méthode de secours pour extraire les liens d'offres si le JSON-LD est manquant."""
        links: list[dict[str, Any]] = []
        marker = "/fr/companies/"
        start = 0
        while True:
            idx = html.find(marker, start)
            if idx == -1:
                break
            href_start = html.rfind('"', 0, idx)
            href_end = html.find('"', idx)
            if href_start == -1 or href_end == -1:
                start = idx + len(marker)
                continue
            href = html[href_start + 1 : href_end]
            if "/jobs/" in href:
                full_url = (
                    href if href.startswith("http") else f"{self.config.base_url}{href}"
                )
                offer_id = self._build_offer_id(url=full_url, title=None)
                links.append(
                    {
                        "id": offer_id,
                        "intitule": None,
                        "description": None,
                        "dateCreation": None,
                        "lieuTravail": {
                            "libelle": None,
                            "codePostal": None,
                            "commune": None,
                        },
                        "entreprise": {
                            "nom": None,
                        },
                        "typeContrat": None,
                        "typeContratLibelle": None,
                        "alternance": False,
                        "salaire": None,
                        "experienceRequise": None,
                        "niveauEtude": None,
                        "origineOffre": {
                            "origine": "2",
                            "urlOrigine": full_url,
                        },
                        "source": "welcome_to_the_jungle",
                    }
                )
            start = idx + len(marker)
        return links


class WTTJCollector:
    """Orchestrateur de la collecte : gère la pagination et l'export final."""

    def __init__(self, scraper: WelcomeToTheJungleScraper) -> None:
        self.scraper = scraper

    def collect(
        self,
        query: str,
        page: int,
        max_pages: int,
        output_path: Path,
        detail_delay_seconds: float = 0.2,
    ) -> Path:
        """Lance la collecte sur plusieurs pages et enregistre le résultat dans un JSON."""
        pages_to_collect = max(1, max_pages)
        all_offers: list[dict[str, Any]] = []
        dedup_by_url: dict[str, dict[str, Any]] = {}

        for current_page in range(page, page + pages_to_collect):
            html = self.scraper.fetch_search_page(query=query, page=current_page)
            offers = self.scraper.extract_offers(html)
            # Enrichissement (optionnel, selon le besoin détecté automatiquement)
            offers = self.scraper.enrich_offers_from_detail_pages(
                offers, delay_seconds=detail_delay_seconds
            )
            all_offers.extend(offers)

        # Dédoublonnage final basé sur l'URL de l'offre
        for offer in all_offers:
            key = self.scraper._nested_value(
                offer, "origineOffre.urlOrigine"
            ) or offer.get("id")
            dedup_by_url[str(key)] = offer

        offers = list(dedup_by_url.values())
        payload = {
            "source": "welcome_to_the_jungle",
            "collected_at_utc": datetime.now(timezone.utc).isoformat(),
            "query": {"query": query, "page": page, "max_pages": pages_to_collect},
            "total": len(offers),
            "offers": offers,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output_path


def build_arg_parser() -> argparse.ArgumentParser:
    """Configure les arguments CLI pour le scraper."""
    parser = argparse.ArgumentParser(
        description="Scraping des offres Welcome to the Jungle."
    )
    parser.add_argument("--query", default="", help="Mots-clés de recherche")
    parser.add_argument("--page", type=int, default=1, help="Page de départ")
    parser.add_argument(
        "--max-pages", type=int, default=3, help="Nombre de pages à scraper"
    )
    parser.add_argument(
        "--detail-delay-seconds",
        type=float,
        default=0.2,
        help="Délai entre requêtes de détail",
    )
    parser.add_argument(
        "--output",
        default="data/raw/wttj_offers.json",
        help="Chemin du fichier JSON de sortie.",
    )
    parser.add_argument(
        "--skip-minio",
        action="store_true",
        help="N'upload pas le JSON brut vers MinIO.",
    )
    return parser


def main() -> int:
    """Point d'entrée principal : charge la config, lance le scraper et l'upload MinIO."""
    args = build_arg_parser().parse_args()
    try:
        load_dotenv_file()
        scraper_config = WTTJScraperConfig.from_env()
        scraper = WelcomeToTheJungleScraper(scraper_config)
        collector = WTTJCollector(scraper)

        output_path = collector.collect(
            query=args.query,
            page=args.page,
            max_pages=args.max_pages,
            output_path=Path(args.output),
            detail_delay_seconds=args.detail_delay_seconds,
        )
        print(f"Scraping termine (fichier local): {output_path}")

        if not args.skip_minio:
            minio_config = MinioConfig.from_env()
            minio_storage = MinioRawStorage(minio_config)
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            object_key = minio_storage.upload_raw_json(
                payload, source="welcome_to_the_jungle"
            )
            print(
                f"Upload MinIO termine: bucket={minio_config.bucket}, object={object_key}"
            )
        return 0
    except Exception as exc:
        print(f"Erreur scraping WTTJ: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
