/* FKTrade cart — vanilla JS, localStorage-backed. No frameworks, no build step. */
(function () {
  var CART_KEY = 'fktrade_cart';

  function getCart() {
    try {
      var raw = localStorage.getItem(CART_KEY);
      var cart = raw ? JSON.parse(raw) : [];
      return Array.isArray(cart) ? cart : [];
    } catch (e) {
      return [];
    }
  }

  function saveCart(cart) {
    localStorage.setItem(CART_KEY, JSON.stringify(cart));
    updateCartBadge();
  }

  function addToCart(item) {
    if (!item || !item.slug || !item.name || isNaN(item.price)) return;
    var cart = getCart();
    var existing = cart.filter(function (i) { return i.slug === item.slug; })[0];
    var qty = item.qty > 0 ? item.qty : 1;
    if (existing) {
      existing.qty += qty;
    } else {
      cart.push({
        slug: item.slug,
        name: item.name,
        price: item.price,
        qty: qty,
        image: item.image || ''
      });
    }
    saveCart(cart);
  }

  function removeFromCart(slug) {
    var cart = getCart().filter(function (i) { return i.slug !== slug; });
    saveCart(cart);
  }

  function updateQty(slug, qty) {
    qty = parseInt(qty, 10);
    var cart = getCart();
    var item = cart.filter(function (i) { return i.slug === slug; })[0];
    if (!item) return;
    if (!qty || qty < 1) {
      removeFromCart(slug);
      return;
    }
    item.qty = qty;
    saveCart(cart);
  }

  function clearCart() {
    localStorage.removeItem(CART_KEY);
    updateCartBadge();
  }

  function cartCount() {
    return getCart().reduce(function (sum, i) { return sum + i.qty; }, 0);
  }

  function cartSubtotal() {
    return getCart().reduce(function (sum, i) { return sum + i.qty * i.price; }, 0);
  }

  function formatPrice(n) {
    return '$' + n.toFixed(2);
  }

  function updateCartBadge() {
    var badge = document.getElementById('cartBadge');
    if (!badge) return;
    var count = cartCount();
    badge.textContent = count;
    badge.style.display = count > 0 ? 'flex' : 'none';
  }

  function wireAddToCartButtons() {
    var buttons = document.querySelectorAll('[data-add-to-cart]');
    for (var i = 0; i < buttons.length; i++) {
      (function (btn) {
        btn.addEventListener('click', function (e) {
          e.preventDefault();
          e.stopPropagation();
          var price = parseFloat(btn.getAttribute('data-price'));
          addToCart({
            slug: btn.getAttribute('data-slug'),
            name: btn.getAttribute('data-name'),
            price: price,
            image: btn.getAttribute('data-image') || '',
            qty: 1
          });
          var original = btn.textContent;
          btn.textContent = 'Added ✓';
          btn.disabled = true;
          setTimeout(function () {
            btn.textContent = original;
            btn.disabled = false;
          }, 1200);
        });
      })(buttons[i]);
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    updateCartBadge();
    wireAddToCartButtons();
  });

  // Expose a small API for page-specific scripts (cart.html, checkout.html).
  window.FKCart = {
    getCart: getCart,
    addToCart: addToCart,
    removeFromCart: removeFromCart,
    updateQty: updateQty,
    clearCart: clearCart,
    cartCount: cartCount,
    cartSubtotal: cartSubtotal,
    formatPrice: formatPrice,
    updateCartBadge: updateCartBadge
  };
})();
