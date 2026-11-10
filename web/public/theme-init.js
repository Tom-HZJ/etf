// Applies the persisted theme before first paint. Served from /public as an
// external file so the production CSP can stay `script-src 'self'`.
(function () {
  try {
    var saved = localStorage.getItem('etf.theme');
    if (saved === 'light' || saved === 'dark') {
      document.documentElement.setAttribute('data-theme', saved);
    }
  } catch (error) {
    /* storage unavailable: keep the default dark theme */
  }
})();
