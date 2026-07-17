// Netlify Function: creates a Stripe PaymentIntent (test mode) for the Payment
// Element embedded in checkout.html. Requires STRIPE_SECRET_KEY to be set in
// the Netlify site's environment variables — never commit a real key to this repo.
const stripe = require('stripe')(process.env.STRIPE_SECRET_KEY);

// Flat shipping rate; free above the threshold. Mirrors js/cart.js on the client,
// but is recomputed here from the server-side item total rather than trusting
// any shipping or total value the client might send.
const SHIPPING_FLAT_CENTS = 699;
const FREE_SHIPPING_THRESHOLD_CENTS = 4900;

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
  // trusted as-is — otherwise a caller could send an arbitrary amount.
  var amount = 0;
  for (var i = 0; i < items.length; i++) {
    var item = items[i] || {};
    var price = Number(item.price); // expected: integer cents
    var qty = Number(item.qty);

    if (!Number.isInteger(price) || price <= 0) {
      return { statusCode: 400, body: JSON.stringify({ error: 'Invalid price for item ' + (i + 1) + ' — must be a positive integer number of cents' }) };
    }
    if (!Number.isInteger(qty) || qty <= 0) {
      return { statusCode: 400, body: JSON.stringify({ error: 'Invalid quantity for item ' + (i + 1) + ' — must be a positive integer' }) };
    }

    amount += price * qty;
  }

  if (amount <= 0) {
    return { statusCode: 400, body: JSON.stringify({ error: 'Cart total must be greater than zero' }) };
  }

  var shipping = amount >= FREE_SHIPPING_THRESHOLD_CENTS ? 0 : SHIPPING_FLAT_CENTS;
  amount += shipping;

  try {
    var paymentIntent = await stripe.paymentIntents.create({
      amount: amount,
      currency: 'usd',
      automatic_payment_methods: { enabled: true }
    });

    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ client_secret: paymentIntent.client_secret })
    };
  } catch (err) {
    console.error('Stripe PaymentIntent creation failed:', err);
    return {
      statusCode: 500,
      body: JSON.stringify({ error: 'Unable to create payment intent' })
    };
  }
};
