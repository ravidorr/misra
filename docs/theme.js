/* Applies the saved theme before first paint and keeps the phone's browser bar in step with it.
   Loaded as a blocking script in <head> (no defer) so the page never flashes the wrong theme. The theme button of the
   shared header (#theme) is wired here too, so it works the same on every page. */
const STORE_KEY = 'misra-theme';
const MODES = ['auto', 'light', 'dark'];
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
  globalThis.misraTheme(localStorage.getItem(STORE_KEY));
} catch {
  /* Storage is blocked: stay on the system theme. */
}

/* Theme: auto follows the system; light and dark are forced with data-theme. */
const currentMode = () => document.documentElement.getAttribute('data-theme') ?? 'auto';

function labelButton(button) {
  const label = `Theme: ${currentMode()}`;
  button.setAttribute('aria-label', label);
  button.title = label;
}

function setTheme(mode) {
  globalThis.misraTheme(mode);
  try {
    if (mode === 'auto') localStorage.removeItem(STORE_KEY);
    else localStorage.setItem(STORE_KEY, mode);
  } catch {
    /* Storage is blocked: the theme lasts until the page closes. */
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const button = document.getElementById('theme');
  if (!button) return;
  labelButton(button);
  button.addEventListener('click', () => {
    setTheme(MODES[(MODES.indexOf(currentMode()) + 1) % MODES.length]);
    labelButton(button);
  });
});

/* Code blocks of the legal pages (an address or a command to paste elsewhere) get a Copy button. */
document.addEventListener('DOMContentLoaded', () => {
  for (const pre of document.querySelectorAll('.legal pre')) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn copy';
    button.textContent = 'Copy';
    button.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(pre.textContent.trim());
        button.textContent = 'Copied';
      } catch {
        const range = document.createRange();
        range.selectNodeContents(pre);
        getSelection().removeAllRanges();
        getSelection().addRange(range);
        button.textContent = 'Press copy';
      }
      setTimeout(() => {
        button.textContent = 'Copy';
      }, 2000);
    });
    pre.after(button);
  }
});
