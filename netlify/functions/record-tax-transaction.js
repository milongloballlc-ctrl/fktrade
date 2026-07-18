// Netlify Function: records the Stripe Tax transaction for a succeeded
// PaymentIntent so the tax collected is included in Stripe's Tax reporting.
// Called from order-thanks.html once, right after a successful checkout
// redirect — Stripe Tax transactions should only be recorded once a sale is
// confirmed, not at calculation time.
const stripe = require('stripe')(process.env.STRIPE_SECRET_KEY);

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

  const paymentIntentId = payload.payment_intent_id;
  if (!paymentIntentId) {
    return { statusCode: 400, body: JSON.stringify({ error: 'payment_intent_id is required' }) };
  }

  try {
    var paymentIntent = await stripe.paymentIntents.retrieve(paymentIntentId);

    if (paymentIntent.status !== 'succeeded') {
      return { statusCode: 400, body: JSON.stringify({ error: 'Payment has not succeeded' }) };
    }

    var calculationId = paymentIntent.metadata && paymentIntent.metadata.tax_calculation_id;
    if (!calculationId) {
      // Nothing to record (e.g. the PaymentIntent predates the Tax integration).
      return { statusCode: 200, body: JSON.stringify({ recorded: false, reason: 'no tax calculation on this payment' }) };
    }

    await stripe.tax.transactions.createFromCalculation({
      calculation: calculationId,
      reference: paymentIntent.id,
    });

    return { statusCode: 200, body: JSON.stringify({ recorded: true }) };
  } catch (err) {
    // A page refresh on order-thanks.html would call this a second time;
    // Stripe rejects re-recording the same calculation, which we treat as
    // success rather than surfacing an error the shopper can't act on.
    var msg = (err && err.message) || '';
    if (/already/i.test(msg)) {
      return { statusCode: 200, body: JSON.stringify({ recorded: true, alreadyRecorded: true }) };
    }
    console.error('Recording Stripe Tax transaction failed:', err);
    return { statusCode: 500, body: JSON.stringify({ error: 'Unable to record tax transaction' }) };
  }
};
