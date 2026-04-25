// src/findajob/web/static/filters.js
//
// Popover Apply/Cancel/Clear handlers + clipboard-write for Copy-link.
//
// The header partial (_table_header.html) renders ENUM/DATE popovers as
// Alpine.js components for open/close. Apply/Clear/Cancel are wired here so
// they (1) write committed values back into the hidden inputs that HTMX
// includes in /rows requests and (2) trigger an htmx event so the URL +
// table refresh.

(function () {
  function $$(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  function commitEnumPopover(name) {
    var hidden = document.querySelector('input[type=hidden][data-popover-target="' + name + '"]');
    if (!hidden) return;
    var checked = $$('input[type=checkbox][data-popover-checkbox="' + name + '"]:checked');
    hidden.value = checked.map(function (cb) { return cb.value; }).join(',');
    htmx.trigger(hidden, 'change');
  }

  function clearEnumPopover(name) {
    var hidden = document.querySelector('input[type=hidden][data-popover-target="' + name + '"]');
    if (!hidden) return;
    hidden.value = '';
    $$('input[type=checkbox][data-popover-checkbox="' + name + '"]').forEach(function (cb) {
      cb.checked = false;
    });
    htmx.trigger(hidden, 'change');
  }

  function commitDatePopover(name) {
    var hiddenFrom = document.querySelector('input[type=hidden][data-popover-target="' + name + '_from"]');
    var hiddenTo = document.querySelector('input[type=hidden][data-popover-target="' + name + '_to"]');
    var dateFrom = document.querySelector('input[data-popover-date-from="' + name + '"]');
    var dateTo = document.querySelector('input[data-popover-date-to="' + name + '"]');
    if (hiddenFrom) hiddenFrom.value = dateFrom ? dateFrom.value : '';
    if (hiddenTo) hiddenTo.value = dateTo ? dateTo.value : '';
    if (hiddenFrom) htmx.trigger(hiddenFrom, 'change');
  }

  function clearDatePopover(name) {
    var hiddenFrom = document.querySelector('input[type=hidden][data-popover-target="' + name + '_from"]');
    var hiddenTo = document.querySelector('input[type=hidden][data-popover-target="' + name + '_to"]');
    var dateFrom = document.querySelector('input[data-popover-date-from="' + name + '"]');
    var dateTo = document.querySelector('input[data-popover-date-to="' + name + '"]');
    if (hiddenFrom) hiddenFrom.value = '';
    if (hiddenTo) hiddenTo.value = '';
    if (dateFrom) dateFrom.value = '';
    if (dateTo) dateTo.value = '';
    if (hiddenFrom) htmx.trigger(hiddenFrom, 'change');
  }

  document.addEventListener('click', function (e) {
    var t = e.target.closest('[data-popover-apply], [data-popover-clear], [data-copy-link]');
    if (!t) return;

    if (t.dataset.popoverApply) {
      var name = t.dataset.popoverApply;
      // Date popovers have separate from/to hidden inputs; ENUM has one.
      if (document.querySelector('input[data-popover-date-from="' + name + '"]')) {
        commitDatePopover(name);
      } else {
        commitEnumPopover(name);
      }
      return;
    }
    if (t.dataset.popoverClear) {
      var name2 = t.dataset.popoverClear;
      if (document.querySelector('input[data-popover-date-from="' + name2 + '"]')) {
        clearDatePopover(name2);
      } else {
        clearEnumPopover(name2);
      }
      return;
    }
    if (t.hasAttribute('data-copy-link')) {
      var label = t.querySelector('[data-copy-link-label]');
      var original = label ? label.textContent : null;
      function flash(msg) {
        if (label) {
          label.textContent = msg;
          setTimeout(function () { label.textContent = original; }, 1500);
        }
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(window.location.href).then(
          function () { flash('Copied!'); },
          function () { flash('Copy failed'); }
        );
      } else {
        flash('Clipboard unavailable');
      }
    }
  });
})();
