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

`build_dashboard.py` bundles `tokens.css`, `components/*.css` and `templates/dashboard.css` into `docs/dashboard.css`. The page loads it, `theme.js`, `dashboard.js`, `auth.js`, `report.js`, `digest.js` (only while the signup switch is on) and the vendored `supabase.js` as separate files (with Subresource Integrity hashes), under a Content-Security-Policy that allows only its own origin and the Supabase project host.

The account control in the page header (`.account`) reuses `btn btn-pill`, is hidden until `auth.js` starts, and on a phone puts the signed-in email on its own row; it has no component of its own.

The "Delete my account" dialog (`components/dialog.css`, a native `<dialog>` opened with `showModal()`) is the one modal of the page: it is named with `aria-labelledby`, described with `aria-describedby`, puts the focus on the safe button ("Cancel"), ignores Esc while a request runs, and shows errors in a `role="alert"` notice. The destructive button is `btn btn-pill btn-danger` (the `--warn-bg` fill and its words, never colour alone). The header's "Delete my account" is `btn btn-pill` beside "Sign out".

The filters (`components/sheet.css`) are one native `<dialog class="sheet">` that the markup opens as a panel. On a wide screen it stays a panel in the page and "Filters" is hidden. Under 561 px wide or 500 px tall it is hidden, and `btn btn-pill filters-open` ("Filters", or "Filters (n)" with the number of filter groups that differ from the defaults; the search words do not count) opens it with `showModal()` as a bottom sheet: the browser traps focus and closes it on Esc, the heading takes the focus, and the focus returns to the button. "Close" and "Show n roles" (the number the list will hold) close it too, as does a tap on the backdrop; a resize across the breakpoint turns it back into a panel. The search is the first control of the page at every width and the level chips and "New only" stay beside it as quick filters. "Hiring now" is a `<details>` over a wrapping grid (closed on a phone, open on a wide screen), so no row scrolls sideways. A `.notice` has the border, radius and shadow of a card (no shadow inside a dialog or a sheet).

The Star button on a card is `btn btn-pill btn-star` with `aria-pressed`: its name stays "Star: ...", and the outline star becomes a filled one and the button is tinted when pressed, so the state is never colour alone. The "Starred (n)" switch above the list is a `chip chip-new`. The line about saving stars and discards (`.sync-note`) is a status region that holds a `notice` and a "Try again" button only when something needs the user's attention. The one-time offer to bring this browser's choices into the account (`.import-offer`) is a `notice` with two checkboxes and two `btn btn-pill` buttons (44px); its result is another `.sync-note` with a "Close" button.

The "Report a site" form (PLAN.md step 5.2) is a second native `dialog` opened from the coverage gap card (`btn btn-pill`, `aria-haspopup="dialog"`). Its fields are `field-group` blocks: a `label`, a `field` (a `textarea.field` for the reason), a `field-hint` and a `field-error` named in `aria-describedby`, with `aria-invalid="true"` while the error shows (the error is text in a tinted box with a dashed field border, never colour alone). Submitting with a problem puts the focus on the first field to fix; a failed send shows a `role="alert"` notice and keeps the text; a sent report moves the focus to the "Report sent" heading and "Close". `field-trap` is the honeypot: off the screen, not `display: none`, with `aria-hidden` and `tabindex="-1"` on its wrapper and input.

The "Daily email" settings (PLAN.md step 6.2, built only while `DIGEST_SIGNUP_ENABLED` is on in `build_dashboard.py`) are a third native `dialog`, opened by a `btn btn-pill` beside "Sign out" (`aria-haspopup="dialog"`). The state line is a `notice` with `role="status"` that names the state in words (not on, waiting for you to confirm, on, unsubscribed); the outcome of an action is `.digest-note` (`role="status"`, `tabindex="-1"` so the focus can move to it when the pressed button goes away) and a failure is a `role="alert"` `notice` that takes the focus. The address is a `field-group` (`field`, `field-hint`, `field-error`); daily or weekly is a `fieldset.digest-frequency` of radios (44px rows); the filters the email will use are a list, with "Use these filters for my email" as a `btn btn-pill`. While a request runs the buttons are `aria-disabled` and `aria-busy` (focus stays, a second click and Esc do nothing). Agents read `data-digest-state` and `data-digest-frequency` on the button and `data-state` on the dialog; there is no WebMCP tool for it, because turning the email on emails a person.

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

- `search_jobs(query, levels, roleTypes, regions, locations, includeRemoteNotSaid, company, newOnly, includeDiscarded, allLocations, sort, limit)`
- `list_filters()` (the values of the level, role type, region and place filters, with counts)
- `list_new_jobs(limit)`
- `get_job(id)`
- `star_job(id)`, `unstar_job(id)` and `list_starred_jobs()`; `discard_job(id)` and `restore_job(id)`. A job is starred, discarded or neither, never both, so `star_job` takes a discard away and `discard_job` a star. Each answers with `saved`: true when the choice is kept (in the browser, or in the account when the user is signed in), false when it is only held on the device until the service answers
- `list_coverage_gaps()`

There is deliberately no tool that deletes an account (that needs a person to read the warning and confirm in the dialog) and none that sends a site report (a report is an email to the maintainer, so only a person who has read the form may send it) or turns the daily email on or off (that emails a person). Agents find the report action through `data-report` hooks (`card`, `open`, `dialog`, `form`, `done`) and `data-state` on the dialog (`idle`, `sending`, `sent`).

Without WebMCP, agents can read each role from the `data-*` attributes on `article.card` (`data-starred`, `data-discarded`, `data-pending` for a choice not saved to the account yet, and `data-listed="false"` on a starred role that has closed), `jobs.json`, or the schema.org `JobPosting` list in `jobs.jsonld`.

## Adding a component

1. Add `components/<name>.css` with a usage comment and tokens only.
2. Show it in `index.html`.
3. Run `npm run lint`.
4. Update `CHANGELOG.md`.
