// Fade a board row out, tinted for its outcome, instead of dropping it at once (#1219).
// Without this the row vanishes the moment the response lands, and a user who picked a
// reject reason saw nothing happen.
//
// A response that removes a row names the outcome in X-Row-Outcome (see
// ROW_OUTCOME_HEADER in web/routes/board_actions.py). Here we tag the row with it and
// give the swap a delay; htmx adds `htmx-swapping` for that delay and app.css does the
// tint and the fade. Responses without the header swap as before.
(function () {
  var HEADER = 'X-Row-Outcome';
  var MIN_MS = 300;
  var MAX_MS = 1000;
  var DEFAULT_MS = 500;

  function fadeMs() {
    var reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    // No fade under reduced motion; the tint stays up for the shortest allowed time.
    if (reduced && reduced.matches) return MIN_MS;
    var raw = window.getComputedStyle(document.documentElement).getPropertyValue('--row-fade-ms');
    var ms = parseFloat(raw);
    if (isNaN(ms)) return DEFAULT_MS;
    return Math.min(MAX_MS, Math.max(MIN_MS, ms));
  }

  document.body.addEventListener('htmx:beforeSwap', function (evt) {
    var d = evt.detail;
    if (!d.shouldSwap || !d.target || d.target.tagName !== 'TR') return;
    var outcome = d.xhr.getResponseHeader(HEADER);
    if (!outcome) return;
    d.target.dataset.rowOutcome = outcome;
    d.swapOverride = 'outerHTML swap:' + fadeMs() + 'ms';
  });
})();
