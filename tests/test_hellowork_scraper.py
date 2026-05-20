import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
from hellowork_scraper import (  # noqa: E402
    HelloWorkCollector,
    HelloWorkScraperConfig,
    HelloWorkScraper,
)


def test_hellowork_extract_offers_from_json_ld():
    html = """
    <html>
      <head>
        <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Data Analyst",
            "url": "https://www.hellowork.com/fr-fr/emploi/data-analyst-paris-123.html",
            "datePosted": "2026-03-22",
            "employmentType": "FULL_TIME",
            "baseSalary": {
              "@type": "MonetaryAmount",
              "currency": "EUR",
              "value": {"@type": "QuantitativeValue", "minValue": 40000, "maxValue": 45000}
            },
            "hiringOrganization": {"name": "Tech Corp"},
            "jobLocation": {"address": {"addressLocality": "Lyon", "addressCountry": "FR"}}
          }
        </script>
      </head>
      <body></body>
    </html>
    """

    cfg = HelloWorkScraperConfig(
        base_url="https://www.hellowork.com",
        timeout_seconds=20,
        user_agent="test-agent",
    )
    scraper = HelloWorkScraper(cfg)
    offers = scraper.extract_offers(html)

    assert len(offers) == 1
    assert offers[0]["intitule"] == "Data Analyst"
    assert offers[0]["entreprise"]["nom"] == "Tech Corp"
    assert offers[0]["lieuTravail"]["commune"] == "Lyon"
    assert offers[0]["salaire"]["min"] == 40000
    assert offers[0]["typeContrat"] == "CDI"
    assert offers[0]["alternance"] is False
    assert offers[0]["source"] == "hellowork"


def test_hellowork_collect_full_flow(tmp_path):
    cfg = HelloWorkScraperConfig(
        base_url="https://www.hellowork.com",
        timeout_seconds=20,
        user_agent="test-agent",
    )
    scraper = HelloWorkScraper(cfg)
    collector = HelloWorkCollector(scraper)

    search_html = """
    <html>
      <body>
        <a href="/fr-fr/emplois/123.html">Job 1</a>
        <a href="/fr-fr/emplois/456.html">Job 2</a>
      </body>
    </html>
    """
    
    detail_html = """
    <html>
      <head>
        <script type="application/ld+json">
          {
            "@type": "JobPosting",
            "title": "Data Analyst",
            "url": "https://www.hellowork.com/fr-fr/emploi/123.html",
            "datePosted": "2026-03-22",
            "employmentType": "FULL_TIME",
            "hiringOrganization": {"name": "Tech Corp"},
            "jobLocation": {"address": {"addressLocality": "Lyon", "addressCountry": "FR"}}
          }
        </script>
      </head>
    </html>
    """

    # Mock search page
    scraper.fetch_search_page = lambda query, location, page: search_html  # type: ignore
    # Mock detail page
    scraper.fetch_job_page = lambda url: detail_html  # type: ignore

    out_file = tmp_path / "hellowork.json"
    written = collector.collect(
        query="data",
        location="",
        max_pages=1,
        output_path=out_file,
    )

    assert written == out_file
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    # Dédoublonnage par ID (les deux URLs de la search pointent vers le même JSON-LD dans le mock)
    assert payload["total"] == 1
    assert payload["source"] == "hellowork"
    assert payload["offers"][0]["intitule"] == "Data Analyst"
