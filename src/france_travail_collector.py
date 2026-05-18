from __future__ import annotations

import argparse
import io
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request


@dataclass
class FranceTravailConfig:
    """Configuration pour l'accès à l'API France Travail (ex-Pôle Emploi)."""
    client_id: str
    client_secret: str
    scope: str
    token_url: str
    search_url: str
    timeout_seconds: int = 20

    @classmethod
    def from_env(cls) -> "FranceTravailConfig":
        """Crée une configuration France Travail à partir des variables d'environnement."""
        env = os.environ
        required = {
            "FRANCE_TRAVAIL_CLIENT_ID": env.get("FRANCE_TRAVAIL_CLIENT_ID", "").strip(),
            "FRANCE_TRAVAIL_CLIENT_SECRET": env.get(
                "FRANCE_TRAVAIL_CLIENT_SECRET", ""
            ).strip(),
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise ValueError(
                "Variables d'environnement manquantes: " + ", ".join(missing)
            )

        return cls(
            client_id=required["FRANCE_TRAVAIL_CLIENT_ID"],
            client_secret=required["FRANCE_TRAVAIL_CLIENT_SECRET"],
            scope=env.get(
                "FRANCE_TRAVAIL_SCOPE",
                "api_offresdemploiv2 o2dsoffre",
            ),
            token_url=env.get(
                "FRANCE_TRAVAIL_TOKEN_URL",
                "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire",
            ),
            search_url=env.get(
                "FRANCE_TRAVAIL_SEARCH_URL",
                "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search",
            ),
            timeout_seconds=int(env.get("FRANCE_TRAVAIL_TIMEOUT_SECONDS", "20")),
        )


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
            bucket=env.get("MINIO_BUCKET_RAW_API", "raw-api").strip() or "raw-api",
            secure=secure,
        )


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


class FranceTravailApiClient:
    """Client HTTP pour l'API France Travail (OAuth2 + Recherche d'offres)."""
    def __init__(self, config: FranceTravailConfig) -> None:
        self.config = config

    def _get_access_token(self) -> str:
        """Obtient un jeton d'accès OAuth2 via client_credentials."""
        form_data = parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "scope": self.config.scope,
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        req = request.Request(self.config.token_url, data=form_data, headers=headers)

        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Echec auth France Travail ({exc.code}): {details}"
            ) from exc
        except error.URLError as exc:
            raise RuntimeError(
                f"Echec connexion auth France Travail: {exc.reason}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Echec technique auth France Travail: {exc}") from exc

        access_token = payload.get("access_token")
        if not access_token:
            raise RuntimeError("Token d'acces absent dans la reponse France Travail.")
        return access_token

    def search_offers(
        self,
        keywords: str,
        location: str = "",
        range_: str = "0-49",
        contract_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Lance une requête de recherche d'offres sur l'API France Travail."""
        token = self._get_access_token()
        params: dict[str, str] = {"range": range_}
        if keywords.strip():
            params["motsCles"] = keywords
        if location.strip():
            params["lieux"] = location
        if contract_type:
            params["typeContrat"] = contract_type

        url = f"{self.config.search_url}?{parse.urlencode(params)}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        req = request.Request(url, headers=headers)

        try:
            with request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            # L'API renvoie 204 si aucun résultat, ce n'est pas une erreur fatale
            if exc.code == 204:
                return []
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Echec recherche France Travail ({exc.code}): {details}"
            ) from exc
        except error.URLError as exc:
            raise RuntimeError(
                f"Echec connexion recherche France Travail: {exc.reason}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"Echec technique recherche France Travail: {exc}"
            ) from exc

        return payload.get("resultats", [])


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
        self, payload: dict[str, Any], source: str = "france_travail"
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


class OfferCollector:
    """Orchestrateur de la collecte : gère la pagination et le dédoublonnage."""
    def __init__(self, client: FranceTravailApiClient) -> None:
        self.client = client

    def collect(
        self,
        keywords: str,
        location: str,
        pages: int,
        contract_type: str | None,
        output_path: Path,
        page_size: int = 50,
    ) -> Path:
        """Lance la collecte sur plusieurs pages et sauvegarde le JSON unifié localement."""
        page_count = max(1, pages)
        size = max(1, page_size)
        offers: list[dict[str, Any]] = []
        ranges: list[str] = []
        
        for page_index in range(page_count):
            start = page_index * size
            end = start + size - 1
            current_range = f"{start}-{end}"
            ranges.append(current_range)
            
            # Appel API pour la page courante
            page_results = self.client.search_offers(
                keywords=keywords,
                location=location,
                range_=current_range,
                contract_type=contract_type,
            )
            offers.extend(page_results)

        # Dédoublonnage simple basé sur l'ID FT
        dedup_by_id: dict[str, dict[str, Any]] = {}
        for offer in offers:
            key = str(offer.get("id") or json.dumps(offer, sort_keys=True))
            dedup_by_id[key] = offer
        offers = list(dedup_by_id.values())

        # Formatage du payload final
        output = {
            "source": "france_travail_api",
            "collected_at_utc": datetime.now(timezone.utc).isoformat(),
            "query": {
                "keywords": keywords,
                "location": location,
                "pages": page_count,
                "page_size": size,
                "ranges": ranges,
                "contract_type": contract_type,
            },
            "total": len(offers),
            "offers": offers,
        }
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output_path


def build_arg_parser() -> argparse.ArgumentParser:
    """Configure les arguments CLI pour le collecteur France Travail."""
    parser = argparse.ArgumentParser(
        description="Collecte des offres d'emploi via l'API France Travail."
    )
    parser.add_argument("--keywords", default="", help="Mots-clés de recherche")
    parser.add_argument("--location", default="", help="Lieux (codes INSEE, départements, régions)")
    parser.add_argument("--pages", type=int, default=3, help="Nombre de pages de 50 offres")
    parser.add_argument("--page-size", type=int, default=50, help="Nombre d'offres par page (max 150)")
    parser.add_argument("--contract-type", default=None, help="Types de contrat (CDI, CDD, etc.)")
    parser.add_argument(
        "--output",
        default="data/raw/france_travail_offers.json",
        help="Chemin du fichier JSON de sortie.",
    )
    parser.add_argument(
        "--skip-minio",
        action="store_true",
        help="N'upload pas le JSON brut vers MinIO.",
    )
    return parser


def main() -> int:
    """Point d'entrée principal : auth, collecte, sauvegarde et upload MinIO."""
    args = build_arg_parser().parse_args()
    try:
        load_dotenv_file()
        config = FranceTravailConfig.from_env()
        client = FranceTravailApiClient(config)
        collector = OfferCollector(client)
        
        output_path = collector.collect(
            keywords=args.keywords,
            location=args.location,
            pages=args.pages,
            page_size=args.page_size,
            contract_type=args.contract_type,
            output_path=Path(args.output),
        )
        print(f"Collecte terminee (fichier local): {output_path}")

        if not args.skip_minio:
            minio_config = MinioConfig.from_env()
            minio_storage = MinioRawStorage(minio_config)
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            object_key = minio_storage.upload_raw_json(payload, source="france_travail")
            print(
                f"Upload MinIO termine: bucket={minio_config.bucket}, object={object_key}"
            )
        return 0
    except Exception as exc:
        print(f"Erreur collecte France Travail: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
