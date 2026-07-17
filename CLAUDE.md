# FKTrade LLC — Supplier-Facing Showcase Site (with cart)

## Purpose
Static multi-page HTML/CSS website for FKTrade LLC (Wyoming-registered US company).
Its primary job is to demonstrate business legitimacy to wholesale suppliers when
applying for supplier accounts. It also runs a lightweight client-side cart and a
single checkout flow (see "Cart architecture" below): one form collecting contact
and US shipping details, with an embedded Stripe Payment Element for card payment,
currently wired to Stripe **test mode** (see "Stripe Payment Element (test mode)"
below). There is still no database and no build step for the site itself — cart
state lives entirely in the browser's `localStorage`; the one server-side piece is
a single Netlify Function that talks to Stripe. No JS frameworks; vanilla JS only.

Hosted as plain static files (GitHub Pages / Netlify). A push to the main branch
triggers automatic redeploy.

## File map
- `index.html` — home: hero, category grid, 4 featured product cards, About teaser, contact section
- `catalog.html` — full product grid, the main product listing, with an Add to Cart control per card
- `categories.html` — the 6 category descriptions
- `product.html` — product detail TEMPLATE (see "Product pages" below)
- `product-<slug>.html` — real product detail pages, each with an Add to Cart button
- `cart.html` — cart page: line items, quantity editor, remove, subtotal, checkout link
- `checkout.html` — order summary + single form (contact + US shipping address +
  embedded Stripe Payment Element); see "Stripe Payment Element (test mode)"
- `order-thanks.html` — order confirmation; clears the cart on load
- `thanks.html` — confirmation page for the general contact form (unrelated to orders)
- `about.html` — company story
- `shipping.html`, `return-policy.html`, `terms.html` — policy pages, same simple text layout
- `styles.css` — the single shared stylesheet
- `js/cart.js` — the cart logic, included on every page (see "Cart architecture")
- `netlify/functions/create-payment-intent.js` — Netlify Function creating a Stripe
  PaymentIntent (test mode); see "Stripe Payment Element (test mode)"
- `package.json` — declares the `stripe` npm dependency for the function above
- `netlify.toml` — tells Netlify's build where the functions directory is

There is no framework and no templating; every page is standalone HTML. `js/cart.js`
is the one shared script, included via `<script src="js/cart.js" defer></script>`
before `</body>` on every page. `catalog.html` additionally has a small inline
`<script>` for the category filter — see "Category filtering" below.

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

## Cart architecture
The cart is entirely client-side, backed by `localStorage` under the key
`fktrade_cart`: a JSON array of `{slug, name, price, qty, image}` objects
(`price` is a plain dollar float, e.g. `24.99` — matching the `$XX.XX` display
convention, NOT cents). All reads/writes go through `js/cart.js`, which exposes a
`window.FKCart` API: `getCart`, `addToCart`, `removeFromCart`, `updateQty`,
`clearCart`, `cartCount`, `cartSubtotal`, `formatPrice`, `updateCartBadge`. Don't
touch `localStorage.fktrade_cart` directly from page scripts — always go through
`FKCart`.

**Header cart icon**: every page's nav has an inline SVG cart icon
(`.nav-cart`, linking to `cart.html`) with a `#cartBadge` count span, replacing the
old "Contact Us" nav-cta button. `js/cart.js` updates the badge on
`DOMContentLoaded` on every page automatically.

**Add to Cart buttons**: any element with `data-add-to-cart` plus
`data-slug`, `data-name`, `data-price`, `data-image` attributes is auto-wired by
`js/cart.js` on `DOMContentLoaded` — clicking it adds that item (qty 1, or +1 if
already in the cart) and shows a brief "Added ✓" state. Two places use this:
- Catalog/index cards: a `.add-to-cart-mini` `<button>` as a **sibling** of the
  card's `<a>` (not nested inside it), so its click doesn't also trigger card
  navigation.
- Product detail pages: a `.btn-primary` `<button>` in `.product-actions`, using
  the `product-<slug>.html` filename's `<slug>` as `data-slug`.

When adding a new product card or product page, always carry through matching
`data-slug`/`data-name`/`data-price`/`data-image` on its add-to-cart control —
`data-slug` must equal the `product-<slug>.html` filename's slug so the same
product is deduplicated (qty increment) regardless of which page it was added from.

**cart.html / checkout.html / order-thanks.html** each have their own small inline
`<script>` (after the shared `js/cart.js` include) that renders cart contents via
`FKCart.getCart()` — there is no shared render function, since each page's markup
differs (editable rows vs. read-only summary vs. clear-on-load).

**checkout.html** has exactly one path to complete an order: a single `<form
id="paymentForm">` with contact fields (name, email, phone) and a US shipping
address, followed by an embedded Stripe Payment Element and a "Pay $&lt;total&gt;"
submit button. There is no Netlify form and no "pay later" alternative on this
page — see "Stripe Payment Element (test mode)" below for the full flow.

## Stripe Payment Element (test mode)
`checkout.html` loads `https://js.stripe.com/v3/` directly in `<head>` (per
Stripe's guidance, not deferred/self-hosted) and initializes it with a publishable
key placeholder declared at the top of the page's inline script:
```js
var STRIPE_PUBLISHABLE_KEY = "pk_test_PLACEHOLDER";
```
**This must be swapped for a real Stripe test-mode publishable key** (Dashboard →
Developers → API keys) before the payment form will work. Publishable keys are
safe to expose client-side, unlike the secret key used in the Netlify Function.

Flow, on page load (only if the cart is non-empty — otherwise the form is hidden
and an empty-cart message is shown, same as before):
1. Convert the cart to `{name, price, qty}` with `price` in **integer cents**
   (`Math.round(dollars * 100)` — the cart itself still stores dollars, per "Cart
   architecture" above) and POST `{ items: [...] }` to
   `/.netlify/functions/create-payment-intent`.
2. Use the returned `client_secret` to call `stripe.elements({ clientSecret,
   appearance })`, with the `appearance` object's `variables`/`rules` mapped to
   `styles.css`'s color tokens (`--green`, `--ink`, `--line`, etc.) and `Inter` as
   the font, so the embedded card fields match the site's look.
3. Mount the Payment Element into `#payment-element` and enable the submit button
   (`#submitPayBtn`, disabled until the element is ready).

On submit: native `required` validation runs first (`form.checkValidity()` /
`reportValidity()`); if valid, the button is disabled and shows "Processing…",
then `stripe.confirmPayment({ elements, confirmParams: { return_url:
'https://fktrade.llc/order-thanks.html', payment_method_data: { billing_details },
shipping } })` is called with the name/email/phone/address field values. On a
declined card or validation error, Stripe resolves with `result.error` and the
message is shown in `#paymentMessage` (`.payment-error`) with the button
re-enabled; on success, Stripe redirects the browser to `return_url` itself (the
`.then()` callback is not reached).

`netlify/functions/create-payment-intent.js` is the Node Netlify Function backing
the endpoint used in step 1:
- Reads the Stripe secret key from `process.env.STRIPE_SECRET_KEY` — **never commit
  a real key to this repo**. Test-mode keys start with `sk_test_`; set the real
  value only in the Netlify site's environment variables (Site settings →
  Environment variables), not in `netlify.toml` or any tracked file.
- Rejects anything but `POST`, invalid JSON, an empty/missing `items` array, and any
  item whose `price`/`qty` isn't a positive integer.
- Computes the total `amount` (integer cents) itself by summing `price * qty`
  server-side, rather than trusting a client-sent total.
- Creates a PaymentIntent with `currency: 'usd'` and `automatic_payment_methods:
  { enabled: true }`, and returns `{ client_secret: paymentIntent.client_secret }`
  as JSON on success, or a 4xx/5xx JSON error.
- **TODO before going live**: the function currently trusts the per-item price the
  client sends when computing the total. Before this leaves test mode, prices must
  be looked up server-side from a fixed product list (e.g. keyed by slug/SKU)
  instead of taking `item.price` from the request, otherwise a caller can submit
  an arbitrary amount.

`package.json` declares the `stripe` npm dependency; `netlify.toml` sets
`functions = "netlify/functions"` so Netlify's build picks up the function. There is
still no bundler/build step for the site itself — `netlify.toml`'s `publish = "."`
serves the repo root as-is.

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
5. Add the `data-add-to-cart` button in `.product-actions` with matching
   `data-slug`/`data-name`/`data-price`/`data-image` (see "Cart architecture").

Never leave a deployed page linking to the bare `product.html` template.

## Duplication traps — always sync these
1. Header nav and footer are copy-pasted in ALL html files. Any change to the menu,
   the contact email (`info@fktrade.llc`), hours, footer links, or the `.nav-cart`
   icon must be applied to every page. After editing, grep to verify:
   `grep -l "info@fktrade.llc" *.html` should list all pages with a footer, and
   `grep -L "js/cart.js" *.html` should list nothing (every page must include it).
2. The 4 featured cards on `index.html` are duplicates of cards in `catalog.html`,
   including their add-to-cart data attributes. A price or name change must be made
   in both files.
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
