/* Applies the saved theme before first paint and keeps the phone's browser bar in step with it.
   Loaded as a blocking script in <head> (no defer) so the page never flashes the wrong theme. */
const BAR_COLORS = { light: '#f5f0e6', dark: '#121116' };

globalThis.misraTheme = (mode) => {
  const root = document.documentElement;
  if (mode === 'light' || mode === 'dark') root.setAttribute('data-theme', mode);
  else root.removeAttribute('data-theme');
  for (const meta of document.querySelectorAll('meta[name="theme-color"]')) {
    meta.dataset.auto ??= meta.content;
    meta.content = BAR_COLORS[mode] ?? meta.dataset.auto;
  }
};

try {
  globalThis.misraTheme(localStorage.getItem('misra-theme'));
} catch {
  /* Storage is blocked: stay on the system theme. */
}
