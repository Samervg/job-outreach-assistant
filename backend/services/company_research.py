import json
import re
from collections import Counter
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from backend.services.company_importer import (
    CompanyImportError,
    FetchedPage,
    _domain_key,
    _extract_company_name,
    _fetch_html,
    _is_same_domain,
    normalize_url,
)


MAX_RESEARCH_PAGES = 4
MAX_TEXT_PER_PAGE = 20_000
MAX_PERSONALIZATION_POINTS = 3

PAGE_TERMS = {
    "product": 100,
    "products": 100,
    "service": 95,
    "services": 95,
    "solution": 95,
    "solutions": 95,
    "urun": 100,
    "hizmet": 95,
    "cozum": 95,
    "technology": 90,
    "teknoloji": 90,
    "platform": 85,
    "careers": 75,
    "career": 75,
    "jobs": 75,
    "kariyer": 75,
    "about": 70,
    "hakkimizda": 70,
}
PRODUCT_PAGE_TERMS = (
    "product", "service", "solution", "urun", "hizmet", "cozum", "platform"
)

TOPICS = {
    "Artificial intelligence": ("artificial intelligence", "yapay zeka"),
    "Machine learning": ("machine learning", "makine ogrenmesi"),
    "Generative AI": ("generative ai", "genai", "uretken yapay zeka"),
    "RAG": ("retrieval augmented generation", "retrieval-augmented generation", "rag"),
    "LLM": ("large language model", "large language models", "llm"),
    "Computer vision": ("computer vision", "bilgisayarli goru"),
    "Data analytics": ("data analytics", "data analysis", "veri analitigi"),
    "Cloud": ("cloud computing", "cloud platform", "bulut bilisim"),
    "Cybersecurity": ("cybersecurity", "cyber security", "siber guvenlik"),
    "Fintech": ("fintech", "financial technology"),
    "E-ticaret": ("e-commerce", "ecommerce", "e-ticaret"),
}

CAREER_TERMS = ("career", "careers", "jobs", "join us", "kariyer", "is ilan")
GENERIC_HEADINGS = {
    "home", "anasayfa", "about", "hakkimizda", "products", "urunler",
    "services", "hizmetler", "solutions", "cozumler", "careers", "kariyer",
    "contact", "iletisim",
}


class CompanyResearchError(Exception):
    pass


def _clean(value: str | None, limit: int = 500) -> str | None:
    if not value:
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned[:limit] if cleaned else None


def _visible_text(soup: BeautifulSoup) -> str:
    for element in soup(["script", "style", "noscript", "svg"]):
        element.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())[:MAX_TEXT_PER_PAGE]


def _relevant_links(soup: BeautifulSoup, base_url: str, domain: str) -> list[str]:
    scored = {}
    for link in soup.select("a[href]"):
        href = link.get("href", "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        try:
            absolute = normalize_url(urljoin(base_url, href))
        except CompanyImportError:
            continue
        if not _is_same_domain(absolute, domain) or absolute == base_url:
            continue
        label = _clean(link.get_text(" ", strip=True)) or ""
        haystack = f"{label.casefold()} {urlparse(absolute).path.casefold()}"
        scores = [score for term, score in PAGE_TERMS.items() if term in haystack]
        if scores:
            scored[absolute] = max(scored.get(absolute, 0), max(scores))
    return [url for url, _ in sorted(scored.items(), key=lambda item: (-item[1], item[0]))]


def _explicit_items(soup: BeautifulSoup, page_url: str) -> list[dict]:
    page_hint = f"{urlparse(page_url).path} {soup.title.get_text(' ') if soup.title else ''}".casefold()
    if not any(term in page_hint for term in PRODUCT_PAGE_TERMS):
        return []
    items = []
    for element in soup.select("main h1, main h2, main h3, article h1, article h2, article h3"):
        text = _clean(element.get_text(" ", strip=True), 120)
        if not text or len(text) < 3 or text.casefold() in GENERIC_HEADINGS:
            continue
        if text not in [item["text"] for item in items]:
            items.append({"text": text, "source_url": page_url})
        if len(items) >= 10:
            break
    return items


def _structured_products(soup: BeautifulSoup, page_url: str) -> list[dict]:
    found = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("@type") not in {"Product", "Service", "SoftwareApplication"}:
                continue
            name = _clean(str(entry.get("name") or ""), 120)
            if name and name not in [item["text"] for item in found]:
                found.append({"text": name, "source_url": page_url})
    return found


def _topic_matches(text: str, source_url: str) -> list[dict]:
    normalized = text.casefold()
    matches = []
    for label, aliases in TOPICS.items():
        matched = next((alias for alias in aliases if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized)), None)
        if not matched:
            continue
        position = normalized.find(matched)
        start = max(0, position - 90)
        end = min(len(text), position + len(matched) + 90)
        excerpt = _clean(text[start:end], 240) or matched
        matches.append({"label": label, "source_url": source_url, "source_excerpt": excerpt})
    return matches


def research_company_website(website: str, session: requests.Session | None = None) -> dict:
    try:
        normalized = normalize_url(website)
        domain = _domain_key(urlparse(normalized).hostname or "")
        session = session or requests.Session()
        homepage = _fetch_html(session, normalized, domain)
    except CompanyImportError as error:
        raise CompanyResearchError(str(error)) from error

    pages: list[FetchedPage] = [homepage]
    homepage_soup = BeautifulSoup(homepage.html, "html.parser")
    for url in _relevant_links(homepage_soup, homepage.url, domain)[:MAX_RESEARCH_PAGES]:
        try:
            pages.append(_fetch_html(session, url, domain))
        except CompanyImportError:
            continue

    topics_by_label = {}
    product_items = []
    hiring_signals = []
    page_texts = []
    summary = None
    summary_source = None
    for page in pages:
        soup = BeautifulSoup(page.html, "html.parser")
        structured_items = _structured_products(soup, page.url)
        text = _visible_text(soup)
        page_texts.append((page.url, text))
        for topic in _topic_matches(text, page.url):
            topics_by_label.setdefault(topic["label"], topic)
        for item in structured_items + _explicit_items(soup, page.url):
            if item["text"] not in [existing["text"] for existing in product_items]:
                product_items.append(item)
        page_hint = f"{page.url} {soup.title.get_text(' ') if soup.title else ''}".casefold()
        if any(term in page_hint for term in CAREER_TERMS):
            title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "", 160)
            if title:
                hiring_signals.append(title)
        if summary is None:
            description = soup.select_one('meta[name="description"], meta[property="og:description"]')
            candidate = _clean(description.get("content"), 400) if description else None
            if candidate:
                summary, summary_source = candidate, page.url

    topic_counts = Counter()
    for _, text in page_texts:
        lowered = text.casefold()
        for label, aliases in TOPICS.items():
            if any(re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", lowered) for alias in aliases):
                topic_counts[label] += 1
    focus_areas = [label for label, _ in sorted(topic_counts.items(), key=lambda item: (-item[1], item[0]))]

    personalization_points = []
    for label in focus_areas[:MAX_PERSONALIZATION_POINTS]:
        source = topics_by_label[label]
        personalization_points.append(
            {
                "text": f"\u015eirketinizin {label} alan\u0131ndaki \u00e7al\u0131\u015fmalar\u0131 ilgimi \u00e7ekti.",
                "source_url": source["source_url"],
                "source_excerpt": source["source_excerpt"],
                "topics": [label],
            }
        )

    if summary is None:
        for page_url, text in page_texts:
            candidate = _clean(text, 400)
            if candidate:
                summary, summary_source = candidate, page_url
                break

    return {
        "company_name": _extract_company_name(homepage_soup),
        "summary": summary,
        "summary_source_url": summary_source,
        "focus_areas": focus_areas,
        "products_or_services": product_items[:10],
        "technologies_or_topics": focus_areas,
        "hiring_signals": list(dict.fromkeys(hiring_signals))[:5],
        "personalization_points": personalization_points,
        "source_pages": [page.url for page in pages],
    }
