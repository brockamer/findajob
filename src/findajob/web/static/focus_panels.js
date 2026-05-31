// #944 — focus management for board action cell-swaps (builds on #886).
//
// The board's action affordances (exclude-rule, reattribute, confirm,
// change-reason) are HTMX cell-swaps: a trigger replaces its <td> with an
// inline form panel via hx-swap="outerHTML". With no focus management, the
// keyboard caret stays on the now-removed trigger and screen readers get no
// signal that anything opened — the new form is silently elsewhere in the DOM.
//
// This handler, on htmx:afterSettle, takes the *live* swapped-in cell
// (evt.detail.elt — NOT evt.detail.target, which after an outerHTML swap is
// the detached old node) and:
//
//   • Open  — if the cell now contains a role="group" panel (#886's marker,
//     unique to these panels), move focus to the panel CONTAINER via
//     tabindex="-1". We deliberately do NOT dive to the first control: the
//     confirm modal renders its destructive "Confirm" button first, and
//     pre-arming it for a keyboard/SR user is unsafe. Focusing the labelled
//     container announces the panel's purpose and arms nothing.
//
//   • Close / re-render — if the swapped-in cell has no panel (cancel/submit
//     restored the trigger cell, or a change-reason <select> re-rendered
//     itself), and focus was actually lost to <body>, return focus to the
//     cell's first interactive control so it isn't stranded.
//
// Scope: only <td>-level swaps. Row-level actions (hx-target="closest tr")
// and the dashboard /rows refresh (<tbody>) never match, so this never
// hijacks focus on ordinary board interactions — itself a WCAG 3.2.x concern.
(function () {
  var FOCUSABLE = [
    'a[href]',
    'button:not([disabled])',
    'input:not([type="hidden"]):not([disabled])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
  ].join(', ');

  document.body.addEventListener('htmx:afterSettle', function (evt) {
    var cell = evt.detail && evt.detail.elt;
    if (!cell || cell.nodeType !== 1 || !cell.matches || !cell.matches('td')) {
      return;
    }

    var panel = cell.querySelector('[role="group"]');
    if (panel) {
      if (!panel.hasAttribute('tabindex')) {
        panel.setAttribute('tabindex', '-1');
      }
      panel.focus();
      return;
    }

    var active = document.activeElement;
    var focusLost = !active || active === document.body || !active.isConnected;
    if (focusLost) {
      var anchor = cell.querySelector(FOCUSABLE);
      if (anchor) {
        anchor.focus();
      }
    }
  });
})();
