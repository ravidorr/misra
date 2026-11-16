# Design system

The look of the Misra dashboard, plain HTML and CSS (no framework, no Storybook). Open [index.html](./index.html) in a browser to see every token and component, with a light, dark and auto theme switch.

## Audience and intent

Misra's readers are students and early-career people in Israel (early 20s). They scan on a phone for entry-level and product roles, and want to decide in seconds. The design is built for that:

- Big type and one primary action per card (Apply). "Not for me" is a quiet link.
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

The "Sign in" button does not start the Google redirect: it opens a native `dialog` (`.sign-in-dialog`, named "Sign in", described by its text) that holds the approved sign-in notice word for word (what signing in is for, what is stored, the Terms and privacy notice links, each a 44px target). "Continue with Google" (`btn btn-pill btn-primary`) starts the redirect and "Cancel" (or Esc) closes the dialog and returns the focus to "Sign in". The header is only the logo, "Sign in" (or the signed-in controls) and the theme button.

The "Delete my account" dialog (`components/dialog.css`, a native `<dialog>` opened with `showModal()`) is one of the page's modal dialogs: it is named with `aria-labelledby`, described with `aria-describedby`, puts the focus on the safe button ("Cancel"), ignores Esc while a request runs, and shows errors in a `role="alert"` notice. The destructive button is `btn btn-pill btn-danger` (the `--warn-bg` fill and its words, never color alone). The header's "Delete my account" is `btn btn-pill` beside "Sign out".

The filters (`components/sheet.css`) are one native `<dialog class="sheet">` that the markup opens as a panel. On a wide screen it stays a panel in the page and "Filters" is hidden. Under 561 px wide or 500 px tall it is hidden, and `btn btn-pill filters-open` ("Filters", or "Filters (n)" with the number of filters that differ from the defaults: a group of values counts once, and so do Company, Location, Sort and each switch; the search words do not count) opens it with `showModal()` as a bottom sheet: the browser traps focus and closes it on Esc, the heading takes the focus, and the focus returns to the button. "Close" and "Show n roles" (the number the list will hold) close it too, as does a tap on the backdrop; a resize across the breakpoint turns it back into a panel. The search is the first control under the headline at every width; the "Level" chips and "New only" are the first controls in the filters, so they count in "Filters (n)" like the others, and the "Starred (n)" switch (a view of its own, not a filter) stays beside "Filters" outside the sheet, so it is still there when the starred list puts the filters away. "Hiring now" is one line at every width: as many story circles as fit, then a `btn btn-pill` toggle ("Show all 15" / "Show fewer", a real button with `aria-expanded` and `aria-controls`, 44px) at the end of the line. The circles that do not fit carry `hidden` (out of the Tab order and away from screen readers) until the toggle opens a wrapping area below; the choice lasts for the page session only, and no row scrolls sideways. A `.notice` has the border, radius and shadow of a card (no shadow inside a dialog or a sheet).

The count of covered companies (the companies with status `ok` in `data/companies.json`, counted by `build_dashboard.py`) is shown twice and typed nowhere. On the first screen it is the search field's placeholder ("Search N companies", muted text from `.field::placeholder`, one line down to 320 px, no extra height) with the same fact in the field's `sr-only` label. In the footer `p.foot-coverage` (`data-companies-covered="N"`, read by agents; `covered` in `jobs.json` too) says it in a sentence with the count of companies on the coverage gap card next to it, and its "Coverage gaps" link (`a.foot-gaps`, a 44px target) opens that card. It is quiet text, not a stat tile: it must not move the search or the jobs.

The Star button on a card is `btn btn-pill btn-star` with `aria-pressed`: its name stays "Star: ...", and the outline star becomes a filled one and the button is tinted when pressed, so the state is never color alone. The "Starred (n)" switch above the list is a `chip chip-new`. The line about saving stars and discards (`.sync-note`) is a status region that holds a `notice` and a "Try again" button only when something needs the user's attention. The one-time offer to bring this browser's choices into the account (`.import-offer`) is a `notice` with two checkboxes and two `btn btn-pill` buttons (44px); its result is another `.sync-note` with a "Close" button.

The footer (`footer.foot`) is a rule, a `div.foot-info` of three muted `--text-sm` lines (the coverage sentence `p.foot-coverage`, the place the choices are saved `p.foot-note#saveNote`, the date `p.foot-updated` with `time#upd`) and the `nav.foot-links` (Privacy, Terms, Contact, 44px targets). The note and the date are written to be true in every state: the note changes when the user signs in, the date is the latest crawl. It stacks and centers on a phone, and nothing scrolls sideways; from 768px the text is left and the links are right.

The coverage gap card is `section.gaps.notice#gapsCard`: a closed `details#gaps` (summary "Coverage gaps: N companies whose roles may be missing or old", a 44px target) over two lists, and under it, outside the `details`, the "Report a site" row, so the button is in view while the list is closed. The lists are "We choose not to read these" and "We cannot read these yet" (the companies whose robots.txt does not allow it, said in plain words with the days their roles are kept); the internal reasons stay in the data and in `data-reason` on each company, not on the page. Each company is a `li.gap-card` (the border and panel of a role card, no shadow) whose name is a 44px link to the career page; the cards flow in a grid (two columns at 320 px, so about a hundred companies stay short), and the robots.txt part comes first with wider cards that carry their sentence.

The "Report a site" form (PLAN.md step 5.2) is a native `dialog` opened from the coverage gap card (`btn btn-pill`, `aria-haspopup="dialog"`). Its fields are `field-group` blocks: a `label`, a `field` (a `textarea.field` for the reason), a `field-hint` and a `field-error` named in `aria-describedby`, with `aria-invalid="true"` while the error shows (the error is text in a tinted box with a dashed field border, never color alone). Submitting with a problem puts the focus on the first field to fix; a failed send shows a `role="alert"` notice and keeps the text; a sent report moves the focus to the "Report sent" heading and "Close". `field-trap` is the honeypot: off the screen, not `display: none`, with `aria-hidden` and `tabindex="-1"` on its wrapper and input.

The "Job alerts" settings (PLAN.md step 6.2, built only while `DIGEST_SIGNUP_ENABLED` is on in `build_dashboard.py`) are a third native `dialog`, opened by a `btn btn-pill` beside "Sign out" (`aria-haspopup="dialog"`). The state line is a `notice` with `role="status"` that names the state in words (not on, waiting for you to confirm, on, unsubscribed); the outcome of an action is `.digest-note` (`role="status"`, `tabindex="-1"` so the focus can move to it when the pressed button goes away) and a failure is a `role="alert"` `notice` that takes the focus. The address is a `field-group` (`field`, `field-hint`, `field-error`); daily or weekly is a `fieldset.digest-frequency` of radios (44px rows); the filters the email will use are a list, with "Use these filters for my email" as a `btn btn-pill`. While a request runs the buttons are `aria-disabled` and `aria-busy` (focus stays, a second click and Esc do nothing). Agents read `data-digest-state` and `data-digest-frequency` on the button and `data-state` on the dialog; there is no WebMCP tool for it, because turning job alerts on emails a person.

## Rules

- Plain CSS. Components use tokens only: no hard-coded colors outside `tokens.css` (Stylelint enforces it).
- Class names are kebab-case: `btn`, `btn-primary`, `card-title`.
- Theme: `:root` is light. Dark applies under `prefers-color-scheme` and `<html data-theme="dark">`. `data-theme="light"` forces light.
- Text and control colors meet WCAG AA. Interactive targets are at least 44px. State is never color alone (`aria-pressed`, text labels).
- Respect `prefers-reduced-motion`.

## Logo and favicon

The mark is a lime badge with an ink pulse line and a violet sparkle: a daily "something new" signal. The wordmark is lowercase `misra` on a tilted lime highlighter swipe.

`assets/mark.svg` and `assets/apple-touch-icon.png` (a 180 px render of the mark on white; `build_dashboard.py` copies them to `docs/favicon.svg` and `docs/apple-touch-icon.png`) are provisional: rebuilt from the designer's concept board, not the designer's masters. Replace them with the final files from [logo-brief.md](./logo-brief.md) when they arrive. The favicon is simplified for 16px: no outer white ring, no hard shadow, thicker pulse.

## Agents (WebMCP)

The dashboard registers tools on `navigator.modelContext` when the browser supports WebMCP, and does nothing otherwise:

- `search_jobs(query, levels, roleTypes, regions, locations, includeRemoteNotSaid, company, newOnly, includeDiscarded, allLocations, sort, limit)`
- `list_filters()` (the values of the level, role type, region and place filters, with counts)
- `list_new_jobs(limit)`
- `get_job(id)`
- `star_job(id)`, `unstar_job(id)` and `list_starred_jobs()`; `discard_job(id)` and `restore_job(id)`. A job is starred, discarded or neither, never both, so `star_job` takes a discard away and `discard_job` a star. Each answers with `saved`: true when the choice is kept (in the browser, or in the account when the user is signed in), false when it is only held on the device until the service answers
- `get_sync_status()` (where the choices and filters are saved, and what the first-sign-in offer would bring over) and `import_browser_choices(filters)` (answers that offer)
- `list_coverage_gaps()`

There is deliberately no tool that deletes an account (that needs a person to read the warning and confirm in the dialog) and none that sends a site report (a report is an email to the maintainer, so only a person who has read the form may send it) or turns job alerts on or off (that emails a person). Agents find the report action through `data-report` hooks (`card`, `open`, `dialog`, `form`, `done`) and `data-state` on the dialog (`idle`, `sending`, `sent`).

Without WebMCP, agents can read each role from the `data-*` attributes on `article.card` (`data-starred`, `data-discarded`, `data-pending` for a choice not saved to the account yet, and `data-listed="false"` on a starred role that has closed), `jobs.json`, or the schema.org `JobPosting` list in `jobs.jsonld`.

## Adding a component

1. Add `components/<name>.css` with a usage comment and tokens only.
2. Show it in `index.html`.
3. Run `npm run lint`.
4. Update `CHANGELOG.md`.
