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
        self, payload: dict[str, Any], source: str = "hellowork"
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
class HelloWorkScraperConfig:
    """Configuration spécifique au scraper HelloWork."""

    base_url: str
    timeout_seconds: int
    user_agent: str

    @classmethod
    def from_env(cls) -> "HelloWorkScraperConfig":
        env = os.environ
        return cls(
            base_url=env.get("HELLOWORK_BASE_URL", "https://www.hellowork.com"),
            timeout_seconds=int(env.get("HELLOWORK_TIMEOUT_SECONDS", "20")),
            user_agent=env.get(
                "HELLOWORK_USER_AGENT",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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
        return " ".join(raw.split())


class HelloWorkScraper:
    """Cœur du scraper HelloWork : gère les requêtes HTTP et l'extraction des données JSON-LD."""

    def __init__(self, config: HelloWorkScraperConfig) -> None:
        self.config = config

    def fetch_search_page(self, query: str, location: str = "", page: int = 1) -> str:
        """Récupère le code HTML de la page de recherche."""
        params = parse.urlencode({"k": query, "l": location, "p": page})
        url = f"{self.config.base_url}/fr-fr/emploi/recherche.html?{params}"
        headers = {"User-Agent": self.config.user_agent}
        req = request.Request(url, headers=headers)

        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            raise RuntimeError(f"Echec scraping HelloWork search: {exc}")

    def fetch_job_page(self, job_url: str) -> str:
        """Récupère le code HTML de la page de détail d'une offre."""
        headers = {"User-Agent": self.config.user_agent}
        req = request.Request(job_url, headers=headers)

        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            raise RuntimeError(f"Echec scraping HelloWork detail: {exc}")

    def _normalize_jobposting(self, job: dict[str, Any]) -> dict[str, Any]:
        """Convertit un objet JSON-LD 'JobPosting' HelloWork en dictionnaire normalisé."""
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
        exp = job.get("experienceRequirements")
        experience_required = exp.get("monthsOfExperience") if isinstance(exp, dict) else None

        
        
        # Mapping contract type
        type_contrat = self._map_contract_type(employment_type)
        
        # Salaire
        salaire = self._extract_salary(job.get("baseSalary"))
        
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
            "alternance": "ALTERNANCE" in str(employment_type).upper() or "CONTRACTOR" in str(employment_type).upper(),
            "salaire": salaire,
            "experienceRequise": experience_required,
            "niveauEtude": None,
            "origineOffre": {
                "origine": "3", # Convention arbitraire pour HelloWork
                "urlOrigine": url,
            },
            "source": "hellowork",
        }

    def _build_offer_id(self, url: Any, title: Any) -> str:
        raw = str(url or title or "")
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8].upper()
        return f"HW_{digest}"

    def _build_lieu_libelle(self, city: Any, country: Any) -> str | None:
        city_s = str(city).strip() if city else ""
        country_s = str(country).strip() if country else ""
        if city_s and country_s:
            return f"{city_s} ({country_s})"
        return city_s or country_s or None

    def _map_contract_type(self, employment_type: Any) -> str | None:
        if not employment_type:
            return None
        val = str(employment_type).upper()
        if "FULL_TIME" in val: return "CDI"
        if "TEMPORARY" in val: return "CDD"
        if "INTERN" in val: return "Stage"
        if "CONTRACTOR" in val: return "Alternance"
        return val

    def _extract_salary(self, base_salary: Any) -> dict[str, Any] | None:
        if not base_salary or not isinstance(base_salary, dict):
            return None
        value = base_salary.get("value")
        currency = base_salary.get("currency")
        if not isinstance(value, dict):
            return None
        min_val = value.get("minValue")
        max_val = value.get("maxValue")
        # Si pas de valeurs, on retourne None plutôt qu'un objet vide
        if not min_val and not max_val:
            return None
        return {
            "libelle": f"{min_val}-{max_val} {currency}",
            "min": min_val,
            "max": max_val,
            "devise": currency,
            "unite": value.get("unitText"),
        }

    def _strip_html(self, value: str) -> str:
        if not value: return ""
        parser = HtmlTextExtractor()
        parser.feed(value)
        parser.close()
        return parser.text()

    def _extract_job_urls(self, html: str) -> list[str]:
        """Extrait les URLs des offres d'emploi depuis la page de recherche."""
        urls: set[str] = set()
        # Pattern typique: /fr-fr/emplois/77964195.html
        import re
        pattern = r'href="(/fr-fr/emplois/[0-9]+\.html)"'
        matches = re.findall(pattern, html)
        for m in matches:
            if m.startswith("/"):
                urls.add(f"{self.config.base_url}{m}")
            else:
                urls.add(m)
        return list(urls)

    def extract_offers(self, html: str) -> list[dict[str, Any]]:
        """Extrait les offres JobPosting du contenu HTML (page de détail)."""
        parser = JsonLdParser()
        parser.feed(html)
        offers: list[dict[str, Any]] = []
        for blob in parser.json_blobs:
            try:
                payload = json.loads(blob)
                items = []
                if isinstance(payload, list):
                    items = payload
                elif isinstance(payload, dict):
                    items = payload.get("@graph", [payload])
                
                for item in items:
                    if isinstance(item, dict) and str(item.get("@type")).lower() == "jobposting":
                        offers.append(self._normalize_jobposting(item))
            except Exception:
                continue
        return offers


class HelloWorkCollector:
    def __init__(self, scraper: HelloWorkScraper) -> None:
        self.scraper = scraper

    def collect(
        self,
        query: str,
        location: str,
        max_pages: int,
        output_path: Path,
    ) -> Path:
        job_urls: set[str] = set()
        
        # 1. Extraction des URLs depuis les pages de recherche
        for p in range(1, max_pages + 1):
            print(f"Extraction URLs HelloWork - Page {p}...")
            html = self.scraper.fetch_search_page(query, location, p)
            urls = self.scraper._extract_job_urls(html)
            job_urls.update(urls)
            if p < max_pages:
                time.sleep(1)

        print(f"Nombre d'URLs trouvées : {len(job_urls)}")

        # 2. Collecte des détails pour chaque URL
        all_offers: list[dict[str, Any]] = []
        for i, url in enumerate(list(job_urls)):
            try:
                print(f"[{i+1}/{len(job_urls)}] Scraping détail : {url}")
                html = self.scraper.fetch_job_page(url)
                offers = self.scraper.extract_offers(html)
                all_offers.extend(offers)
                time.sleep(0.5) # Politesse
            except Exception as e:
                print(f"Erreur sur {url}: {e}")

        # Dédoublonnage
        dedup = {o["id"]: o for o in all_offers}
        offers = list(dedup.values())

        payload = {
            "source": "hellowork",
            "collected_at_utc": datetime.now(timezone.utc).isoformat(),
            "total": len(offers),
            "offers": offers,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        return output_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="")
    parser.add_argument("--location", default="")
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--output", default="data/raw/hellowork_offers.json")
    parser.add_argument("--skip-minio", action="store_true")
    args = parser.parse_args()

    try:
        load_dotenv_file()
        config = HelloWorkScraperConfig.from_env()
        scraper = HelloWorkScraper(config)
        collector = HelloWorkCollector(scraper)
        
        path = collector.collect(args.query, args.location, args.max_pages, Path(args.output))
        print(f"Collecte HelloWork finie: {path}")

        if not args.skip_minio:
            m_config = MinioConfig.from_env()
            storage = MinioRawStorage(m_config)
            storage.upload_raw_json(json.loads(path.read_text()), source="hellowork")
        return 0
    except Exception as e:
        print(f"Erreur: {e}")
        return 1

if __name__ == "__main__":
    main()
