"""
Fetch the small, finite set of gic.gov.lk pages that are genuinely static
server-rendered HTML (not JSON-API-driven like services/organizations/forms/
news). Confirmed live by raw HTTP fetch - these pages contain real content
directly in the HTML response (e.g. /about's Mission/Vision text sits in
plain <h2>/<p> tags), so a normal requests.get() + BeautifulSoup extraction
captures them completely. No headless browser is used anywhere in this
project.

This is intentionally a short hardcoded list, not a generic crawler - there
is no sitemap or API enumerating these pages. Checked and excluded:
  - /citizen-support/faqs: only question stubs are rendered, no answer text
    exists anywhere in the page's HTML (nothing to index).
  - Footer Privacy Policy / Terms of Service / Accessibility links are all
    href="#" placeholders - those pages don't exist yet.

Usage:
    python scripts/gic/fetch_gic_static_pages.py
"""

import json
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup
import html2text

BASE_URL = "https://gic.gov.lk"
OUT_DIR = Path(__file__).resolve().parents[3] / "documents" / "gic"
USER_AGENT = "Mozilla/5.0 (compatible; GIC-RAG-Ingest/1.0)"

STATIC_PAGES = [
    {"path": "/about", "title": "About the Government Information Centre", "category": "about"},
    {"path": "/", "title": "GIC homepage overview and contact information", "category": "general"},
]

_h2t = html2text.HTML2Text()
_h2t.body_width = 0
_h2t.ignore_images = True


def fetch_html(path: str) -> str:
    req = urllib.request.Request(f"{BASE_URL}{path}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def extract_main_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    # Note: on this site <main> wraps only the floating chat-widget button, not
    # the actual page content (confirmed by inspection) - the real content sits
    # in sibling elements directly under <body>, so we strip chrome and use body.
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "svg", "button"]):
        tag.decompose()
    root = soup.body or soup
    return _h2t.handle(str(root)).strip()


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    records = []
    for page in STATIC_PAGES:
        print(f"Fetching {page['path']} ...")
        html = fetch_html(page["path"])
        text = extract_main_text(html)
        records.append({
            "title": page["title"],
            "description": text,
            "url": f"{BASE_URL}{page['path']}",
            "category": page["category"],
        })
        print(f"  -> {len(text)} chars extracted")

    (OUT_DIR / "static_pages.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(records)} static page records written to static_pages.json")


if __name__ == "__main__":
    main()
