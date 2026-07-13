"""
Download the ~96 government form PDFs referenced in documents/gic/forms_manifest.json
(produced by fetch_gic_data.py) so they can run through the framework's existing,
unmodified pdfplumber-based PDF ingestion pipeline.

Usage:
    python scripts/gic/fetch_gic_data.py       # first, to produce forms_manifest.json
    python scripts/gic/download_gic_forms.py
"""

import json
import re
import urllib.request
import urllib.error
from pathlib import Path

GIC_DIR = Path(__file__).resolve().parents[3] / "documents" / "gic"
MANIFEST_PATH = GIC_DIR / "forms_manifest.json"
FORMS_DIR = GIC_DIR / "forms"
USER_AGENT = "Mozilla/5.0 (compatible; GIC-RAG-Ingest/1.0)"


def sanitize_filename(name: str, url: str) -> str:
    base = Path(url.split("?")[0]).name  # keep the site's own unique filename
    base = re.sub(r'[^A-Za-z0-9._-]', '_', base)
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return base


def download(url: str, dest: Path) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "pdf" not in content_type.lower():
                print(f"  SKIP (not a PDF, Content-Type={content_type}): {url}")
                return False
            data = resp.read()
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"  FAILED: {url} ({e})")
        return False

    dest.write_bytes(data)
    return True


def main():
    if not MANIFEST_PATH.exists():
        print(f"Missing {MANIFEST_PATH} - run fetch_gic_data.py first.")
        return 1

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    FORMS_DIR.mkdir(parents=True, exist_ok=True)

    updated = []
    downloaded, skipped, failed = 0, 0, 0
    for form in manifest:
        filename = sanitize_filename(form["name"], form["url"])
        dest = FORMS_DIR / filename

        if dest.exists():
            skipped += 1
        else:
            print(f"Downloading: {form['name']}")
            if download(form["url"], dest):
                downloaded += 1
            else:
                failed += 1
                continue

        updated.append({**form, "local_path": str(dest)})

    MANIFEST_PATH.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDone. Downloaded: {downloaded}, already present: {skipped}, failed: {failed}, total in manifest: {len(updated)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
