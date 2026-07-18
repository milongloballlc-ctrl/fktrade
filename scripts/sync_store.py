#!/usr/bin/env python3
"""
Stage 2 of the Amazon-to-site sync pipeline.

Pulls a fresh GET_MERCHANT_LISTINGS_ALL_DATA report via the SP-API Reports
API, compares it against the site's current product-<slug>.html pages (keyed
by the data-asin attribute Stage 1 stamps on each page), and reconciles:

  - new ASINs      -> create a product page + a catalog.html card
  - price changes  -> update data-price on the product page, its catalog.html
                       card, and its index.html card if it's featured there
  - delisted ASINs -> remove cards from catalog.html/index.html and delete
                       the product page

"Delisted" and "new" are both defined purely by ASIN presence/absence in the
fresh report — not by the report's Active/Inactive status column, since
Inactive can just mean temporarily out of stock rather than removed.

Usage:
    python3 scripts/sync_store.py [--dry-run]

--dry-run prints every planned change and writes nothing (still makes
read-only SP-API calls, since computing an accurate plan for new ASINs
requires looking up their category and description).

Credentials: same as fetch_images.py — see .env.example.

Exclusions: ASINs listed in scripts/sync_exclude.txt (one per line, '#'
comments allowed) are left completely alone — never added, price-updated, or
removed, regardless of what the report says.
"""
import argparse
import csv
import gzip
import io
import os
import re
import sys
import time
import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_images import (  # noqa: E402
    load_env, get_access_token, largest_main_image_url,
    SPAPI_ENDPOINT, MARKETPLACE_ID, USER_AGENT, REPO_ROOT, IMG_DIR,
    REQUEST_INTERVAL_SECONDS, MAX_RETRIES,
)

try:
    import requests
except ImportError:
    sys.exit("The 'requests' package is required. Install it with:\n    pip install -r requirements.txt")

CATALOG_PATH = os.path.join(REPO_ROOT, "catalog.html")
INDEX_PATH = os.path.join(REPO_ROOT, "index.html")
EXCLUDE_PATH = os.path.join(REPO_ROOT, "scripts", "sync_exclude.txt")

REPORT_TYPE = "GET_MERCHANT_LISTINGS_ALL_DATA"
REPORT_POLL_INTERVAL_SECONDS = 15
REPORT_POLL_TIMEOUT_SECONDS = 600

# Short site category labels, same six used by data-category throughout the site.
SITE_CATEGORY_FULL_NAME = {
    "Home": "Home & Kitchen",
    "Tools": "Tools & Home Improvement",
    "Pet": "Pet Supplies",
    "Toys": "Toys & Games",
    "Office": "Office Products",
    "Beauty": "Beauty",
}

# Amazon Catalog Items `productTypes[].productType` -> our short site category.
# Anything not listed here is logged as unmapped and skipped, never guessed.
PRODUCT_TYPE_TO_CATEGORY = {
    # Beauty / personal care
    "SKIN_CARE_PRODUCT": "Beauty", "FACIAL_TREATMENT": "Beauty", "FACE_POWDER": "Beauty",
    "EYE_MAKEUP": "Beauty", "LIP_MAKEUP": "Beauty", "MAKEUP": "Beauty", "MAKEUP_PRIMER": "Beauty",
    "HAIR_CARE_PRODUCT": "Beauty", "HAIR_STYLING_PRODUCT": "Beauty", "HAIR_COLOR": "Beauty",
    "TOPICAL_HAIR_REGROWTH_TREATMENT": "Beauty",
    "PERFUME": "Beauty", "DEODORANT": "Beauty", "ORAL_CARE_PRODUCT": "Beauty",
    "PERSONAL_CARE_PRODUCT": "Beauty", "NUTRITIONAL_SUPPLEMENT": "Beauty",
    "VITAMIN": "Beauty", "TOPICAL_PAIN_RELIEF": "Beauty", "BATH_PRODUCT": "Beauty",
    "SKIN_CLEANSING_AGENT": "Beauty", "SKIN_CARE_AGENT": "Beauty",
    "COSMETIC_KIT": "Beauty", "NAIL_POLISH": "Beauty",
    # Home & kitchen
    "HOME_BED_AND_BATH": "Home", "KITCHEN": "Home", "CONTAINER": "Home",
    "HOME_FURNITURE_AND_DECOR": "Home", "HOUSEWARES": "Home", "STORAGE_ORGANIZER": "Home",
    # Tools & home improvement
    "HAND_TOOLS": "Tools", "POWER_TOOL": "Tools", "HARDWARE": "Tools",
    "TOOL_ACCESSORY": "Tools", "ABRASIVE": "Tools", "MEASURING_TOOL_OR_KIT": "Tools",
    # Pet supplies
    "PET_SUPPLIES": "Pet", "ANIMAL_COLLAR_LEASH_OR_HARNESS": "Pet", "PET_BED": "Pet",
    "PET_TOY": "Pet", "ANIMAL_FOOD": "Pet",
    # Toys & games
    "TOYS_AND_GAMES": "Toys", "GAME": "Toys", "PUZZLE": "Toys", "BUILDING_BLOCKS": "Toys",
    "ACTION_FIGURE": "Toys",
    # Office products
    "OFFICE_PRODUCTS": "Office", "PAPER": "Office", "OFFICE_SUPPLY": "Office",
    "WRITING_INSTRUMENT": "Office",
}


# --------------------------------------------------------------------------
# Exclude list
# --------------------------------------------------------------------------

def load_exclude_list():
    if not os.path.exists(EXCLUDE_PATH):
        return set()
    asins = set()
    with open(EXCLUDE_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            asins.add(line)
    return asins


# --------------------------------------------------------------------------
# Reports API
# --------------------------------------------------------------------------

def request_report(session, access_token):
    headers = {
        "x-amz-access-token": access_token,
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
    }
    body = {"reportType": REPORT_TYPE, "marketplaceIds": [MARKETPLACE_ID]}

    for attempt in range(MAX_RETRIES):
        resp = session.post(
            f"{SPAPI_ENDPOINT}/reports/2021-06-30/reports",
            headers=headers, json=body, timeout=20,
        )
        if resp.status_code == 429:
            wait = 2 ** attempt
            print(f"  rate limited requesting report, backing off {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["reportId"]
    raise RuntimeError("gave up requesting report after retries (rate limited)")


def poll_report(session, access_token, report_id):
    headers = {"x-amz-access-token": access_token, "User-Agent": USER_AGENT}
    deadline = time.time() + REPORT_POLL_TIMEOUT_SECONDS
    while time.time() < deadline:
        resp = session.get(
            f"{SPAPI_ENDPOINT}/reports/2021-06-30/reports/{report_id}",
            headers=headers, timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        status = data.get("processingStatus")
        print(f"  report status: {status}")
        if status == "DONE":
            return data["reportDocumentId"]
        if status in ("CANCELLED", "FATAL"):
            raise RuntimeError(f"report processing ended with status {status}")
        time.sleep(REPORT_POLL_INTERVAL_SECONDS)
    raise RuntimeError("timed out waiting for report to finish processing")


def download_report(session, access_token, report_document_id):
    resp = session.get(
        f"{SPAPI_ENDPOINT}/reports/2021-06-30/documents/{report_document_id}",
        headers={"x-amz-access-token": access_token, "User-Agent": USER_AGENT},
        timeout=20,
    )
    resp.raise_for_status()
    doc = resp.json()
    raw = session.get(doc["url"], timeout=60).content
    if doc.get("compressionAlgorithm") == "GZIP":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8-sig")


def parse_report(text):
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    rows = []
    for row in reader:
        asin = (row.get("asin1") or "").strip()
        name = (row.get("item-name") or "").strip()
        price = (row.get("price") or "").strip()
        status = (row.get("status") or "").strip()
        sku = (row.get("seller-sku") or "").strip()
        if not asin or not name:
            continue
        rows.append({"asin": asin, "name": name, "price": price, "status": status, "sku": sku})
    return rows


def fetch_active_listings_report(session, access_token):
    print("Requesting active listings report...")
    report_id = request_report(session, access_token)
    print(f"  report requested (id {report_id}), polling until done...")
    doc_id = poll_report(session, access_token, report_id)
    print("  downloading report document...")
    text = download_report(session, access_token, doc_id)
    rows = parse_report(text)
    print(f"  {len(rows)} listing rows in report")
    return rows


# --------------------------------------------------------------------------
# Site state
# --------------------------------------------------------------------------

def load_site_products():
    """Return {asin: {slug, path, html, price, name}} for every product page
    that carries a data-asin attribute (i.e. every page Stage 1 has touched)."""
    products = {}
    for path in sorted(glob.glob(os.path.join(REPO_ROOT, "product-*.html"))):
        with open(path, encoding="utf-8") as f:
            html = f.read()
        asin_m = re.search(r'data-asin="([^"]+)"', html)
        if not asin_m:
            continue
        slug = os.path.basename(path)[len("product-"):-len(".html")]
        price_m = re.search(r'data-price="([\d.]+)"', html)
        name_m = re.search(r"<h1>(.*?)</h1>", html, re.DOTALL)
        products[asin_m.group(1)] = {
            "slug": slug,
            "path": path,
            "html": html,
            "price": price_m.group(1) if price_m else None,
            "name": re.sub(r"&amp;", "&", name_m.group(1)).strip() if name_m else slug,
        }
    return products


# --------------------------------------------------------------------------
# Plan computation
# --------------------------------------------------------------------------

def compute_plan(report_rows, site_products, excluded_asins):
    report_by_asin = {r["asin"]: r for r in report_rows if r["asin"] not in excluded_asins}
    site_asins = set(a for a in site_products if a not in excluded_asins)

    new_rows = [report_by_asin[a] for a in report_by_asin if a not in site_products]
    delisted = [(a, site_products[a]) for a in site_asins if a not in report_by_asin]

    price_changes = []
    for asin, row in report_by_asin.items():
        if asin not in site_products:
            continue
        site_price = site_products[asin]["price"]
        if site_price is None:
            continue
        try:
            if abs(float(site_price) - float(row["price"])) >= 0.01:
                price_changes.append((asin, site_products[asin], row))
        except ValueError:
            continue

    return {"new": new_rows, "delisted": delisted, "price_changes": price_changes}


# --------------------------------------------------------------------------
# Catalog Items lookups for new products
# --------------------------------------------------------------------------

def fetch_catalog_item_data(session, access_token, asin, included_data):
    url = f"{SPAPI_ENDPOINT}/catalog/2022-04-01/items/{asin}"
    params = {"marketplaceIds": MARKETPLACE_ID, "includedData": included_data}
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


def category_from_item(item):
    product_types = item.get("productTypes", [])
    seen = []
    for pt in product_types:
        name = pt.get("productType")
        seen.append(name)
        if name in PRODUCT_TYPE_TO_CATEGORY:
            return PRODUCT_TYPE_TO_CATEGORY[name], None
    return None, f"no mapping for productType(s) {seen}"


# Amazon bullet points often lead with an ALL-CAPS marketing label like
# "ADAPTS TO YOUR SHADE: " — strip that off rather than carry hype phrasing
# onto a page meant to read factual and modest (see "Tone and content rules").
_BULLET_LABEL_RE = re.compile(r"^[A-Z0-9][A-Z0-9 &/'-]{2,40}:\s*")


def _clean_bullet_text(text, limit=240):
    text = _BULLET_LABEL_RE.sub("", text.strip())
    if len(text) <= limit:
        return text.rstrip()
    # Truncate at the last sentence boundary within the limit; fall back to
    # the last whole word so we never cut off mid-word.
    truncated = text[:limit]
    for stop in (". ", "! ", "? "):
        idx = truncated.rfind(stop)
        if idx > 0:
            return truncated[: idx + 1].rstrip()
    idx = truncated.rfind(" ")
    return (truncated[:idx] if idx > 0 else truncated).rstrip().rstrip(",;:-") + "…"


def build_description(item, category_full):
    summaries = item.get("summaries", [])
    item_name = summaries[0].get("itemName") if summaries else None
    attributes = item.get("attributes", {}) or {}
    bullet_points = attributes.get("bullet_point", [])
    if bullet_points:
        text = (bullet_points[0].get("value") or "").strip()
        if text:
            return _clean_bullet_text(text)
    if item_name:
        return f"{item_name}, sourced through FKTrade LLC's supplier network."
    return f"A product from FKTrade LLC's {category_full} range."


# --------------------------------------------------------------------------
# Slug generation
# --------------------------------------------------------------------------

_SLUG_JUNK_RE = re.compile(r"[®™©|]")
_SLUG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def slugify(title, existing_slugs):
    text = _SLUG_JUNK_RE.sub(" ", title.lower())
    text = _SLUG_NON_ALNUM_RE.sub("-", text).strip("-")
    words = [w for w in text.split("-") if w]
    slug = "-".join(words[:6]) or "product"

    base, n = slug, 2
    while slug in existing_slugs:
        slug = f"{base}-{n}"
        n += 1
    return slug


# --------------------------------------------------------------------------
# Product page generation (mirrors the established page structure)
# --------------------------------------------------------------------------

PRODUCT_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="icon" type="image/svg+xml" href="favicon.svg">
<meta name="description" content="{description_attr}">
<meta property="og:type" content="website">
<meta property="og:title" content="{name} — FKTrade LLC">
<meta property="og:description" content="{description_attr}">
<meta property="og:image" content="img/{slug}.jpg">
<title>{name} — FKTrade LLC</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="styles.css">
</head>
<body>

<header>
  <div class="wrap nav">
    <a href="index.html" class="brand">FK<span>Trade</span></a>
    <ul>
      <li><a href="categories.html">Categories</a></li>
      <li><a href="catalog.html" class="active">Products</a></li>
      <li><a href="about.html">About</a></li>
      <li><a href="index.html#contact">Contact</a></li>
    </ul>
    <a href="cart.html" class="nav-cart" aria-label="Cart">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="9" cy="20" r="1.4"/><circle cx="17" cy="20" r="1.4"/><path d="M3 4h2l2.2 11.2a2 2 0 002 1.8h7.6a2 2 0 002-1.7L20 8H6"/></svg>
      <span class="cart-badge" id="cartBadge">0</span>
    </a>
  </div>
</header>

<div class="wrap breadcrumb"><a href="index.html">Home</a> / <a href="catalog.html">Catalog</a> / {name}</div>

<section class="product-detail">
  <div class="wrap">

    <div class="product-gallery">
      <img src="img/{slug}.jpg" alt="{name}">
    </div>

    <div class="product-info" data-asin="{asin}">
      <div class="prod-cat">{category_full}</div>
      <h1>{name}</h1>
      <div class="product-price-lg">${price}</div>

      <p class="product-desc">{description}</p>

      <ul class="spec-list">
        <li><span>SKU</span><span>{sku}</span></li>
        <li><span>Category</span><span>{category_full}</span></li>
      </ul>

      <div class="product-actions">
        <button type="button" class="btn-primary" data-add-to-cart data-slug="{slug}" data-name="{name}" data-price="{price}" data-image="img/{slug}.jpg">Add to Cart</button>
        <a href="catalog.html" class="btn-secondary">Back to Catalog</a>
      </div>
    </div>

  </div>
</section>

<footer id="contact">
  <div class="wrap">
    <div class="foot-grid">
      <div class="foot-brand">
        <div class="brand">FK<span>Trade</span></div>
        <p>Home goods, tools, pet supplies, toys, and office products. Open to working with new suppliers.</p>
      </div>
      <div>
        <h4>Sections</h4>
        <ul>
          <li><a href="categories.html">Categories</a></li>
          <li><a href="catalog.html">Catalog</a></li>
          <li><a href="about.html">About</a></li>
        </ul>
      </div>
      <div>
        <h4>Company</h4>
        <ul>
          <li><a href="terms.html">Terms of Service</a></li>
          <li><a href="return-policy.html">Return Policy</a></li>
          <li><a href="shipping.html">Shipping</a></li>
        </ul>
      </div>
      <div>
        <h4>Contact</h4>
        <ul>
          <li>info@fktrade.llc</li>
          <li>Mon–Fri, 9:00–18:00 EST</li>
        </ul>
      </div>
    </div>
    <div class="foot-bottom">
      <span>© 2026 FKTrade LLC</span>
      <span>Registered business in the United States</span>
      <span>Legal name: FKTrade LLC</span>
    </div>
  </div>
</footer>

<script src="js/cart.js" defer></script>

</body>
</html>
"""

CATALOG_CARD_TEMPLATE = """      <div class="prod-card" data-category="{category_short}">
        <a href="product-{slug}.html">
          <div class="prod-img"><img src="img/{slug}.jpg" alt="{name}"></div>
          <div class="prod-body">
            <div class="prod-cat">{category_short}</div>
            <div class="prod-name">{name}</div>
            <div class="prod-price">${price}</div>
          </div>
        </a>
        <button type="button" class="add-to-cart-mini" data-add-to-cart data-slug="{slug}" data-name="{name}" data-price="{price}" data-image="img/{slug}.jpg">Add to Cart</button>
      </div>
"""


def _html_escape(text):
    """Escape for both text-node and attribute-value contexts (order matters:
    '&' must be escaped first so the other entities aren't double-escaped)."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_product_page(plan_item):
    name = _html_escape(plan_item["name"])
    description = _html_escape(plan_item["description"])
    return PRODUCT_PAGE_TEMPLATE.format(
        name=name,
        slug=plan_item["slug"],
        asin=plan_item["asin"],
        category_full=plan_item["category_full"],
        price=plan_item["price"],
        description=description,
        description_attr=description,
        sku=_html_escape(plan_item["sku"] or plan_item["asin"]),
    )


def render_catalog_card(plan_item):
    return CATALOG_CARD_TEMPLATE.format(
        category_short=plan_item["category_short"],
        slug=plan_item["slug"],
        name=_html_escape(plan_item["name"]),
        price=plan_item["price"],
    )


def insert_catalog_card(html, card_html):
    marker = '<div class="prod-grid" id="prodGrid">'
    idx = html.find(marker)
    if idx == -1:
        raise RuntimeError("could not find #prodGrid in catalog.html")
    insert_at = idx + len(marker)
    return html[:insert_at] + "\n" + card_html + html[insert_at:]


def remove_card_for_slug(html, slug):
    pattern = re.compile(
        r'\s*<div class="prod-card"[^>]*>\s*'
        r'<a href="product-' + re.escape(slug) + r'\.html">.*?</a>\s*'
        r'<button[^>]*data-add-to-cart[^>]*>.*?</button>\s*'
        r'</div>',
        re.DOTALL,
    )
    new_html, n = pattern.subn("", html)
    return new_html, n > 0


def update_card_price(html, slug, new_price):
    pattern = re.compile(
        r'(<a href="product-' + re.escape(slug) + r'\.html">.*?<div class="prod-price">\$)'
        r'[\d.]+(</div>.*?data-slug="' + re.escape(slug) + r'"[^>]*data-price=")'
        r'[\d.]+(")',
        re.DOTALL,
    )
    new_html, n = pattern.subn(rf"\g<1>{new_price}\g<2>{new_price}\g<3>", html)
    return new_html, n > 0


def update_product_page_price(html, new_price):
    html = re.sub(r'(<div class="product-price-lg">\$)[\d.]+(</div>)', rf"\g<1>{new_price}\g<2>", html)
    html = re.sub(r'(data-add-to-cart[^>]*data-price=")[\d.]+(")', rf"\g<1>{new_price}\g<2>", html)
    return html


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print planned changes, write nothing")
    args = parser.parse_args()

    print("Loading credentials...")
    creds = load_env()

    excluded_asins = load_exclude_list()
    if excluded_asins:
        print(f"Excluding {len(excluded_asins)} ASIN(s) from scripts/sync_exclude.txt")

    print("Loading current site product pages...")
    site_products = load_site_products()
    print(f"  {len(site_products)} product pages carry a data-asin")

    print("Authenticating with SP-API...")
    session = requests.Session()
    access_token = get_access_token(creds)

    report_rows = fetch_active_listings_report(session, access_token)

    plan = compute_plan(report_rows, site_products, excluded_asins)

    existing_slugs = set(p["slug"] for p in site_products.values())
    new_plan_items = []
    unmapped = []

    if plan["new"]:
        print(f"\nLooking up category/description for {len(plan['new'])} new ASIN(s)...")
        for i, row in enumerate(plan["new"], start=1):
            asin = row["asin"]
            print(f"  [{i}/{len(plan['new'])}] {asin} \"{row['name'][:60]}\"...")
            try:
                item = fetch_catalog_item_data(
                    session, access_token, asin, "productTypes,summaries,attributes,images"
                )
            except Exception as e:  # noqa: BLE001
                unmapped.append((asin, row["name"], f"catalog lookup failed: {e}"))
                time.sleep(REQUEST_INTERVAL_SECONDS)
                continue

            category_short, err = category_from_item(item)
            if category_short is None:
                unmapped.append((asin, row["name"], err))
                time.sleep(REQUEST_INTERVAL_SECONDS)
                continue

            category_full = SITE_CATEGORY_FULL_NAME[category_short]
            slug = slugify(row["name"], existing_slugs)
            existing_slugs.add(slug)

            plan_item = {
                "asin": asin,
                "slug": slug,
                "name": row["name"],
                "price": row["price"],
                "sku": row["sku"],
                "category_short": category_short,
                "category_full": category_full,
                "description": build_description(item, category_full),
                "image_url": largest_main_image_url(item),
            }
            new_plan_items.append(plan_item)
            time.sleep(REQUEST_INTERVAL_SECONDS)

    # ---------------------------------------------------------------- report
    print("\n" + "=" * 60)
    print("SYNC PLAN" + (" (dry-run — nothing will be written)" if args.dry_run else ""))
    print("=" * 60)

    print(f"\nNew products: {len(new_plan_items)}")
    for item in new_plan_items:
        print(f"  + {item['asin']} \"{item['name'][:60]}\" -> product-{item['slug']}.html "
              f"[{item['category_short']}] ${item['price']}")

    print(f"\nUnmapped categories (skipped, not created): {len(unmapped)}")
    for asin, name, reason in unmapped:
        print(f"  ? {asin} \"{name[:60]}\": {reason}")

    print(f"\nPrice changes: {len(plan['price_changes'])}")
    for asin, site_item, row in plan["price_changes"]:
        print(f"  ~ {site_item['slug']} ({asin}): ${site_item['price']} -> ${row['price']}")

    print(f"\nDelisted (to remove): {len(plan['delisted'])}")
    for asin, site_item in plan["delisted"]:
        print(f"  - {site_item['slug']} ({asin})")

    if args.dry_run:
        print("\nDry run complete. No files were changed.")
        return

    # ------------------------------------------------------------- execute
    changed_files = set()

    if new_plan_items:
        print("\nFetching images and writing new product pages...")
        os.makedirs(IMG_DIR, exist_ok=True)
        with open(CATALOG_PATH, encoding="utf-8") as f:
            catalog_html = f.read()

        for item in new_plan_items:
            if item["image_url"]:
                dest = os.path.join(IMG_DIR, f"{item['slug']}.jpg")
                resp = session.get(item["image_url"], timeout=30)
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    f.write(resp.content)
                changed_files.add(os.path.relpath(dest, REPO_ROOT))
            else:
                print(f"  ! {item['slug']}: no MAIN image found, page will have a broken image")

            page_path = os.path.join(REPO_ROOT, f"product-{item['slug']}.html")
            with open(page_path, "w", encoding="utf-8") as f:
                f.write(render_product_page(item))
            changed_files.add(os.path.relpath(page_path, REPO_ROOT))

            catalog_html = insert_catalog_card(catalog_html, render_catalog_card(item))

        with open(CATALOG_PATH, "w", encoding="utf-8") as f:
            f.write(catalog_html)
        changed_files.add(os.path.relpath(CATALOG_PATH, REPO_ROOT))

    if plan["price_changes"]:
        print("\nApplying price changes...")
        with open(CATALOG_PATH, encoding="utf-8") as f:
            catalog_html = f.read()
        index_html = None
        if os.path.exists(INDEX_PATH):
            with open(INDEX_PATH, encoding="utf-8") as f:
                index_html = f.read()

        for asin, site_item, row in plan["price_changes"]:
            new_price = row["price"]
            page_html = update_product_page_price(site_item["html"], new_price)
            with open(site_item["path"], "w", encoding="utf-8") as f:
                f.write(page_html)
            changed_files.add(os.path.relpath(site_item["path"], REPO_ROOT))

            catalog_html, hit = update_card_price(catalog_html, site_item["slug"], new_price)
            if hit:
                changed_files.add(os.path.relpath(CATALOG_PATH, REPO_ROOT))

            if index_html is not None:
                index_html, hit = update_card_price(index_html, site_item["slug"], new_price)
                if hit:
                    changed_files.add(os.path.relpath(INDEX_PATH, REPO_ROOT))

        with open(CATALOG_PATH, "w", encoding="utf-8") as f:
            f.write(catalog_html)
        if index_html is not None:
            with open(INDEX_PATH, "w", encoding="utf-8") as f:
                f.write(index_html)

    if plan["delisted"]:
        print("\nRemoving delisted products...")
        with open(CATALOG_PATH, encoding="utf-8") as f:
            catalog_html = f.read()
        index_html = None
        if os.path.exists(INDEX_PATH):
            with open(INDEX_PATH, encoding="utf-8") as f:
                index_html = f.read()

        for asin, site_item in plan["delisted"]:
            catalog_html, hit = remove_card_for_slug(catalog_html, site_item["slug"])
            if hit:
                changed_files.add(os.path.relpath(CATALOG_PATH, REPO_ROOT))
            if index_html is not None:
                index_html, hit = remove_card_for_slug(index_html, site_item["slug"])
                if hit:
                    changed_files.add(os.path.relpath(INDEX_PATH, REPO_ROOT))
            os.remove(site_item["path"])
            changed_files.add(os.path.relpath(site_item["path"], REPO_ROOT))

        with open(CATALOG_PATH, "w", encoding="utf-8") as f:
            f.write(catalog_html)
        if index_html is not None:
            with open(INDEX_PATH, "w", encoding="utf-8") as f:
                f.write(index_html)

    print(f"\nDone. {len(changed_files)} file(s) changed.")


if __name__ == "__main__":
    main()
