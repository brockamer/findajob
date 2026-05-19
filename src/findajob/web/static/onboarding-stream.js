// src/findajob/web/static/onboarding-stream.js
//
// SSE consumer for the onboarding interview chat (#740).
//
// Replaces HTMX's request/response cycle for /onboarding/interview/turn with
// a streaming fetch that consumes Server-Sent Events from
// POST /onboarding/interview/turn-stream. As the LLM emits FILE blocks over
// 60-180s, each <<<END FILE: name>>> close marker arrives as an SSE
// "captured" event and renders a transient chip in #stream-progress; the
// final "finish" event delivers the rendered assistant bubble plus the
// progress-row + finalize-block data.
//
// XSS posture: every string-interpolated value passes through escapeHtml(),
// encodeURIComponent(), or Number()-coercion. The only raw HTML accepted is
// data.assistant_html, which the server pre-renders via render_chat_assistant_html()
// — same trust contract as templates/onboarding/_turn_bubble.html's
// `{{ rendered_content | safe }}`. All HTML insertion goes through replaceHtml()
// which clears textContent first then uses insertAdjacentHTML, never raw
// innerHTML assignment.
//
// DRIFT GUARD: the bubble markup below mirrors templates/onboarding/_turn_bubble.html.
// If that template changes structure (classes, wrappers), update renderUserBubble
// and renderAssistantBubble here too.

(function () {
  function escapeHtml(s) {
    return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // Clears any existing content via textContent, then injects html via
  // insertAdjacentHTML. Callers must pass HTML built from escapeHtml-sanitized
  // inputs (or server-rendered trusted HTML for the assistant bubble).
  function replaceHtml(el, html) {
    if (!el) return;
    el.textContent = '';
    if (html) el.insertAdjacentHTML('afterbegin', html);
  }

  function clearEl(el) {
    if (el) el.textContent = '';
  }

  function renderUserBubble(text) {
    return ''
      + '<div class="rounded p-3 bg-blue-50 border border-blue-200" data-role="user">'
      +   '<div class="text-xs uppercase tracking-wide text-slate-500 mb-1">user</div>'
      +   '<div class="whitespace-pre-wrap text-sm">' + escapeHtml(text) + '</div>'
      + '</div>';
  }

  function renderAssistantBubble(safeHtml) {
    return ''
      + '<div class="rounded p-3 bg-slate-50 border border-slate-200" data-role="assistant">'
      +   '<div class="text-xs uppercase tracking-wide text-slate-500 mb-1">assistant</div>'
      +   '<div class="prose prose-sm max-w-none text-sm">' + (safeHtml || '') + '</div>'
      + '</div>';
  }

  function renderProgressRow(data) {
    var captured = Number(data.captured_count) || 0;
    var required = Number(data.required_count) || 0;
    var cost = Number(data.cumulative_cost_usd) || 0;
    var html = ''
      + '<span class="inline-flex items-center px-2 py-1 rounded bg-slate-100 border"'
      +       ' data-captured-count="' + captured + '"'
      +       ' data-required-count="' + required + '">'
      +   'Captured ' + captured + ' of ' + required + ' required blocks'
      + '</span>'
      + '<span class="inline-flex items-center px-2 py-1 rounded bg-slate-100 border"'
      +       ' data-cumulative-cost-usd="' + cost.toFixed(4) + '"'
      +       ' title="Sum of OpenRouter\'s reported per-turn cost. Matches your OpenRouter dashboard.">'
      +   '$' + cost.toFixed(2) + ' spent'
      + '</span>';
    if (data.finalize_ready) {
      html += '<span class="text-green-700 font-medium">All blocks captured — ready to finalize.</span>';
    }
    return html;
  }

  function renderFinalizeBlock(data, sessionId) {
    if (!data.finalize_ready) return '';
    var keyHint = data.keys_collected
      ? '<p class="text-xs text-green-900">Using OpenRouter key from Step 1 '
        + '(<code>***' + escapeHtml(data.openrouter_last4 || '') + '</code>).</p>'
      : '';
    return ''
      + '<h2 class="font-semibold text-green-900">Finalize onboarding</h2>'
      + '<p class="text-sm text-green-900">'
      +   'All required blocks have been emitted. Click Finalize and we\'ll verify '
      +   'your OpenRouter key and write your config files.'
      + '</p>'
      + keyHint
      + '<form action="/onboarding/interview/' + encodeURIComponent(sessionId) + '/finalize"'
      +       ' method="post" hx-boost="false" class="space-y-2">'
      +   '<button type="submit" class="px-4 py-2 bg-green-700 text-white rounded hover:bg-green-800">Finalize</button>'
      + '</form>';
  }

  function renderErrorBanner(message) {
    return ''
      + '<div class="border border-red-300 bg-red-50 text-red-900 px-4 py-3 rounded">'
      +   '<p class="font-medium">Something went wrong</p>'
      +   '<p class="text-sm mt-1 whitespace-pre-wrap">' + escapeHtml(message) + '</p>'
      +   '<p class="text-xs mt-2 text-red-700">You can keep typing to retry — the previous turn is preserved.</p>'
      + '</div>';
  }

  function appendCapturedChip(progressEl, name) {
    var chip = document.createElement('span');
    chip.className = 'inline-flex items-center px-2 py-1 rounded bg-amber-50 border border-amber-200 text-xs text-amber-900';
    chip.textContent = '📄 ' + name + ' captured';
    progressEl.appendChild(chip);
  }

  function setFormDisabled(form, disabled) {
    var controls = form.querySelectorAll('button, textarea');
    Array.prototype.forEach.call(controls, function (el) {
      el.disabled = disabled;
    });
  }

  // Parse buffered SSE bytes into {events, remaining}. Each event is delimited
  // by '\n\n'. Each event has zero or more 'event: type' and 'data: line' fields.
  function parseSseBuffer(buffer) {
    var events = [];
    var idx;
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      var raw = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      var lines = raw.split('\n');
      var eventType = 'message';
      var dataLines = [];
      for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        if (line.indexOf('event: ') === 0) {
          eventType = line.slice('event: '.length).trim();
        } else if (line.indexOf('data: ') === 0) {
          dataLines.push(line.slice('data: '.length));
        }
      }
      if (dataLines.length) {
        try {
          var data = JSON.parse(dataLines.join('\n'));
          events.push({ event: eventType, data: data });
        } catch (e) {
          // Malformed JSON — skip but don't abort the stream.
        }
      }
    }
    return { events: events, remaining: buffer };
  }

  function attachHandler(form) {
    var endpoint = form.getAttribute('data-stream-endpoint');
    if (!endpoint) return;

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var textarea = form.querySelector('textarea[name="message"]');
      var sessionInput = form.querySelector('input[name="session_id"]');
      if (!textarea || !sessionInput) return;
      var message = textarea.value;
      var sessionId = sessionInput.value;
      if (!message.trim()) return;

      var messagesEl = document.getElementById('messages');
      var streamProgressEl = document.getElementById('stream-progress');
      var progressRowEl = document.getElementById('progress-row');
      var finalizeEl = document.getElementById('finalize-block');
      var errorSlotEl = document.getElementById('error-slot');
      var thinkingEl = document.getElementById('turn-thinking');

      clearEl(streamProgressEl);
      clearEl(errorSlotEl);
      if (thinkingEl) thinkingEl.style.display = 'flex';
      setFormDisabled(form, true);

      var fd = new FormData();
      fd.append('session_id', sessionId);
      fd.append('message', message);

      fetch(endpoint, { method: 'POST', body: fd, credentials: 'same-origin' })
        .then(function (resp) {
          if (!resp.ok) {
            // Pre-stream HTTP errors (402/404/503) — body is JSON with `detail`.
            return resp.json().then(function (j) {
              throw new Error((j && j.detail) || ('HTTP ' + resp.status));
            }, function () {
              throw new Error('HTTP ' + resp.status);
            });
          }
          return consumeStream(resp.body, {
            form: form,
            textarea: textarea,
            sessionId: sessionId,
            message: message,
            messagesEl: messagesEl,
            streamProgressEl: streamProgressEl,
            progressRowEl: progressRowEl,
            finalizeEl: finalizeEl,
            errorSlotEl: errorSlotEl,
            thinkingEl: thinkingEl,
          });
        })
        .catch(function (err) {
          if (thinkingEl) thinkingEl.style.display = 'none';
          clearEl(streamProgressEl);
          replaceHtml(errorSlotEl, renderErrorBanner((err && err.message) || 'Request failed.'));
          setFormDisabled(form, false);
        });
    });
  }

  function consumeStream(body, ctx) {
    var reader = body.getReader();
    var decoder = new TextDecoder('utf-8');
    var buffer = '';
    var firstTerminalEventSeen = false;

    function pump() {
      return reader.read().then(function (result) {
        if (result.done) {
          if (!firstTerminalEventSeen) {
            if (ctx.thinkingEl) ctx.thinkingEl.style.display = 'none';
            clearEl(ctx.streamProgressEl);
            replaceHtml(ctx.errorSlotEl, renderErrorBanner('Stream ended without a final response. Please retry.'));
            setFormDisabled(ctx.form, false);
          }
          return;
        }
        buffer += decoder.decode(result.value, { stream: true });
        var parsed = parseSseBuffer(buffer);
        buffer = parsed.remaining;
        for (var i = 0; i < parsed.events.length; i++) {
          var evt = parsed.events[i];
          if (evt.event === 'captured') {
            if (ctx.streamProgressEl) appendCapturedChip(ctx.streamProgressEl, evt.data.name || '?');
          } else if (evt.event === 'finish') {
            handleFinish(evt.data, ctx);
            firstTerminalEventSeen = true;
          } else if (evt.event === 'error') {
            handleError(evt.data, ctx);
            firstTerminalEventSeen = true;
          }
        }
        return pump();
      });
    }

    return pump();
  }

  function handleFinish(data, ctx) {
    if (ctx.messagesEl) {
      ctx.messagesEl.insertAdjacentHTML('beforeend', renderUserBubble(data.user_message || ctx.message));
      ctx.messagesEl.insertAdjacentHTML('beforeend', renderAssistantBubble(data.assistant_html || ''));
      ctx.messagesEl.scrollTop = ctx.messagesEl.scrollHeight;
    }
    replaceHtml(ctx.progressRowEl, renderProgressRow(data));
    if (ctx.finalizeEl) {
      replaceHtml(ctx.finalizeEl, renderFinalizeBlock(data, ctx.sessionId));
      ctx.finalizeEl.className = data.finalize_ready
        ? 'border border-green-300 bg-green-50 rounded p-4 space-y-3 mt-4'
        : '';
    }
    clearEl(ctx.streamProgressEl);
    clearEl(ctx.errorSlotEl);
    if (ctx.thinkingEl) ctx.thinkingEl.style.display = 'none';
    ctx.textarea.value = '';
    setFormDisabled(ctx.form, false);
  }

  function handleError(data, ctx) {
    replaceHtml(ctx.errorSlotEl, renderErrorBanner(data.message || 'The turn failed.'));
    clearEl(ctx.streamProgressEl);
    if (ctx.thinkingEl) ctx.thinkingEl.style.display = 'none';
    // Keep the textarea contents so the user can edit + retry.
    setFormDisabled(ctx.form, false);
  }

  function init() {
    var forms = document.querySelectorAll('form[data-stream-endpoint]');
    Array.prototype.forEach.call(forms, attachHandler);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
