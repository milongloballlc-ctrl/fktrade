# FKTrade LLC — Supplier-Facing Showcase Site

## Purpose
Static multi-page HTML/CSS website for FKTrade LLC (Wyoming-registered US company).
Its job is to demonstrate business legitimacy to wholesale suppliers when applying
for supplier accounts. It is NOT a transactional store: no cart, no checkout, no JS
frameworks, no build step. Keep it that way unless explicitly asked otherwise.

Hosted as plain static files (GitHub Pages / Netlify). A push to the main branch
triggers automatic redeploy.

## File map
- `index.html` — home: hero, category grid, 4 featured product cards, About teaser, contact footer
- `catalog.html` — full product grid (9 cards currently), the main product listing
- `categories.html` — the 6 category descriptions
- `product.html` — product detail TEMPLATE (see "Product pages" below)
- `about.html` — company story
- `shipping.html`, `return-policy.html`, `terms.html` — policy pages, same simple text layout
- `styles.css` — the single shared stylesheet, ~190 lines

There is no separate JS file, no framework, and no templating. `catalog.html` has one
small inline `<script>` (vanilla JS, no dependencies) that powers the category filter —
see "Category filtering" below. Every page is otherwise standalone HTML.

## Design system (do not invent new styles)
Colors are CSS variables in `:root` of `styles.css`:
`--bg #FAF8F3`, `--ink #21231F`, `--ink-soft #5A5D55`, `--green #2F5D50`,
`--green-deep #1E3D34`, `--rust #C0704A`, `--line #E3DFD3`, `--card #FFFFFF`.
Headings and `.brand` use 'Fraunces' (serif), body uses 'Inter', both loaded from
Google Fonts in each page's `<head>`. Layout container is `.wrap` (max-width 1180px).
Reuse existing classes (`prod-card`, `cat-card`, `page-hero`, `spec-list`, etc.)
instead of adding new CSS when possible.

## Product card pattern (catalog.html and index.html)
```html
<div class="prod-card" data-category="Home">
  <a href="product-SLUG.html">
    <div class="prod-img"><img src="IMage_URL" alt="Short name"></div>
    <div class="prod-body">
      <div class="prod-cat">Home</div>          <!-- short category label -->
      <div class="prod-name">Product Name</div>
      <div class="prod-price">$24.99</div>
    </div>
  </a>
</div>
```
Category labels used in cards: Home, Tools, Pet, Toys, Office, Beauty.
Full category names (used on product detail pages and categories.html):
Home & Kitchen, Tools & Home Improvement, Pet Supplies, Toys & Games,
Office Products, Beauty.

The `data-category` attribute on `.prod-card` in `catalog.html` MUST always match the
short label in that card's `.prod-cat` div exactly (same string). This is what the
category filter script matches against — see "Category filtering" below.

## Category filtering (catalog.html)
The category grid on `index.html` and `categories.html` links each `.cat-card` to
`catalog.html?cat=<ShortLabel>` (e.g. `catalog.html?cat=Pet`), using the same six short
labels as `data-category`. In `catalog.html`, an inline script reads the `?cat=`
query param on load, hides any `.prod-card` whose `data-category` doesn't match, and
reveals a `#catFilterNotice` banner ("Showing category: X — Clear filter") by adding
the `is-active` class. `.cat-card` is an `<a>` (not a `<div>`) — the anchor styling
(no underline, inherits color, hover lift) lives in the `.cat-card` rules in
`styles.css`. When adding a new product card, always set `data-category` to one of
the six short labels above so it participates correctly in the filter.

## Product pages — IMPORTANT
`product.html` is a template. It currently contains a visible placeholder banner
(`.placeholder-note` with text "TEMPLATE — duplicate this file...") and bracket
placeholders like `[Replace with real product description.]`, `[SKU]`.

When creating a real product page:
1. Copy `product.html` to `product-<slug>.html` (e.g. `product-spice-organizer.html`).
2. Remove the `.placeholder-note` div entirely.
3. Fill in: `<title>`, breadcrumb, image, `.prod-cat`, `<h1>`, price, description,
   and every `<li>` in `.spec-list` with real values. No brackets may remain.
4. Point the corresponding card's `href` in `catalog.html` (and `index.html` if the
   product is featured there) to the new file.

Never leave a deployed page linking to the bare `product.html` template.

## Duplication traps — always sync these
1. Header nav and footer are copy-pasted in ALL 8 html files. Any change to the menu,
   the contact email (`info@fktrade.llc`), hours, or footer links must be applied to
   every page. After editing, grep to verify:
   `grep -l "info@fktrade.llc" *.html` should list all pages with a footer.
2. The 4 featured cards on `index.html` are duplicates of cards in `catalog.html`.
   A price or name change must be made in both files.
3. The `active` class on the nav link marks the current page; keep it correct when
   adding pages.

## Images
Current images are hotlinked from Unsplash (stock photos). This is a known weakness
for a legitimacy-facing site. When adding real products, prefer downloading images
into a local `img/` folder and referencing them relatively, keeping filenames
slug-based (`img/spice-organizer.jpg`). Do not hotlink new Unsplash URLs for real
products.

## Tone and content rules
- Language: English only on the site (audience is US suppliers).
- Voice: factual, modest, professional. No hype ("best", "amazing"), no fake reviews,
  no invented certifications or credentials. This site must survive due diligence.
- Do not invent facts about the company. If information is missing (address, phone,
  EIN, brand names), ask the owner instead of fabricating.
- Prices shown are indicative retail prices; keep the $XX.XX format.

## Workflow
- Small edits: make the change, show a summary of files touched, then commit with a
  short imperative message (e.g. "Add pet grooming glove product") and push.
- Before committing, verify all internal links resolve to existing files
  (every `href` ending in `.html` must match a file in the repo).
- The owner may write prompts in Russian; site content stays in English.
