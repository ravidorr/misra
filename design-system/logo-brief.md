# Misra logo and favicon brief

For the graphic designer. Misra is a small, independent product. This brief covers who it is for, how it looks today, and exactly what to deliver.

## 1. What Misra is

Misra is a daily list of entry-level, student and product roles at companies in Israel. Every morning it checks company career pages and shows what is open and what is new. People open it on their phone, scroll, tap Apply or move on.

No account is needed. Signing in is optional: it saves choices across devices and lets people turn on job alerts. It is a quick, useful habit, like checking the weather.

Open questions for the maintainer, to settle before you start:

- What does the name "Misra" mean, and how is it pronounced? Use it if it helps the idea.
- Should there also be a Hebrew version of the wordmark? Optional, design it only if asked.

## 2. Who it is for

- Students and early-career people in Israel, born after 2000, in their early 20s.
- Almost all of them use a phone. Many use dark mode.
- They scan fast. They want it snappy, not corporate, and not overwhelming.
- They are looking for their first role, so the mood should be confident and encouraging, never stressful.

## 3. Brand personality

Fast. Direct. Playful but calm. Confident. A little cheeky, never childish.

It should feel like a tool made by someone their age, not like a recruitment agency or a bank. Think sticker on a laptop, not suit and tie.

Not: corporate, formal, "AI", sleek-luxury, cute mascot.

## 4. Look and feel today

The interface already has a visual language. The logo should belong to it, not fight it.

- Chunky 2px borders and hard offset shadows (no blur). A "sticker" feel.
- Rounded shapes: 14px and 20px corners, and pills. Stickers tilt about 3 degrees.
- Big, heavy, tightly spaced lowercase type for headlines. The word "misra" is written in lowercase in the app.
- A lime "highlighter" block behind the key word of the headline.
- Two colors do the work: violet to act, lime for new.

The live design system is in `design-system/index.html` (open it in a browser, switch light and dark).

## 5. Colors

Use these exact values. Raw colors live in `design-system/tokens.css`.

| Role | Light | Dark | Notes |
| --- | --- | --- | --- |
| Ink | `#111111` | `#F5F0E6` | Text, borders, shadows |
| Background | `#F5F0E6` | `#121116` | Warm cream and near-black |
| Panel | `#FFFFFF` | `#1D1B24` | Cards |
| Violet (action) | `#6C47FF` | `#A58FFF` (text), `#6C47FF` (fills) | Buttons, links |
| Violet tint | `#E6DEFF` | `#2B2447` | Tags |
| Lime (new) | `#D4FF4F` | `#D4FF4F` | Means "new". Always paired with `#111111` text |
| Amber (careful) | `#FFE3B3` | `#4A3512` | Warnings only |

Rules:

- Lime is reserved for "new" in the interface. The current mark is a lime disc with a violet spark (the sparkle marks the daily "something new").
- Never put white text on lime, or lime text on white.
- Keep the mark to two colors plus ink. A one-color version is required.
- The mark must work on cream, white, near-black and violet backgrounds.

## 6. Typography

The app uses the system UI font (San Francisco on iPhone, Roboto on Android, Segoe UI on Windows), set at weight 800 with tracking of about -0.04em for headlines.

For the wordmark you may draw custom lettering or pick a typeface. If you choose a typeface, tell us the name and license, and prefer one with a free or low-cost web license. Direction:

- Lowercase.
- Heavy and geometric or grotesque, with tight spacing.
- Friendly terminals. Open counters that stay readable when small.
- Examples to react to (not mandatory): Inter Tight ExtraBold, Space Grotesk Bold, Satoshi Black, General Sans Bold.

If the logo uses a font that is not a system font, the app will use the exported SVG wordmark and will not load the font.

## 7. What to design

1. A **mark** (symbol only) that works as the favicon and app icon.
2. A **wordmark** ("misra").
3. A **lockup** of mark and wordmark, horizontal. A stacked version is optional.

The mark is the priority. It will be seen most at 16 to 32 pixels in a browser tab, next to dozens of other tabs.

### Concept directions to explore

Pick two or three and show variations. You are welcome to propose a better idea.

- **The drop.** A daily drop of fresh roles: a dot, drop or pulse that stands for "something new today".
- **The highlighter.** The lime highlighter swipe behind a letter, as in the headline.
- **The sticker.** A bold lowercase "m" or a simple shape in sticker form, with the hard shadow.
- **The signal.** Something appearing at the top of a list: one new item above the rest.

### What to avoid

- A letter "M" with a dot or circle on top or beside it. A first attempt looked like a human figure and was rejected. Check every concept for accidental figures, faces and suggestive shapes at all sizes and rotations.
- Briefcases, magnifying glasses, radar sweeps, arrows, handshakes, ladders, rockets, resumes.
- Gradients, glows, glass, 3D, sparkles, "AI" imagery, circuit patterns.
- Flags, Stars of David, and other national or religious symbols.
- Mascots and characters.
- Fine detail that disappears below 32px.

## 8. Technical requirements

- Design on a 64 by 64 grid. Keep geometry simple and shapes closed.
- Test at 16px, 32px and 180px. The mark must be recognizable at 16px.
- Provide a version that reads on both light and dark browser tabs.
- Maskable icon: keep the important part inside the central 80% circle.
- Text in the mark, if any, must be outlined.
- Check it in grayscale and in one color.

## 9. Deliverables

Send these files:

| File | Use |
| --- | --- |
| `mark.svg`, `wordmark.svg`, `logo-horizontal.svg` | Master vector files, full color on a transparent background |
| `*-black.svg`, `*-white.svg` | One-color versions |
| `favicon.svg` | Browser tab icon. May include a `prefers-color-scheme` rule for light and dark |
| `favicon.ico` | 16, 32 and 48 px in one file, for older browsers |
| `apple-touch-icon.png` | 180 by 180, opaque background, no transparency |
| `icon-192.png`, `icon-512.png` | App icons |
| `icon-maskable-512.png` | Maskable app icon |
| `social-1200x630.png` | Link preview image: logo, name, one line of copy |
| `email-header.png` | 2x export, about 600 px wide, for the job alerts email header (email clients do not show SVG) |
| `avatar-512.png` | GitHub and social profile picture |
| Source file | Figma, Illustrator or Affinity, with fonts outlined or listed |
| One-page guide | Clear space, minimum size, color use, and 3 or 4 "do not" examples |

Export SVGs with plain paths and no embedded fonts, filters or masks.

## 10. Tagline and tone

Working tagline: **Scan fast. Decide faster.** Open to better. The voice is plain, direct and friendly. No emoji and no exclamation marks.

## 11. Process

1. Two or three directions in rough form, shown at 16, 32 and 180px on cream, white and near-black.
2. One round of feedback, then a refined direction.
3. Final files as in section 9.

## 12. Checklist before sending

- Legible at 16px on a light tab and a dark tab.
- No accidental figure, face or other unwanted shape in any rotation.
- Works in one color.
- Fits the sticker look: heavy, rounded, simple.
- Colors match section 5 exactly.
- Files match section 9.
