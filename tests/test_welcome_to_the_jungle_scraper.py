import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
from welcome_to_the_jungle_scraper import (  # noqa: E402
    WTTJCollector,
    WTTJScraperConfig,
    WelcomeToTheJungleScraper,
)


def test_extract_offers_from_json_ld():
    html = """
    <html>
      <head>
        <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Data Engineer",
            "url": "https://www.welcometothejungle.com/fr/companies/acme/jobs/data-engineer",
            "datePosted": "2026-03-22",
            "employmentType": "CDI",
            "baseSalary": {
              "@type": "MonetaryAmount",
              "currency": "EUR",
              "value": {"@type": "QuantitativeValue", "minValue": 45000, "maxValue": 55000}
            },
            "experienceRequirements": {"monthsOfExperience": 36},
            "educationRequirements": {"credentialCategory": "Master Degree"},
            "hiringOrganization": {"name": "Acme"},
            "jobLocation": {"address": {"addressLocality": "Paris", "addressCountry": "FR"}}
          }
        </script>
      </head>
      <body></body>
    </html>
    """

    cfg = WTTJScraperConfig(
        base_url="https://www.welcometothejungle.com",
        timeout_seconds=20,
        user_agent="test-agent",
    )
    scraper = WelcomeToTheJungleScraper(cfg)
    offers = scraper.extract_offers(html)

    assert len(offers) == 1
    assert offers[0]["intitule"] == "Data Engineer"
    assert offers[0]["entreprise"]["nom"] == "Acme"
    assert offers[0]["lieuTravail"]["commune"] == "Paris"
    assert offers[0]["salaire"]["min"] == 45000
    assert offers[0]["experienceRequise"] == "36 mois (~3 ans)"
    assert offers[0]["niveauEtude"] == "Bac+5"


def test_fallback_extract_offers_from_links():
    html = """
    <html><body>
      <a href="/fr/companies/acme/jobs/data-analyst_paris">Offer 1</a>
      <a href="/fr/companies/other/jobs/data-scientist_lyon">Offer 2</a>
    </body></html>
    """
    cfg = WTTJScraperConfig(
        base_url="https://www.welcometothejungle.com",
        timeout_seconds=20,
        user_agent="test-agent",
    )
    scraper = WelcomeToTheJungleScraper(cfg)
    offers = scraper.extract_offers(html)

    assert len(offers) == 2
    assert offers[0]["origineOffre"]["urlOrigine"].startswith(
        "https://www.welcometothejungle.com/fr/companies/"
    )


def test_enrich_offers_from_detail_pages():
    listing_html = """
    <html><body>
      <a href="/fr/companies/acme/jobs/data-engineer_paris">Offer 1</a>
    </body></html>
    """
    detail_html = """
    <html><head>
      <script type="application/ld+json">
        {
          "@type": "JobPosting",
          "title": "Data Engineer",
          "url": "https://www.welcometothejungle.com/fr/companies/acme/jobs/data-engineer_paris",
          "datePosted": "2026-03-23",
          "employmentType": "CDI",
          "hiringOrganization": {"name": "Acme"},
          "jobLocation": {"address": {"addressLocality": "Paris", "addressCountry": "FR"}}
        }
      </script>
    </head></html>
    """
    cfg = WTTJScraperConfig(
        base_url="https://www.welcometothejungle.com",
        timeout_seconds=20,
        user_agent="test-agent",
    )
    scraper = WelcomeToTheJungleScraper(cfg)
    offers = scraper.extract_offers(listing_html)
    assert len(offers) == 1
    assert offers[0]["intitule"] is None

    scraper.fetch_job_page = lambda _url: detail_html  # type: ignore
    enriched = scraper.enrich_offers_from_detail_pages(offers, delay_seconds=0)

    assert enriched[0]["intitule"] == "Data Engineer"
    assert enriched[0]["entreprise"]["nom"] == "Acme"
    assert enriched[0]["lieuTravail"]["commune"] == "Paris"


def test_collect_with_max_pages_deduplicates(tmp_path):
    cfg = WTTJScraperConfig(
        base_url="https://www.welcometothejungle.com",
        timeout_seconds=20,
        user_agent="test-agent",
    )
    scraper = WelcomeToTheJungleScraper(cfg)
    collector = WTTJCollector(scraper)

    html_page = """
    <html>
      <head>
        <script type="application/ld+json">
          {
            "@type": "JobPosting",
            "title": "Data Engineer",
            "url": "https://www.welcometothejungle.com/fr/companies/acme/jobs/data-engineer",
            "datePosted": "2026-03-23",
            "employmentType": "FULL_TIME",
            "hiringOrganization": {"name": "Acme"},
            "jobLocation": {"address": {"addressLocality": "Paris", "addressCountry": "FR"}}
          }
        </script>
      </head>
      <body></body>
    </html>
    """

    scraper.fetch_search_page = lambda query, page: html_page  # type: ignore
    scraper.enrich_offers_from_detail_pages = lambda offers, delay_seconds: offers  # type: ignore

    out_file = tmp_path / "wttj.json"
    written = collector.collect(
        query="data scientist",
        page=1,
        max_pages=3,
        output_path=out_file,
        detail_delay_seconds=0,
    )

    assert written == out_file
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["query"]["max_pages"] == 3
    assert payload["total"] == 1
