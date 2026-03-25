import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
from france_travail_collector import (  # noqa: E402
    FranceTravailApiClient,
    FranceTravailConfig,
    MinioConfig,
    MinioRawStorage,
    OfferCollector,
    load_dotenv_file,
)


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class FakeMinioClient:
    def __init__(self):
        self.created_buckets = []
        self.objects = []
        self._exists = False

    def bucket_exists(self, bucket_name):
        return self._exists

    def make_bucket(self, bucket_name):
        self.created_buckets.append(bucket_name)
        self._exists = True

    def put_object(self, bucket_name, object_name, data, length, content_type):
        body = data.read()
        self.objects.append(
            {
                "bucket": bucket_name,
                "object_name": object_name,
                "length": length,
                "content_type": content_type,
                "body": body,
            }
        )


def test_from_env_requires_credentials(monkeypatch):
    # Verifie qu'on bloque explicitement si les credentials ne sont pas presentes.
    monkeypatch.delenv("FRANCE_TRAVAIL_CLIENT_ID", raising=False)
    monkeypatch.delenv("FRANCE_TRAVAIL_CLIENT_SECRET", raising=False)
    with pytest.raises(ValueError):
        FranceTravailConfig.from_env()


def test_load_dotenv_file_loads_values(tmp_path, monkeypatch):
    # Verifie que le chargeur .env remplit bien os.environ, y compris les valeurs quotees.
    env_file = tmp_path / ".env"
    env_file.write_text(
        "FRANCE_TRAVAIL_CLIENT_ID=my-id\n"
        "FRANCE_TRAVAIL_CLIENT_SECRET=my-secret\n"
        'FRANCE_TRAVAIL_SCOPE="api_offresdemploiv2 o2dsoffre"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("FRANCE_TRAVAIL_CLIENT_ID", raising=False)
    monkeypatch.delenv("FRANCE_TRAVAIL_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("FRANCE_TRAVAIL_SCOPE", raising=False)

    load_dotenv_file(str(env_file))
    cfg = FranceTravailConfig.from_env()

    assert cfg.client_id == "my-id"
    assert cfg.client_secret == "my-secret"
    assert cfg.scope == "api_offresdemploiv2 o2dsoffre"


def test_search_offers_returns_results():
    # Simule l'auth + la recherche API pour verifier le parsing des offres renvoyees.
    cfg = FranceTravailConfig(
        client_id="id",
        client_secret="secret",
        scope="scope",
        token_url="https://token.test",
        search_url="https://search.test",
    )
    client = FranceTravailApiClient(cfg)

    responses = [
        FakeResponse({"access_token": "abc-token"}),
        FakeResponse({"resultats": [{"id": "A1"}, {"id": "A2"}]}),
    ]

    with patch("france_travail_collector.request.urlopen", side_effect=responses):
        results = client.search_offers("data engineer")

    assert len(results) == 2
    assert results[0]["id"] == "A1"


def test_collect_writes_json(tmp_path):
    # Verifie le flux complet de collecte: appel client, serialisation et ecriture fichier.
    cfg = FranceTravailConfig(
        client_id="id",
        client_secret="secret",
        scope="scope",
        token_url="https://token.test",
        search_url="https://search.test",
    )
    client = FranceTravailApiClient(cfg)
    collector = OfferCollector(client)
    out_file = tmp_path / "offers.json"

    responses = [
        FakeResponse({"access_token": "abc-token"}),
        FakeResponse({"resultats": [{"id": "A1"}]}),
        FakeResponse({"access_token": "abc-token"}),
        FakeResponse({"resultats": [{"id": "A2"}]}),
        FakeResponse({"access_token": "abc-token"}),
        FakeResponse({"resultats": [{"id": "A3"}]}),
    ]

    with patch("france_travail_collector.request.urlopen", side_effect=responses):
        written_path = collector.collect(
            keywords="data scientist",
            location="75",
            pages=3,
            page_size=1,
            contract_type=None,
            output_path=out_file,
        )

    assert written_path == out_file
    assert out_file.exists()
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["source"] == "france_travail_api"
    assert payload["total"] == 3
    assert payload["query"]["pages"] == 3
    assert payload["query"]["ranges"] == ["0-0", "1-1", "2-2"]


def test_collect_with_range_override(tmp_path):
    # Le collecteur construit simplement les ranges pages*page_size.
    cfg = FranceTravailConfig(
        client_id="id",
        client_secret="secret",
        scope="scope",
        token_url="https://token.test",
        search_url="https://search.test",
    )
    client = FranceTravailApiClient(cfg)
    collector = OfferCollector(client)
    out_file = tmp_path / "offers_ranges.json"

    responses = [
        FakeResponse({"access_token": "abc-token"}),
        FakeResponse({"resultats": [{"id": "A1"}]}),
        FakeResponse({"access_token": "abc-token"}),
        FakeResponse({"resultats": [{"id": "A2"}]}),
    ]

    with patch("france_travail_collector.request.urlopen", side_effect=responses):
        collector.collect(
            keywords="",
            location="",
            pages=2,
            page_size=10,
            contract_type=None,
            output_path=out_file,
        )

    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["query"]["pages"] == 2
    assert payload["query"]["ranges"] == ["0-9", "10-19"]


def test_minio_upload_creates_bucket_and_uploads():
    # Verifie la partie Data Lake: bucket brut puis upload de l'objet JSON.
    cfg = MinioConfig(
        endpoint="localhost:9000",
        access_key="admin",
        secret_key="password",
        bucket="raw-api",
        secure=False,
    )
    fake_client = FakeMinioClient()
    storage = MinioRawStorage(cfg, minio_client=fake_client)

    object_key = storage.upload_raw_json(
        {"source": "france_travail_api", "offers": [{"id": "A1"}]},
        source="france_travail",
    )

    assert fake_client.created_buckets == ["raw-api"]
    assert len(fake_client.objects) == 1
    assert fake_client.objects[0]["bucket"] == "raw-api"
    assert fake_client.objects[0]["content_type"] == "application/json"
    assert object_key.startswith("france_travail/")
