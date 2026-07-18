#!/usr/bin/env python3
"""
Stage 1 of the Amazon-to-site sync pipeline.

Matches existing product-<slug>.html pages to ASINs in the Amazon active
listings report (listings.txt), fetches the MAIN catalog image for each
matched ASIN via the SP-API Catalog Items API, saves it to img/<slug>.jpg,
and stamps each matched product page with a data-asin attribute.

Usage:
    python3 scripts/fetch_images.py

Credentials: see .env.example. Reads LWA_CLIENT_ID, LWA_CLIENT_SECRET, and
SP_API_REFRESH_TOKEN from a local .env file (gitignored) if present, without
overriding real environment variables — so the same script works unmodified
in CI, where those three names are set as repository secrets instead.

Matching is conservative: a product page is only linked to a listings.txt row
when the match is both strong and unambiguous. Anything else is skipped and
printed, never guessed.
"""
import difflib
import os
import re
import sys
import time
import glob

try:
    import requests
except ImportError:
    sys.exit(
        "The 'requests' package is required. Install it with:\n"
        "    pip install -r requirements.txt"
    )

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LISTINGS_PATH = os.path.join(REPO_ROOT, "listings.txt")
IMG_DIR = os.path.join(REPO_ROOT, "img")

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
SPAPI_ENDPOINT = "https://sellingpartnerapi-na.amazon.com"
MARKETPLACE_ID = "ATVPDKIKX0DER"  # Amazon.com (US)
USER_AGENT = "FKTradeSync/1.0 (Language=Python)"

# SP-API Catalog Items getCatalogItem is rate-limited; stay well under it.
REQUEST_INTERVAL_SECONDS = 0.5  # ~2 req/sec
MAX_RETRIES = 5

# A product-page name is linked to a listings.txt row only when the overlap
# score clears this floor AND beats the next-best candidate by this margin.
MATCH_MIN_SCORE = 0.75
MATCH_MIN_MARGIN = 0.15


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------

def load_env():
    """Load .env into os.environ without overriding real env vars (CI wins)."""
    env_path = os.path.join(REPO_ROOT, ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)

    required = ["LWA_CLIENT_ID", "LWA_CLIENT_SECRET", "SP_API_REFRESH_TOKEN"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        sys.exit(
            "Missing required credentials: " + ", ".join(missing) +
            "\nSet them in a local .env file (see .env.example) or as environment variables."
        )
    return {k: os.environ[k] for k in required}


def get_access_token(creds):
    resp = requests.post(
        LWA_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": creds["SP_API_REFRESH_TOKEN"],
            "client_id": creds["LWA_CLIENT_ID"],
            "client_secret": creds["LWA_CLIENT_SECRET"],
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# --------------------------------------------------------------------------
# Parsing: listings.txt and existing product pages
# --------------------------------------------------------------------------

def load_listings():
    """Parse the tab-separated Amazon active listings report."""
    with open(LISTINGS_PATH, encoding="utf-8-sig") as f:
        lines = [line.rstrip("\n") for line in f if line.strip()]

    header = lines[0].split("\t")
    idx = {name: i for i, name in enumerate(header)}
    rows = []
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) <= max(idx.get("item-name", 0), idx.get("asin1", 0)):
            continue
        name = cols[idx["item-name"]].strip()
        asin = cols[idx["asin1"]].strip() if "asin1" in idx else ""
        price = cols[idx["price"]].strip() if "price" in idx else ""
        status = cols[idx["status"]].strip() if "status" in idx else ""
        if not name or not asin:
            continue
        rows.append({"name": name, "asin": asin, "price": price, "status": status})
    return rows


def load_product_pages():
    """Parse every product-<slug>.html (excluding the product.html template)."""
    products = []
    for path in sorted(glob.glob(os.path.join(REPO_ROOT, "product-*.html"))):
        fname = os.path.basename(path)
        slug = fname[len("product-"):-len(".html")]
        with open(path, encoding="utf-8") as f:
            html = f.read()
        name_m = re.search(r"<h1>(.*?)</h1>", html, re.DOTALL)
        price_m = re.search(r'<div class="product-price-lg">\$([\d.]+)</div>', html)
        if not name_m or not price_m:
            print(f"  ! {fname}: could not read name/price, skipping")
            continue
        name = re.sub(r"&amp;", "&", name_m.group(1)).strip()
        products.append({
            "slug": slug,
            "path": path,
            "html": html,
            "name": name,
            "price": price_m.group(1),
        })
    return products


# --------------------------------------------------------------------------
# ASIN-to-slug matching
# --------------------------------------------------------------------------

_STOPWORDS = {"the", "a", "an", "for", "with", "and", "of", "to", "in", "on"}

# Pack-size / count signal (e.g. "60-Count", "180 Pcs", "6-Pack") used to break
# ties between otherwise near-identical listings for the same product family
# that differ only by quantity. Only applied when BOTH names carry a
# recognizable count, so it never affects products without this pattern.
_COUNT_PATTERN = re.compile(r"(\d+)\s*[-\s]?(?:ct|count|pcs?|pack|piece|pieces)\b", re.IGNORECASE)
_COUNT_BONUS = 0.20


def _tokens(text):
    words = re.findall(r"[a-z0-9]+", text.lower())
    return set(w for w in words if w not in _STOPWORDS)


def _extract_count(text):
    m = _COUNT_PATTERN.search(text)
    return int(m.group(1)) if m else None


def match_products_to_listings(products, listings):
    """Return (matches: {slug: listing_row}, unmatched: [(product, reason)])."""
    matches = {}
    unmatched = []

    for prod in products:
        prod_tokens = _tokens(prod["name"])
        if not prod_tokens:
            unmatched.append((prod, "product name has no usable tokens"))
            continue

        prod_count = _extract_count(prod["name"])

        scored = []
        for row in listings:
            row_tokens = _tokens(row["name"])
            if not row_tokens:
                continue
            overlap = len(prod_tokens & row_tokens) / len(prod_tokens)

            adjusted = overlap
            row_count = _extract_count(row["name"]) if prod_count is not None else None
            if prod_count is not None and row_count is not None:
                adjusted += _COUNT_BONUS if row_count == prod_count else -_COUNT_BONUS

            scored.append((adjusted, overlap, row))
        scored.sort(key=lambda x: -x[0])

        if not scored:
            unmatched.append((prod, "no listings rows to compare against"))
            continue

        best_score, best_overlap, best_row = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0

        if best_score < MATCH_MIN_SCORE:
            unmatched.append((
                prod,
                f"best candidate only {best_overlap:.0%} token overlap "
                f"(\"{best_row['name'][:70]}...\")"
            ))
            continue

        if best_score - second_score < MATCH_MIN_MARGIN:
            unmatched.append((
                prod,
                f"ambiguous — top two candidates {best_score:.0%} and {second_score:.0%} "
                f"overlap"
            ))
            continue

        if prod_count is not None and best_score != best_overlap:
            print(
                f"  i {prod['slug']}: disambiguated by pack-size count "
                f"({prod_count}) among near-identical candidates"
            )

        # Secondary sanity check: flag (but don't block on) a large price gap.
        try:
            if abs(float(prod["price"]) - float(best_row["price"])) > 1.00:
                print(
                    f"  ! {prod['slug']}: matched by name but price differs "
                    f"(site ${prod['price']} vs listing ${best_row['price']})"
                )
        except ValueError:
            pass

        matches[prod["slug"]] = best_row

    return matches, unmatched


# --------------------------------------------------------------------------
# Catalog Items API
# --------------------------------------------------------------------------

def fetch_catalog_item(session, access_token, asin):
    url = f"{SPAPI_ENDPOINT}/catalog/2022-04-01/items/{asin}"
    params = {"marketplaceIds": MARKETPLACE_ID, "includedData": "images"}
    headers = {"x-amz-access-token": access_token, "User-Agent": USER_AGENT}

    for attempt in range(MAX_RETRIES):
        resp = session.get(url, headers=headers, params=params, timeout=20)
        if resp.status_code == 429:
            wait = 2 ** attempt
            print(f"    rate limited, backing off {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()

    raise RuntimeError(f"gave up after {MAX_RETRIES} retries (rate limited)")


def largest_main_image_url(catalog_item):
    best = None
    best_area = -1
    for image_set in catalog_item.get("images", []):
        for img in image_set.get("images", []):
            if img.get("variant") != "MAIN":
                continue
            area = int(img.get("width", 0)) * int(img.get("height", 0))
            if area > best_area:
                best_area = area
                best = img.get("link")
    return best


def download_image(session, url, dest_path):
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        f.write(resp.content)


# --------------------------------------------------------------------------
# Page updates
# --------------------------------------------------------------------------

def ensure_local_image_paths(html, slug):
    """Point .product-gallery img, data-image, and og:image at img/<slug>.jpg."""
    local_path = f"img/{slug}.jpg"
    html = re.sub(
        r'(<div class="product-gallery">\s*<img src=")[^"]*(")',
        rf"\1{local_path}\2", html, count=1,
    )
    html = re.sub(
        r'(data-add-to-cart[^>]*data-image=")[^"]*(")',
        rf"\1{local_path}\2", html, count=1,
    )
    html = re.sub(
        r'(<meta property="og:image" content=")[^"]*(")',
        rf"\1{local_path}\2", html, count=1,
    )
    return html


def add_data_asin(html, asin):
    if re.search(r'<div class="product-info"[^>]*\bdata-asin=', html):
        return re.sub(r'(data-asin=")[^"]*(")', rf"\1{asin}\2", html, count=1)
    return re.sub(
        r'(<div class="product-info")(>)',
        rf'\1 data-asin="{asin}"\2', html, count=1,
    )


def update_catalog_and_index_images(slugs_touched):
    """catalog.html / index.html cards already point at img/<slug>.jpg from
    earlier work, but make that guarantee explicit and self-healing."""
    for fname in ("catalog.html", "index.html"):
        path = os.path.join(REPO_ROOT, fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            html = f.read()
        original = html
        for slug in slugs_touched:
            local_path = f"img/{slug}.jpg"
            html = re.sub(
                rf'(<a href="product-{re.escape(slug)}\.html">\s*<div class="prod-img"><img src=")[^"]*(")',
                rf"\1{local_path}\2", html,
            )
            html = re.sub(
                rf'(data-add-to-cart[^>]*data-slug="{re.escape(slug)}"[^>]*data-image=")[^"]*(")',
                rf"\1{local_path}\2", html,
            )
        if html != original:
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"  updated image paths in {fname}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    print("Loading credentials...")
    creds = load_env()

    print("Loading listings.txt and product pages...")
    listings = load_listings()
    products = load_product_pages()
    print(f"  {len(listings)} listings rows, {len(products)} product pages")

    print("Matching product pages to ASINs...")
    matches, unmatched = match_products_to_listings(products, listings)
    print(f"  {len(matches)} matched, {len(unmatched)} unmatched/ambiguous")

    if not matches:
        print("Nothing matched — nothing to fetch.")
        return

    print("Authenticating with SP-API...")
    access_token = get_access_token(creds)

    os.makedirs(IMG_DIR, exist_ok=True)
    session = requests.Session()

    fetched = []
    failed = []
    products_by_slug = {p["slug"]: p for p in products}

    for i, (slug, listing) in enumerate(matches.items(), start=1):
        asin = listing["asin"]
        print(f"[{i}/{len(matches)}] {slug} (ASIN {asin})...")
        try:
            item = fetch_catalog_item(session, access_token, asin)
            image_url = largest_main_image_url(item)
            if not image_url:
                failed.append((slug, asin, "no MAIN image in catalog response"))
                print("    no MAIN image found")
                continue

            dest = os.path.join(IMG_DIR, f"{slug}.jpg")
            download_image(session, image_url, dest)
            print(f"    saved {os.path.relpath(dest, REPO_ROOT)}")

            prod = products_by_slug[slug]
            html = prod["html"]
            html = ensure_local_image_paths(html, slug)
            html = add_data_asin(html, asin)
            with open(prod["path"], "w", encoding="utf-8") as f:
                f.write(html)

            fetched.append((slug, asin))
        except Exception as e:  # noqa: BLE001 — report and continue with the rest
            failed.append((slug, asin, str(e)))
            print(f"    FAILED: {e}")

        time.sleep(REQUEST_INTERVAL_SECONDS)

    update_catalog_and_index_images([slug for slug, _ in fetched])

    # ---------------------------------------------------------------- summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Fetched: {len(fetched)}")
    for slug, asin in fetched:
        print(f"  ✓ {slug}  ({asin})")

    print(f"\nSkipped (unmatched/ambiguous): {len(unmatched)}")
    for prod, reason in unmatched:
        print(f"  ? {prod['slug']}: {reason}")

    print(f"\nFailed: {len(failed)}")
    for slug, asin, reason in failed:
        print(f"  ✗ {slug} ({asin}): {reason}")


if __name__ == "__main__":
    main()
