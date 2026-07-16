// Netlify Function: creates a Stripe Checkout Session (test mode) for the cart
// posted from checkout.html. Requires STRIPE_SECRET_KEY to be set in the Netlify
// site's environment variables — never commit a real key to this repo.
const stripe = require('stripe')(process.env.STRIPE_SECRET_KEY);

const SUCCESS_URL = 'https://fktrade.llc/order-thanks.html';
const CANCEL_URL = 'https://fktrade.llc/cart.html';

exports.handler = async function (event) {
  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, body: JSON.stringify({ error: 'Method not allowed' }) };
  }

  let payload;
  try {
    payload = JSON.parse(event.body || '{}');
  } catch (e) {
    return { statusCode: 400, body: JSON.stringify({ error: 'Invalid JSON body' }) };
  }

  const items = payload.items;

  if (!Array.isArray(items) || items.length === 0) {
    return { statusCode: 400, body: JSON.stringify({ error: 'Cart is empty' }) };
  }

  // TODO before going live: prices here come from the client and must be
  // re-validated against a server-side product list (id/price lookup), not
  // trusted as-is — otherwise a caller could send an arbitrary price.
  var line_items = [];
  for (var i = 0; i < items.length; i++) {
    var item = items[i] || {};
    var name = typeof item.name === 'string' ? item.name.trim() : '';
    var price = Number(item.price); // expected: integer cents
    var qty = Number(item.qty);

    if (!name) {
      return { statusCode: 400, body: JSON.stringify({ error: 'Each cart item requires a name' }) };
    }
    if (!Number.isInteger(price) || price <= 0) {
      return { statusCode: 400, body: JSON.stringify({ error: 'Invalid price for "' + name + '" — must be a positive integer number of cents' }) };
    }
    if (!Number.isInteger(qty) || qty <= 0) {
      return { statusCode: 400, body: JSON.stringify({ error: 'Invalid quantity for "' + name + '" — must be a positive integer' }) };
    }

    line_items.push({
      price_data: {
        currency: 'usd',
        product_data: { name: name },
        unit_amount: price
      },
      quantity: qty
    });
  }

  try {
    var session = await stripe.checkout.sessions.create({
      mode: 'payment',
      line_items: line_items,
      success_url: SUCCESS_URL,
      cancel_url: CANCEL_URL
    });

    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: session.url })
    };
  } catch (err) {
    console.error('Stripe Checkout Session creation failed:', err);
    return {
      statusCode: 500,
      body: JSON.stringify({ error: 'Unable to create checkout session' })
    };
  }
};
