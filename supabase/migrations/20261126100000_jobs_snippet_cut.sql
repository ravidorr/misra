-- Whether the public snippet of a posting is shorter than the full text, so the page can show an ellipsis exactly (TODO.md,
-- "Snippet ellipsis"). `snippet_cut` is true when the 255-character snippet is shorter than the text (it was cut), false
-- when it is the whole text, and null when there is no snippet or the row predates this column. It says nothing of the
-- text itself: one flag, readable by everyone like `snippet`. `description` and `details` stay closed.
--
-- The sync step (sync_supabase.py, descriptions.snippet_cut) sets it from now on, and rewrites a row whose flag differs
-- from what it computes, so the daily run corrects any row the backfill below got wrong. The backfill computes the same
-- rule in SQL from the stored text (white space trimmed at both ends, as Python's strip does), so the page is exact from
-- the first load and no crawl is needed. Additive: nothing is dropped or changed but this one column. Not applied by the
-- pull request: apply it once after the merge, see supabase/README.md.

alter table public.jobs add column snippet_cut boolean;
grant select (snippet_cut) on public.jobs to anon, authenticated;

update public.jobs
   set snippet_cut = length(snippet) < length(regexp_replace(description, '^\s+|\s+$', '', 'g'))
 where snippet is not null
   and description is not null;
