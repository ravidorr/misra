# PRD: Accounts, personalisation and reach

Status: draft for discussion, 2026-11-14. Nothing here is built yet. Once agreed, it is broken into a step-by-step plan
and the steps are tracked in `TODO.md`.

## Background

Misra crawls career pages daily, emails a digest of new jobs and publishes a dashboard. The first users have been using
it and they loved it. Their feedback points at eight gaps:

1. A user's "Not for me" choices live only in one browser, so they cannot move between a computer and a phone, and
   there is no way to mark a job as interesting.
2. "Israel" is too broad a location filter: someone in Tel Aviv does not want Jerusalem, and someone in Beersheba does
   not want Haifa.
3. The web page is not the only way people want to use the data: some want to pull jobs into their own AI assistant, to
   apply or to draft a cover letter.
4. A card shows too little: a short sentence about the role would help.
5. The coverage gap card leaves out many sites we already know we do not handle.
6. Users cannot tell us about a site we do not cover.
7. The digest email is the same for everyone, with no personalisation.
8. Level (Intern, Junior) and field (Product) are blended into one filter, and Product is the only field recognised, so
   people looking in other fields get little from the dashboard or the email.

## Goals

- A user signs in once and their stars, "Not for me" choices, filters and email preferences follow them across devices.
- Jobs can be narrowed to the regions a person is willing to work in.
- Level and field (role type) are separate choices, and more fields than Product are recognised.
- Each card tells a person enough to decide whether to open the posting.
- The same jobs are usable from an AI assistant, not only from the web page.
- The coverage gap card is complete and honest, and users can report missing sites.
- Every daily email is personalised and opt-in.
- Running cost stays at zero (free tiers only) at the expected scale of tens of users.

## Non-goals

- Username and password accounts. Sign-in is Google only, so we never store passwords.
- Paid infrastructure.
- Applying to jobs on a user's behalf, or storing cover letters.
- Letting users add a site to the crawl directly (they can report one; see FR-7).

## Users

- Job seekers in Israel, mostly early-career (intern, junior), who use both a laptop and a phone.
- Almost all of them already use an AI assistant for applications, so they expect to use the data there too, not only
  on the web page.
- The maintainer, who reviews reported sites and keeps the service running.

## Constraints

- Free to run. A free backend service such as Supabase, or a similar one, holds user accounts and preferences.
- Sign-in uses Google, so account security rests on Google.
- The service keeps working for visitors who do not sign in.
- The only personal detail we keep about a user is their email address. We discard what the sign-in provider sends (such
  as a name or photo) and never store CVs, phone numbers or other contact details. Besides the email, we keep the
  user's own choices (stars, "Not for me", filters, email settings) and a few technical records the service needs: when
  the account was created and last used, and when the email was confirmed or unsubscribed. Users can delete everything.
- Job data is served from the backend. If it cannot be reached, the dashboard shows a clear error message. There is no
  offline copy of the jobs.

## Requirements

Each requirement has acceptance criteria (AC). It is done when all of them hold.

### FR-1: Sign in with Google

- AC: A visitor who is not signed in sees the whole dashboard as today. Signing in is optional.
- AC: A user can sign in with their Google account and sign out. There is no password anywhere.
- AC: A user can see and change only their own information.
- AC: A user can delete their account and all of their data in one step.
- AC: `PRIVACY.md` says what we keep (email address, stars, "Not for me" choices, preferences), why, and that we never store a CV.
- AC: The only personal detail we keep is the email address. What the sign-in provider sends, such as a name or photo,
  is discarded and not shown or stored. The technical records listed in the constraints above are the only other
  account data.

### FR-2: Stars, discards and filters follow the user

Today a user can mark a job "Not for me", which hides it, and that choice lives only in one browser, along with the
filters they set. We add a Star, the opposite of "Not for me", for jobs the user is interested in. Stars, discards and
the dashboard filters all follow the user across devices.

- AC: A user can star a job from its card and remove the star again. A starred job can be viewed as a list.
- AC: A job marked "Not for me" stays hidden, and a job cannot be starred and discarded at the same time.
- AC: Stars and "Not for me" choices made on one device appear on the user's other devices after they sign in.
- AC: Discards already stored in the browser are offered for import the first time a user signs in, without duplicates.
- AC: The filters a user sets on the dashboard (regions and locations, levels, search words, company, sort order and
  the show/hide options) are remembered and appear on the user's other devices after they sign in.
- AC: Filters already stored in the browser are offered for import the first time a user signs in, together with the
  discards.
- AC: Starring, discarding and filtering still work when signed out, on that device only.
- AC: If the service is unreachable, a star or a discard is not lost.
- AC: A starred job that has since closed is still shown, marked as no longer listed. It keeps its title, company,
  location and link, so the user can still see what they starred.
- AC: A "Not for me" choice is removed as soon as its job closes, so these choices never pile up. A job that reopens
  later appears in the feed again.

### FR-3: Filter by region

Jobs outside Israel are not a concern for now.

- AC: A job in Israel whose place we recognise gets one of these regions:
  - Tel Aviv area (for example Tel Aviv, Ramat Gan)
  - Sharon (for example Herzliya, Raanana, Hod Hasharon)
  - Center (for example Petah Tikva, Rosh Haayin, Rehovot)
  - Jerusalem
  - Haifa and the north (for example Haifa, Yokneam, Migdal Haemek)
  - South (for example Beersheba)
  - Remote
- AC: A job in Israel whose place we cannot group into a region is listed under its own location. The filter offers
  the list of every distinct location in Israel found in the jobs, as written in the data, so a user can pick a place we
  did not group. For now the same place may appear under different spellings; grouping them can come later.
- AC: A job in Israel with no city, only "Israel", is listed as "Israel, city not given".
- AC: A job that lists several cities belongs to every region or location it names.
- AC: A hybrid role counts as the region of its office.
- AC: A remote role counts as Remote only when it is open to people in Israel. Remote roles for other countries, such as
  "Remote, US", are not shown as Remote. When the posting does not say, the job is not shown as Remote by default, and
  a user can choose to include these.
- AC: No job in Israel is dropped because we could not group its location.
- AC: Jobs outside Israel keep working as they do today behind the existing Israel / all locations choice, and
  regions apply to Israeli jobs only.
- AC: A user can pick one or more regions or locations, and the choice is remembered when signed in.
- AC: The region or location is visible on the card and available to AI assistants reading the page.
- AC: At least 95% of jobs in Israel fall into a region, and the locations we could not group are reported so we can
  improve.

### FR-4: Richer job data

We collect more about each job than the card shows. The card stays compact; the extra data is there to be used.

This is best-effort per site. Career sites differ: some give the full posting and others only a title and a link. We
collect what each site offers, and a job with little data still appears as it does today. Sites are added one at a time,
starting with the ones that matter most to users.

- AC: For every job whose site provides it, we keep the full posting text and the details it gives (such as
  requirements, seniority and type of role), beyond what the card shows today.
- AC: A job whose site gives only a title and a link keeps working everywhere as it does today, without a description.
- AC: The share of jobs with a full description is reported, so we can see which sites to improve first.
- AC: Each card shows a short description of the role: the first 255 characters of the posting's description. It is
  taken as written, with no AI involved, and nothing more is forced onto the card.
- AC: A user can drill down from a card to see the fuller details without leaving the dashboard.
- AC: Search finds jobs by words in the full posting text, not only in the title and company.
- AC: An AI assistant can read all of the collected data for a job (FR-6).
- AC: The short description never cuts a word in half, and a job with no description shows none.
- AC: A site that gives no description does not stop the daily update.
- AC: The cost of storage stays within the free budget.
- AC: If users tell us the short description is not good enough, we revisit it, and an AI-written summary is the
  option to consider then. Until we get that feedback, we do not use AI for it.

### FR-5: A complete coverage gap card

- AC: The card lists every site we know we do not crawl, with a reason for each (for example, the site is down, blocked,
  or has no public listing).
- AC: It matches the open work in `TODO.md`, and the two cannot silently drift apart.
- AC: It shows the number of gaps grouped by reason, and reads well on a phone.

### FR-6: Use the jobs from an AI assistant

- AC: Anyone can connect their AI assistant, without signing in, and ask it to search jobs by region, level and
  keyword. The first version offers public job search only.
- AC: The assistant can get one job with everything we collected (FR-4), enough to draft a cover letter (title, company,
  region, link and the full posting).
- AC: The assistant can search by words in the full posting text, the same as the dashboard search.
- AC: The assistant does not see anyone's stars, discards or preferences in this version.
- AC: The dashboard has a "Connect your assistant" page with plain, step-by-step instructions. Someone with no
  technical skills can connect their assistant in a couple of minutes by following it.
- AC: Connecting an assistant is optional. The dashboard and the email digest work fully without it.
- AC: The README keeps only a short technical note for developers.
- AC: The jobs the assistant sees match the ones on the dashboard.

### FR-7: Report a missing site

- AC: The coverage gap card has a "report a site" action that opens a short form on the dashboard.
- AC: The form asks for the company, the careers page link and why it matters. No account and no personal details are
  needed, so a visitor who is not signed in can send a report.
- AC: Each report is sent by email to the administrator.
- AC: The user sees a confirmation after sending, and an error message if it could not be sent.
- AC: A flood of reports, or automated spam, cannot fill the administrator's inbox.
- AC: Accepted sites move onto the crawl list or the coverage gap card with a reason.

### FR-8: Personalised daily digest

- AC: A signed-in user can turn the email on or off and choose the address it goes to (their Google email by default).
- AC: The digest filters are regions, levels, role types and keywords. A user sets them with one action on the
  dashboard, "Use these filters for my email", which copies those four filters. Later changes to the dashboard
  filters, such as a quick search, do not change the email until the user does that again.
- AC: Daily is the default for every new subscriber. Early-career roles attract many applicants and fill quickly, so a
  new listing matters on the day it appears. Weekly is available if the user chooses it.
- AC: Each subscriber gets only the jobs that match their own filters and that they have not been emailed before.
- AC: The first email holds only jobs that appeared after the user confirmed their email, never the existing backlog.
- AC: A user with nothing new gets no email (a weekly subscriber gets one summary a week).
- AC: The address is confirmed before the first email, and every email has an unsubscribe link that works in one click.
- AC: The emails come from a project address, not a personal one. A problem sending to one user never stops the others.
- AC: The email carries job listings only. Crawl problems are not in it; they stay on the coverage gap card (FR-5) and
  in a separate email to the maintainer, sent only when something new breaks.
- AC: The maintainer's own failure alerts keep working, and the maintainer receives the job digest as a normal
  subscriber, with filters chosen like anyone else's.

### FR-9: Level and role type

Two different things describe a job. The **level** says how senior it is (Intern, Junior). The **role type** says which
field it is in (Product, R&D, IT, Finance and so on). Today only Product is recognised as a field, and the dashboard
blends it with the levels. We keep the two apart, and recognise more role types over time.

- AC: Every job has a level and a role type, shown on the card.
- AC: Level and role type are separate filters, on the dashboard, in the digest settings and for the assistant.
- AC: The first role types are Product plus the common fields in the jobs we collect (for example R&D, IT and Finance).
  The final list is agreed during planning, and a job we cannot place is listed as "Other" and is never dropped.
- AC: A job can belong to more than one role type when its title says so.
- AC: Adding a role type later does not need a change to how users set their filters.
- AC: The share of jobs placed in a role type other than "Other" is reported, so we can see which fields to improve.
- AC: Users who choose no role type see every type.
- AC: A job matches when it fits both the levels and the role types a user chose. Product with every level gives
  Product roles at any seniority. Product with Intern and Junior gives only the entry-level ones.
- AC: This changes today's digest, which sends Product roles of any level plus Intern and Junior roles of any field.
  That is a bug, not a behaviour to keep: the digest follows the rule above.
- AC: A job whose level is not Intern or Junior has the level "Not specified" for now, can be chosen like any other
  level, and is never dropped. Senior, Staff and similar levels can be recognised later.

## Future idea: application help in the assistant

Not part of the first version. It builds on FR-4 and FR-6 and is written down so the early work leaves room for it.

When a user connects their AI assistant, we could also offer ready-made help for applying:

- Draft a cover letter for a specific job, using the full posting text.
- Suggest improvements to the user's CV, tailored to a specific job.
- Say what a CV is missing for a role.
- Rank the user's starred jobs by how well they fit. This needs the assistant to see a user's stars, which is a later
  step: the assistant would have to prove whose stars it is reading.

How it would work in the first version:

- The CV stays with the user, in their own assistant. We do not store it, and we collect no new personal data.
  This is a firm decision, not only a first-version choice: we will never save a user's CV, in any form.
- Our part is the job data plus good instructions for each task.
- The help only presents real experience better. It never invents experience, skills or achievements.

Possible later step:

- Store a small profile of preferences (skills, level, regions, keywords) that the user edits, to show fit on cards and
  improve the digest filters. It holds no name, contact details or CV content.

Decided against: storing the CV, whether original or with personal details removed. A cleaned CV can still identify a
person through employers, schools and dates, and keeping it would add privacy duties for little gain, since the
assistant already has the CV on the user's side and can add their name to a cover letter.

Open questions: which tasks users want most, and whether the help is offered inside the assistant only or also on the
dashboard.

## Success metrics

The service has no analytics or tracking (`PRIVACY.md`), so every metric below is read from data we already hold: the
user accounts, the email sender's reports, the daily crawl and the reports inbox. We look at them monthly. There are no
numeric targets yet, because we do not know our starting point; after the first two months we set them from what we
see.

- **Reach:** the number of people who signed in, and how many of them signed in again in the last 30 days.
- **Digest:** the number of subscribers, the share still subscribed after four weeks, and the unsubscribe and bounce
  rates reported by the email sender.
- **Usefulness:** the share of signed-in users who have starred at least one job.
- **Data quality:** the share of jobs in Israel that fall into a region (target: above 95%), and the share of jobs with
  a full description.
- **Coverage:** the number of sites on the coverage gap card, and the number of sites reported and accepted.
- **Assistant:** not measured. The assistant reads public data without signing in, so we cannot see who connects or
  how it is used.
- **Cost:** monthly cost stays at zero.

The first-minute sign-in experience is not a metric. It is checked once, by hand, as a launch test: sign in on a phone
and see the stars and discards made on a laptop.

## Open questions

None at the moment.

## Before launch

Check with a lawyer before we send digests to real users: the privacy notice for storing email addresses, and the
Israeli rules on sending email people signed up for. The consent, confirmation and unsubscribe steps in FR-8 are built
to satisfy them, and the lawyer confirms nothing is missing.

The questions, in plain language, are in the addendum at the end of this document.

## Proposed order (to be turned into the step plan)

1. Sign-in, plus stars and discards that sync (FR-1, FR-2).
2. Regions and filters (FR-3).
3. Coverage gap card and "report a site" (FR-5, FR-7). These are independent and can move earlier for a quick win.
4. Level and role type (FR-9), then the personalised digest (FR-8), so the digest can use them.
5. Richer job data and short descriptions, then the AI assistant connection (FR-4, FR-6).

## Notes for the plan

Technical direction agreed so far. These are inputs to the step plan, not requirements.

- The target is for all data to live in Supabase: jobs and their descriptions as well as user data. The daily crawl then
  writes there directly.
- Get there in steps. First copy the open jobs in Israel, with their descriptions, from SQLite into Supabase after each
  daily run, and have the dashboard drill-down, search and assistant read from that copy. Move the crawl itself over
  once that works.
- Before SQLite is retired, keep a regular export of the data into the repository, so the history and a restore path
  are not lost.
- Check the free-tier limits (database size, backups, project pausing) against the real data size before the move.
- Confirm the backend choice (Supabase is the working choice) with a small trial before committing to it.
- Email sending uses Resend, as in `easy-theory`. Its free tier is shared across the apps on that account (about 100
  emails a day, to be confirmed with the account), so check the sending volume before opening the digest to many
  subscribers, and move to a larger plan or another sender if it is outgrown.

## Addendum: what to ask the lawyer

This is written to be handed over as it is. It needs no legal knowledge. The aim is a short meeting that ends with clear
yes or no answers, ideally confirmed by email afterwards.

### What we are building, in a few sentences

Misra is a free website that lists open jobs at Israeli companies, mostly intern and junior roles. People can sign in
with their Google account. Once signed in, they can mark jobs they like or do not like, and they can ask for a daily
email with the new jobs that match what they chose (their area, their level, and keywords). Nobody pays and we show no
ads. We are a private individual, not a company, and we expect tens of users at first.

### What we keep about each user

- Their email address, and nothing else about who they are. We throw away the name and photo that Google sends.
- A few technical records: when the account was created and last used, and when the email was confirmed or
  unsubscribed.
- Their choices: the jobs they marked, their filters, and their email settings.
- We never ask for or keep a CV, a phone number or an address.
- A user can delete everything at any time.

### How the email works

- Only people who sign in and turn the email on get it.
- After they turn it on, we send a confirmation email first. The digest starts only after they click the link.
- Every email has an unsubscribe link that works with one click, without signing in.
- The email lists jobs from other companies' career pages. It does not sell or promote anything of ours.
- It is sent by a sending service (Resend), from a project address.

### Questions to ask

1. **Is our daily email "advertising" under the Israeli spam law?** It only lists public job postings the person asked
   for. If the lawyer says yes, we need to know exactly what each email must contain, such as the word "advertisement"
   or our name and address.
2. **Is our way of getting permission enough?** A person turns the email on, then confirms by clicking a link. Is that
   valid consent, and should we keep a record of when they agreed?
3. **Is our unsubscribe enough?** One click, no sign-in, and we stop sending at once. Is there anything more we must
   do, or a time limit?
4. **Do we have to register this list of emails anywhere?** We hold only email addresses, for a small group of people.
5. **What must our privacy notice say?** We will write it, and we want the lawyer to tell us what is missing. It should
   cover what we keep, why, who can see it, and how to delete it.
6. **What must we do when someone asks us to delete their data?** Is it enough to delete the account and everything
   tied to it, and how fast?
7. **Are we personally responsible as a private individual, and does that change if we later become a company?** We
   want to know the risk before we start.
8. **Does it matter that the service runs on providers abroad?** The database and the sending service are outside Israel.
   Do we need to tell users, or sign anything with those providers?
9. **Can we store and show other companies' job postings?** We copy the text of postings from their public career
   pages, show the first lines on our site, and let a user's AI assistant read the whole text. Is that allowed, and do
   we need to credit or link to the source, or remove a posting if a company asks?
10. **Is there anything else we should do before the first real email?** An open question at the end, so nothing is
   missed.

### What to ask for at the end

- A yes or no on each question above, in writing (an email is enough).
- The exact wording the lawyer wants in the emails and in the privacy notice, if any.
- A short list of anything we must change before launch.
