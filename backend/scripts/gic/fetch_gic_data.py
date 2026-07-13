"""
Fetch + normalize gic.gov.lk content into flat JSON knowledge files.

gic.gov.lk is a Next.js SPA backed by clean, unauthenticated, same-origin JSON
REST APIs (verified live, no pagination, full payload per request):
    /api/services       12 categories -> 78 subServices -> 763 leaf items
    /api/organizations  19 top-level orgs -> 669 subOrganizations
    /api/forms          13 categories -> ... -> 96 downloadable PDF forms
    /api/news           6 articles

This script flattens each nested structure into a flat JSON array of simple
English-only objects (title/summary/description/url/category), shaped for
DocumentLoader.load_and_chunk_json() (one object = one chunk). Forms are not
converted to a knowledge record here - they're written to a manifest for
download_gic_forms.py, which feeds the existing PDF pipeline instead.

Usage (no backend imports needed, safe to run standalone/repeatedly):
    python scripts/gic/fetch_gic_data.py
"""

import json
import time
import urllib.request
import urllib.error
from pathlib import Path

from bs4 import BeautifulSoup
import html2text

BASE_URL = "https://gic.gov.lk"
OUT_DIR = Path(__file__).resolve().parents[3] / "documents" / "gic"
USER_AGENT = "Mozilla/5.0 (compatible; GIC-RAG-Ingest/1.0)"

_h2t = html2text.HTML2Text()
_h2t.body_width = 0
_h2t.ignore_images = True
_h2t.ignore_emphasis = False


def clean_html(raw: str) -> str:
    """Convert a rich-HTML description field into clean Markdown-ish text."""
    if not raw or not raw.strip():
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    return _h2t.handle(str(soup)).strip()


def fetch_json(path: str, retries: int = 1):
    url = f"{BASE_URL}{path}"
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(2)
    raise RuntimeError(f"Failed to fetch {url}: {last_err}")


def _person_line(label: str, person: dict) -> str:
    """Render a person-object (minister/head/contact) as one text line, or '' if empty."""
    if not person or not isinstance(person, dict):
        return ""
    name = (person.get("name") or "").strip()
    if not name:
        return ""
    designation = (person.get("designation") or "").strip()
    phone = (person.get("phone") or "").strip()
    email = (person.get("email") or "").strip()
    parts = [name]
    if designation:
        parts.append(f"({designation})")
    line = f"{label}: {' '.join(parts)}"
    contact_bits = [b for b in (f"phone: {phone}" if phone else "", f"email: {email}" if email else "") if b]
    if contact_bits:
        line += " - " + ", ".join(contact_bits)
    return line


# ---------------------------------------------------------------------------
# Services: 12 categories -> 78 subServices -> 763 leaf items. Every level has
# its own live detail page, so each record cites the URL for its own level.
# ---------------------------------------------------------------------------

def normalize_services(data: list) -> list:
    records = []
    for cat in data:
        cat_slug = cat.get("slug", "")
        cat_title = cat.get("title", "")
        cat_url = f"{BASE_URL}/services/{cat_slug}"

        records.append({
            "title": cat_title,
            "summary": "",
            "description": clean_html(cat.get("description", "")),
            "url": cat_url,
            "category": cat_title,
            "category_type": "overview",
        })

        for sub in cat.get("subServices", []):
            sub_slug = sub.get("slug", "")
            sub_title = sub.get("title", "")
            sub_url = f"{cat_url}/{sub_slug}"

            records.append({
                "title": sub_title,
                "summary": sub.get("summary", ""),
                "description": "",
                "url": sub_url,
                "category": sub_title,
                "top_category": cat_title,
                "category_type": "overview",
            })

            for item in sub.get("items", []):
                item_slug = item.get("slug", "")
                records.append({
                    "title": item.get("title", ""),
                    "summary": item.get("summary", ""),
                    "description": clean_html(item.get("description", "")),
                    "url": f"{sub_url}/{item_slug}",
                    "category": sub_title,
                    "top_category": cat_title,
                    "category_type": "item",
                })
    return records


# ---------------------------------------------------------------------------
# Organizations: 19 top-level orgs -> 669 subOrganizations. Sub-orgs have
# their own dedicated URL nested under the parent (confirmed live), not the
# parent's URL.
# ---------------------------------------------------------------------------

def _service_offered_lines(services_offered) -> list:
    lines = []
    for svc in services_offered or []:
        if isinstance(svc, str):
            if svc.strip():
                lines.append(f"- {svc.strip()}")
        elif isinstance(svc, dict):
            name = (svc.get("name") or "").strip()
            desc = clean_html(svc.get("description") or "")
            if not name and not desc:
                continue
            line = f"- {name}" if name else "-"
            if desc:
                line += f": {desc}"
            for doc in svc.get("docs") or []:
                if isinstance(doc, dict) and doc.get("url"):
                    line += f" (see: {doc.get('title', 'document')} - {doc['url']})"
            for lnk in svc.get("links") or []:
                if isinstance(lnk, dict) and lnk.get("url"):
                    line += f" (see: {lnk.get('title', 'link')} - {lnk['url']})"
            lines.append(line)
    return lines


def normalize_organizations(data: list) -> list:
    records = []
    for org in data:
        org_id = org.get("id", "")
        org_url = f"{BASE_URL}/organizations/{org_id}"
        records.append({
            "title": org.get("name", ""),
            "description": clean_html(org.get("description", "")),
            "url": org_url,
            "category": org.get("name", ""),
        })

        for sub in org.get("subOrganizations", []):
            sub_id = sub.get("id", "")
            sub_url = f"{org_url}/{sub_id}"

            desc_parts = [clean_html(sub.get("description", ""))]

            offered_lines = _service_offered_lines(sub.get("servicesOffered"))
            if not offered_lines and sub.get("servicesOfferedStr"):
                offered_lines = [f"- {sub['servicesOfferedStr']}"]
            if offered_lines:
                desc_parts.append("\n\nServices offered:\n" + "\n".join(offered_lines))

            for label, key in (("Minister", "minister"), ("Deputy Minister", "deputyMinister"),
                                ("Contact person", "contactPerson")):
                line = _person_line(label, sub.get(key))
                if line:
                    desc_parts.append(line)

            key_personnel = sub.get("keyPersonnel")
            if isinstance(key_personnel, list):
                for kp in key_personnel:
                    line = _person_line("Key personnel", kp)
                    if line:
                        desc_parts.append(line)

            other_details = sub.get("otherOfficeDetails")
            if isinstance(other_details, str) and other_details.strip():
                desc_parts.append(f"Other details: {other_details.strip()}")

            head_line = _person_line("Head of organization", sub.get("headOfOrganization"))
            if head_line:
                desc_parts.append(head_line)

            records.append({
                "title": sub.get("name", ""),
                "description": "\n\n".join(p for p in desc_parts if p),
                "phone": sub.get("phone", "") or "",
                "email": sub.get("email", "") or "",
                "address": sub.get("address", "") or "",
                "fax": sub.get("fax", "") or "",
                "website": sub.get("website", "") or "",
                "url": sub_url,
                "category": org.get("name", ""),
            })
    return records


# ---------------------------------------------------------------------------
# News: flat array, 6 articles.
# ---------------------------------------------------------------------------

def normalize_news(data: list) -> list:
    records = []
    for item in data:
        records.append({
            "title": item.get("title_en", ""),
            "summary": item.get("excerpt_en", ""),
            "description": item.get("content_en", ""),
            "url": f"{BASE_URL}/news/{item.get('id', '')}",
            "category": item.get("category", ""),
        })
    return records


# ---------------------------------------------------------------------------
# Forms: not a knowledge record - a manifest of PDFs for download_gic_forms.py
# ---------------------------------------------------------------------------

def normalize_forms_manifest(data: list) -> list:
    manifest = []
    for cat in data:
        cat_name = cat.get("name", "")
        for sub in cat.get("subCategories", []):
            for svc in sub.get("services", []):
                for form in svc.get("forms", []):
                    link = form.get("link", "")
                    if not link:
                        continue
                    url = link if link.startswith("http") else f"{BASE_URL}{link}"
                    manifest.append({
                        "name": form.get("name", ""),
                        "url": url,
                        "category": cat_name,
                    })
    return manifest


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Fetching /api/services ...")
    services = normalize_services(fetch_json("/api/services"))
    (OUT_DIR / "services.json").write_text(json.dumps(services, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {len(services)} records written to services.json")

    print("Fetching /api/organizations ...")
    orgs = normalize_organizations(fetch_json("/api/organizations"))
    (OUT_DIR / "organizations.json").write_text(json.dumps(orgs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {len(orgs)} records written to organizations.json")

    print("Fetching /api/news ...")
    news = normalize_news(fetch_json("/api/news"))
    (OUT_DIR / "news.json").write_text(json.dumps(news, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {len(news)} records written to news.json")

    print("Fetching /api/forms ...")
    forms_manifest = normalize_forms_manifest(fetch_json("/api/forms"))
    (OUT_DIR / "forms_manifest.json").write_text(json.dumps(forms_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {len(forms_manifest)} PDF forms written to forms_manifest.json")

    print("\nDone. Next: python scripts/gic/download_gic_forms.py")


if __name__ == "__main__":
    main()
