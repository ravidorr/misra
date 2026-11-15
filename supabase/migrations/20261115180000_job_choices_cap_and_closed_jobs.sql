-- Stars and discards sync (PLAN.md step 4.2; PRD FR-2). Two rules on top of public.user_jobs, both enforced in the
-- database so no client can skip them.
--
-- 1. A cap on the rows of one user (2,000). A star or a discard is one row, and the page lets a user make as many as
--    they like, so without a cap one account could fill the database. Changing the state of a job the user already has
--    a row for is never refused (an upsert fires the insert trigger too, so the trigger lets an existing row through).
--    The refusal is error code 54000 (program_limit_exceeded), which the page tells apart from a network failure.
-- 2. A discard disappears when its job closes (FR-2 AC 10), so discards never pile up, and a job that reopens later shows
--    in the feed again because nothing hides it. A star stays: it keeps the title, location and link, so the user can
--    still see what they starred, marked as no longer listed. The daily sync closes a job by upserting
--    status = 'closed' on public.jobs (service role); this trigger runs on that write. It is security definer because
--    the rows belong to other users, and it only ever deletes discards of the one job that closed.

create function public.limit_user_jobs() returns trigger
language plpgsql
set search_path = ''
as $$
begin
  -- One writer at a time per user, so two requests at the cap cannot both pass the count.
  perform pg_advisory_xact_lock(hashtext(new.user_id::text));
  if exists (
    select 1 from public.user_jobs
    where user_id = new.user_id and company = new.company and job_id = new.job_id
  ) then
    return new;
  end if;
  if (select count(*) from public.user_jobs where user_id = new.user_id) >= 2000 then
    raise exception 'A user can keep at most 2000 starred and discarded jobs' using errcode = '54000';
  end if;
  return new;
end;
$$;

revoke all on function public.limit_user_jobs() from public, anon, authenticated;

create trigger limit_user_jobs before insert on public.user_jobs
  for each row execute function public.limit_user_jobs();

create function public.drop_discards_of_closed_job() returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  delete from public.user_jobs
  where state = 'discard' and company = new.company and job_id = new.job_id;
  return new;
end;
$$;

revoke all on function public.drop_discards_of_closed_job() from public, anon, authenticated;

create trigger drop_discards_of_closed_job after insert or update of status on public.jobs
  for each row when (new.status = 'closed') execute function public.drop_discards_of_closed_job();
