# Design System

The look of the Misra dashboard, plain HTML and CSS (no framework, no Storybook). Open [index.html](./index.html) in a browser to see every token and component, with a light, dark and auto theme switch.

## Audience and intent

Misra's readers are students and early-career people in Israel (early 20s). They scan on a phone for entry-level and product roles, and want to decide in seconds. The design is built for that:

- Big type and one primary action per card (Apply). Dismissing is a quiet link.
- Loud only about what is new. Lime means new, violet means act, amber means be careful.
- Chunky borders and hard offset shadows keep it playful. Two colors keep it calm. It is a feed with a headline number, not a table with a filter bar.
- Mobile first: most readers are on a phone. Light and dark follow the phone's setting by default, with a manual switch. `theme-color` tints the browser bar in both, safe-area insets clear notches, and hover effects only apply where a hover exists.
- Readable by agents: semantic markup, `data-*` hooks, JSON-LD (`jobs.jsonld`), and WebMCP tools.

## Layout

| Path | Purpose |
| --- | --- |
| `tokens.css` | All tokens (color, type, space, radius, shadow) with light and dark values. The only file allowed to hold raw colors. |
| `components/` | One stylesheet per component. Each starts with a usage example in a comment. |
| `index.html`, `showcase.css` | The showcase page. Open it directly, no build step. |
| `logo-brief.md` | Brief for the graphic designer creating the logo and favicon. |

`build_dashboard.py` bundles `tokens.css`, `components/*.css` and `templates/dashboard.css` into `docs/dashboard.css`. The page loads it, `theme.js` and `dashboard.js` as separate files (with Subresource Integrity hashes), under a Content-Security-Policy that allows only its own origin.

## Rules

- Plain CSS. Components use tokens only: no hard-coded colors outside `tokens.css` (Stylelint enforces it).
- Class names are kebab-case: `btn`, `btn-primary`, `card-title`.
- Theme: `:root` is light. Dark applies under `prefers-color-scheme` and `<html data-theme="dark">`. `data-theme="light"` forces light.
- Text and control colors meet WCAG AA. Interactive targets are at least 44px. State is never color alone (`aria-pressed`, text labels).
- Respect `prefers-reduced-motion`.

## Logo and favicon

The mark is a violet badge with an ink pulse line and a lime sparkle: a daily "something new" signal. The wordmark is lowercase `misra` on a tilted lime highlighter swipe.

`assets/mark.svg` (also `docs/favicon.svg`) and `docs/apple-touch-icon.png` are provisional: rebuilt from the designer's concept board, not the designer's masters. Replace them with the final files from [logo-brief.md](./logo-brief.md) when they arrive. The favicon is simplified for 16px: no outer white ring, no hard shadow, thicker pulse.

## Agents (WebMCP)

The dashboard registers tools on `navigator.modelContext` when the browser supports WebMCP, and does nothing otherwise:

- `search_jobs(query, category, company, newOnly, includeDiscarded, allLocations, sort, limit)`
- `list_new_jobs(limit)`
- `get_job(id)`
- `discard_job(id)` and `restore_job(id)`
- `list_coverage_gaps()`

Without WebMCP, agents can read each role from the `data-*` attributes on `article.card`, `jobs.json`, or the schema.org `JobPosting` list in `jobs.jsonld`.

## Adding a component

1. Add `components/<name>.css` with a usage comment and tokens only.
2. Show it in `index.html`.
3. Run `npm run lint`.
4. Update `CHANGELOG.md`.
