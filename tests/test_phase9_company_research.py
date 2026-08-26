import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import backend.companies as companies
import backend.database as database
import backend.services.company_importer as importer
import backend.services.company_research as research_service
from backend.companies import CompanyUpsert
from backend.services.email_generator import (
    OllamaInvalidResponseError,
    generate_email,
    select_company_personalization,
)


class FakeResponse:
    def __init__(self, body=b"", status=200, content_type="text/html", headers=None):
        self.body = body
        self.status_code = status
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.encoding = "utf-8"

    def iter_content(self, chunk_size):
        for index in range(0, len(self.body), chunk_size):
            yield self.body[index : index + chunk_size]

    def close(self):
        pass


class FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return self.responses[url]


def html(value):
    return FakeResponse(value.encode("utf-8"))


class CompanyResearchExtractionTests(unittest.TestCase):
    def test_homepage_about_product_focus_sources_and_no_external_crawl(self):
        homepage = """
        <html><head><title>Acme AI</title>
        <meta name="description" content="Acme builds grounded enterprise AI tools.">
        </head><body><a href="/products">Products</a><a href="/about">About</a>
        <a href="/careers">Careers</a><a href="https://outside.example/product">Outside</a>
        <p>Our teams build artificial intelligence systems.</p></body></html>
        """
        products = """
        <html><head><title>Products</title></head><body><main>
        <h2>Knowledge Assistant</h2><p>Our Knowledge Assistant uses RAG for enterprise search.</p>
        <script type="application/ld+json">{"@type":"SoftwareApplication","name":"Vision Studio"}</script>
        </main></body></html>
        """
        about = "<html><title>About</title><body>We develop machine learning solutions.</body></html>"
        careers = "<html><title>Careers at Acme</title><body>Join us</body></html>"
        session = FakeSession(
            {
                "https://acme.example/": html(homepage),
                "https://acme.example/products": html(products),
                "https://acme.example/about": html(about),
                "https://acme.example/careers": html(careers),
            }
        )
        with patch.object(importer, "_validate_public_host"):
            result = research_service.research_company_website("acme.example", session)

        self.assertEqual(result["company_name"], "Acme AI")
        self.assertEqual(result["summary"], "Acme builds grounded enterprise AI tools.")
        self.assertIn("RAG", result["focus_areas"])
        self.assertIn("Machine learning", result["focus_areas"])
        product_names = [item["text"] for item in result["products_or_services"]]
        self.assertIn("Knowledge Assistant", product_names)
        self.assertIn("Vision Studio", product_names)
        self.assertTrue(result["hiring_signals"])
        self.assertIn("https://acme.example/products", result["source_pages"])
        self.assertNotIn("https://outside.example/product", session.calls)
        self.assertNotIn("<html", json.dumps(result).casefold())
        rag_point = next(point for point in result["personalization_points"] if point["topics"] == ["RAG"])
        self.assertEqual(rag_point["source_url"], "https://acme.example/products")
        self.assertIn("RAG", rag_point["source_excerpt"])

    def test_homepage_plus_maximum_four_same_domain_pages(self):
        links = "".join(f'<a href="/products/{index}">Products {index}</a>' for index in range(7))
        responses = {"https://example.com/": html(f"<html><title>Example</title>{links}</html>")}
        responses.update(
            {f"https://example.com/products/{index}": html("<html><title>Product</title></html>") for index in range(7)}
        )
        session = FakeSession(responses)
        with patch.object(importer, "_validate_public_host"):
            result = research_service.research_company_website("example.com", session)
        self.assertEqual(len(session.calls), 5)
        self.assertEqual(len(result["source_pages"]), 5)

    def test_private_url_is_rejected_by_shared_fetch_protection(self):
        with patch.object(
            importer.socket,
            "getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 0))],
        ):
            with self.assertRaises(research_service.CompanyResearchError):
                research_service.research_company_website(
                    "http://private.example", FakeSession({})
                )


class CompanyResearchPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_directory.name) / "phase9.db"
        self.database_patch = patch.object(database, "DATABASE_PATH", self.db_path)
        self.database_patch.start()
        database.initialize_database()
        self.company = companies.create_company(
            CompanyUpsert(
                name="Acme",
                website="https://acme.example",
                contact_email="jobs@acme.example",
                target_position="AI Engineer",
            )
        )

    def tearDown(self):
        self.database_patch.stop()
        self.temp_directory.cleanup()

    @staticmethod
    def research_payload():
        return {
            "company_name": "Acme",
            "summary": "Acme builds RAG systems.",
            "summary_source_url": "https://acme.example/",
            "focus_areas": ["RAG"],
            "products_or_services": [{"text": "Knowledge Assistant", "source_url": "https://acme.example/products"}],
            "technologies_or_topics": ["RAG"],
            "hiring_signals": ["Careers"],
            "personalization_points": [{
                "text": "Şirketinizin RAG alanındaki çalışmaları ilgimi çekti.",
                "source_url": "https://acme.example/products",
                "source_excerpt": "Knowledge Assistant uses RAG for enterprise search.",
                "topics": ["RAG"],
            }],
            "source_pages": ["https://acme.example/", "https://acme.example/products"],
        }

    def test_research_persists_across_restart_without_raw_html(self):
        with patch.object(companies, "research_company_website", return_value=self.research_payload()):
            saved = companies.research_company(self.company.id)
        database.initialize_database()
        loaded = companies.get_company_research(self.company.id)
        self.assertEqual(saved.research.focus_areas, loaded.research.focus_areas)
        self.assertEqual(loaded.research.personalization_points[0].source_url, "https://acme.example/products")
        with database.get_connection() as connection:
            raw = connection.execute("SELECT research_json FROM company_research").fetchone()[0]
        self.assertNotIn("<html", raw.casefold())

    def test_research_invalidates_when_website_changes(self):
        with patch.object(companies, "research_company_website", return_value=self.research_payload()):
            companies.research_company(self.company.id)
        companies.update_company(
            self.company.id,
            CompanyUpsert(
                name="Acme",
                website="https://new-acme.example",
                contact_email="jobs@acme.example",
                target_position="AI Engineer",
            ),
        )
        with self.assertRaises(HTTPException) as context:
            companies.get_company_research(self.company.id)
        self.assertEqual(context.exception.status_code, 404)


class GroundedPersonalizationTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "name": "Test User",
            "target_job_title": "AI Engineer",
            "professional_summary": "Python ve yapay zeka projeleri geliştiriyorum.",
        }
        self.company = {"name": "Acme", "target_position": "AI Engineer"}
        self.evidence = {
            "relevant_skills": ["Python", "RAG"],
            "relevant_projects": [{
                "name": "Knowledge Assistant",
                "description": "RAG based assistant",
                "technologies": ["RAG", "Python"],
            }],
            "relevant_experience": [],
        }
        self.research = CompanyResearchPersistenceTests.research_payload()

    def test_strong_overlap_selects_grounded_point_and_weak_overlap_returns_none(self):
        strong = select_company_personalization(
            self.evidence, "AI Engineer", self.research
        )
        self.assertIsNotNone(strong)
        self.assertEqual(strong.topic, "RAG")
        weak = dict(self.research)
        weak["personalization_points"] = [{
            "text": "Şirketinizin Computer vision alanındaki çalışmaları ilgimi çekti.",
            "source_url": "https://acme.example/vision",
            "source_excerpt": "We build computer vision products.",
            "topics": ["Computer vision"],
        }]
        self.assertIsNone(select_company_personalization(self.evidence, "AI Engineer", weak))

    def test_missing_research_falls_back_and_grounded_sentence_is_required(self):
        evidence_sentence = "Knowledge Assistant projemde RAG ve Python ile çalıştım."
        grounded = "Şirketinizin RAG alanındaki çalışmaları ilgimi çekti."

        class OllamaResponse:
            def __init__(self, body):
                self.body = body

            def raise_for_status(self):
                pass

            def json(self):
                return {"message": {"content": json.dumps({"subject": "AI Engineer Başvurusu", "body": self.body})}}

        fallback_body = f"Merhaba Acme Ekibi,\n\n{evidence_sentence}\n\nİyi çalışmalar,\nTest User"
        with patch("backend.services.email_generator.ensure_configured_model"), patch(
            "backend.services.email_generator.requests.post", return_value=OllamaResponse(fallback_body)
        ):
            generated = generate_email(self.profile, self.company, self.evidence, None)
        self.assertNotIn(grounded, generated.body)

        grounded_body = f"Merhaba Acme Ekibi,\n\n{evidence_sentence} {grounded}\n\nİyi çalışmalar,\nTest User"
        with patch("backend.services.email_generator.ensure_configured_model"), patch(
            "backend.services.email_generator.requests.post", return_value=OllamaResponse(grounded_body)
        ):
            generated = generate_email(self.profile, self.company, self.evidence, self.research)
        self.assertIn(grounded, generated.body)

        unsupported_body = f"Merhaba Acme Ekibi,\n\n{evidence_sentence} Acme sektör lideridir.\n\nİyi çalışmalar,\nTest User"
        with patch("backend.services.email_generator.ensure_configured_model"), patch(
            "backend.services.email_generator.requests.post", return_value=OllamaResponse(unsupported_body)
        ):
            with self.assertRaises(OllamaInvalidResponseError):
                generate_email(self.profile, self.company, self.evidence, self.research)

        extra_claim_body = f"Merhaba Acme Ekibi, şirketiniz sektör lideridir.\n\n{evidence_sentence} {grounded}\n\nİyi çalışmalar,\nTest User"
        with patch("backend.services.email_generator.ensure_configured_model"), patch(
            "backend.services.email_generator.requests.post", return_value=OllamaResponse(extra_claim_body)
        ):
            with self.assertRaises(OllamaInvalidResponseError):
                generate_email(self.profile, self.company, self.evidence, self.research)


if __name__ == "__main__":
    unittest.main()
