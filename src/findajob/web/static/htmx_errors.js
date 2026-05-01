// Surface htmx 4xx/5xx and network errors as a transient toast.
// Without this, htmx silently no-ops on non-2xx responses, which manifests
// as buttons/dropdowns that appear to do nothing — see #361 (mobile Chrome
// stall on Applied → Not Selected). The toast lives in base.html.
document.body.addEventListener('htmx:responseError', function (evt) {
  var status = evt.detail.xhr.status;
  var path = evt.detail.requestConfig && evt.detail.requestConfig.path;
  var toast = document.getElementById('htmx-error-toast');
  if (toast) {
    toast.textContent = 'Request failed (' + status + '): ' + (path || 'unknown');
    toast.classList.remove('hidden');
    setTimeout(function () {
      toast.classList.add('hidden');
    }, 6000);
  }
  console.error('htmx response error', status, path, evt.detail.xhr.responseText);
});

document.body.addEventListener('htmx:sendError', function (evt) {
  var path = evt.detail.requestConfig && evt.detail.requestConfig.path;
  var toast = document.getElementById('htmx-error-toast');
  if (toast) {
    toast.textContent = 'Network error: ' + (path || 'unknown');
    toast.classList.remove('hidden');
    setTimeout(function () {
      toast.classList.add('hidden');
    }, 6000);
  }
  console.error('htmx send error', path);
});
