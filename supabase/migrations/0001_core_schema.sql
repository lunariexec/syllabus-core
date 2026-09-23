-- =====================================================================
-- Syllabus Core — core schema (v0.1, first cut)
-- Target: Supabase (Postgres 15+) with pgvector.
--
-- Principles
--   1. Curriculum-agnostic: NaCCA (Ghana) first, CAPS (SA) later = new rows, not new code.
--   2. Nothing is ever deleted. Content changes create new immutable versions.
--   3. Editions are yearly. A published edition is locked; publishing a new one
--      archives the old one automatically.
--   4. Every status change is logged (who, when, why).
-- =====================================================================

create extension if not exists vector;
create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------
create type app_role as enum ('admin', 'curriculum_lead', 'reviewer', 'finance', 'writer', 'designer', 'teacher');
create type edition_status as enum ('draft', 'in_review', 'approved', 'published', 'archived');
create type block_status as enum ('draft', 'in_review', 'changes_requested', 'approved');
create type version_origin as enum ('ai_generated', 'human_edit', 'carried_forward', 'imported');
create type run_status as enum ('queued', 'running', 'succeeded', 'failed', 'cancelled');
create type diff_kind as enum ('added', 'removed', 'modified', 'unchanged');

-- ---------------------------------------------------------------------
-- Organisations, people, roles
-- ---------------------------------------------------------------------
create table organisations (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  country     text not null,                      -- 'GH', 'ZA'
  created_at  timestamptz not null default now()
);

-- One row per login. id matches Supabase auth.users.id.
create table profiles (
  id          uuid primary key,
  full_name   text not null,
  email       text not null unique,
  created_at  timestamptz not null default now()
);

create table memberships (
  org_id      uuid not null references organisations(id),
  user_id     uuid not null references profiles(id),
  role        app_role not null,
  active      boolean not null default true,
  created_at  timestamptz not null default now(),
  primary key (org_id, user_id, role)
);

-- ---------------------------------------------------------------------
-- Curriculum frameworks (the "framework packs")
-- ---------------------------------------------------------------------
create table frameworks (
  id          uuid primary key default gen_random_uuid(),
  code        text not null unique,               -- 'NACCA', 'CAPS'
  name        text not null,                      -- 'NaCCA Standards-Based Curriculum'
  country     text not null,
  language_variant text not null default 'en-GB', -- spelling dictionary for the stylist
  home_languages text[] not null default '{}'     -- bridge-table languages, e.g. {Twi,Ewe,Ga,Dagbani}
);

create table subjects (
  id           uuid primary key default gen_random_uuid(),
  framework_id uuid not null references frameworks(id),
  code         text not null,                     -- 'ENG'
  name         text not null,                     -- 'English Language'
  unique (framework_id, code)
);

create table grades (
  id           uuid primary key default gen_random_uuid(),
  framework_id uuid not null references frameworks(id),
  code         text not null,                     -- 'B1'
  name         text not null,                     -- 'Basic 1'
  sort_order   int  not null,
  unique (framework_id, code)
);

-- The official documents we generate FROM (uploaded, never scraped blindly).
create table curriculum_sources (
  id            uuid primary key default gen_random_uuid(),
  subject_id    uuid not null references subjects(id),
  grade_id      uuid not null references grades(id),
  academic_year int  not null,                    -- 2026 = the 2026/27 year
  title         text not null,                    -- 'NaCCA English Curriculum Draft 1, 2026'
  storage_path  text not null,                    -- Supabase Storage key of the PDF/DOCX
  checksum      text not null,                    -- sha256, detects re-uploads of the same file
  uploaded_by   uuid references profiles(id),
  uploaded_at   timestamptz not null default now(),
  unique (subject_id, grade_id, checksum)
);

-- Individual content standards extracted from a source (e.g. 'B1.1.1').
create table curriculum_standards (
  id           uuid primary key default gen_random_uuid(),
  source_id    uuid not null references curriculum_sources(id),
  code         text not null,                     -- 'B1.1.1'
  strand       text not null,                     -- 'Oral Language'
  sub_strand   text,
  statement    text not null,
  indicators   jsonb not null default '[]',
  embedding    vector(1024),                      -- for "find the standard this block serves"
  unique (source_id, code)
);

-- Year-on-year comparison: what changed between two sources.
create table curriculum_diffs (
  id             uuid primary key default gen_random_uuid(),
  from_source_id uuid not null references curriculum_sources(id),
  to_source_id   uuid not null references curriculum_sources(id),
  standard_code  text not null,
  kind           diff_kind not null,
  old_statement  text,
  new_statement  text,
  created_at     timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- Book blueprints: the layout contract the AI must obey
-- ---------------------------------------------------------------------
create table blueprints (
  id          uuid primary key default gen_random_uuid(),
  subject_id  uuid not null references subjects(id),
  grade_id    uuid not null references grades(id),
  version     int  not null,
  name        text not null,
  spec        jsonb not null,                     -- see /blueprints/*.json
  created_at  timestamptz not null default now(),
  unique (subject_id, grade_id, version)
);

-- ---------------------------------------------------------------------
-- Editions → themes → units → content blocks → versions
-- ---------------------------------------------------------------------
create table editions (
  id                   uuid primary key default gen_random_uuid(),
  org_id               uuid not null references organisations(id),
  subject_id           uuid not null references subjects(id),
  grade_id             uuid not null references grades(id),
  academic_year        int  not null,
  revision             int  not null default 1,   -- '2027 Edition, rev 2'
  title                text not null,             -- 'Basic 1 English: Our World • Our Words'
  status               edition_status not null default 'draft',
  curriculum_source_id uuid references curriculum_sources(id),
  blueprint_id         uuid references blueprints(id),
  based_on_edition_id  uuid references editions(id),
  published_at         timestamptz,
  archived_at          timestamptz,
  created_by           uuid references profiles(id),
  created_at           timestamptz not null default now(),
  unique (org_id, subject_id, grade_id, academic_year, revision)
);
-- Only one live (published) edition per org/subject/grade at a time.
create unique index one_published_edition
  on editions (org_id, subject_id, grade_id) where status = 'published';

create table themes (
  id          uuid primary key default gen_random_uuid(),
  edition_id  uuid not null references editions(id),
  number      int  not null,
  title       text not null,
  colour      text,                               -- tab colour in the book
  page_start  int,
  page_end    int,
  unique (edition_id, number)
);

create table units (
  id          uuid primary key default gen_random_uuid(),
  theme_id    uuid not null references themes(id),
  number      int  not null,                      -- 0 = theme project, 99 = theme assessment
  kind        text not null default 'unit' check (kind in ('project','unit','assessment')),
  title       text not null,
  page_start  int,
  page_end    int,
  standard_codes text[] not null default '{}',
  unique (theme_id, number)
);

-- A block = one section on the page (song, vocabulary strip, phonics, teacher note ...).
create table content_blocks (
  id            uuid primary key default gen_random_uuid(),
  unit_id       uuid not null references units(id),
  block_type    text not null,                    -- must be a type defined in the blueprint
  sort_order    int  not null,
  status        block_status not null default 'draft',
  current_version_id uuid,                        -- FK added below
  standard_codes text[] not null default '{}',
  unique (unit_id, sort_order)
);

-- Immutable history. Rows are INSERT-only (enforced by trigger).
create table block_versions (
  id                uuid primary key default gen_random_uuid(),
  block_id          uuid not null references content_blocks(id),
  version_no        int  not null,
  content           jsonb not null,
  origin            version_origin not null,
  generation_run_id uuid,
  carried_from_version_id uuid references block_versions(id),
  created_by        uuid references profiles(id),
  created_at        timestamptz not null default now(),
  unique (block_id, version_no)
);
alter table content_blocks
  add constraint content_blocks_current_version_fk
  foreign key (current_version_id) references block_versions(id);

-- ---------------------------------------------------------------------
-- AI generation + quality checks
-- ---------------------------------------------------------------------
create table generation_runs (
  id            uuid primary key default gen_random_uuid(),
  edition_id    uuid not null references editions(id),
  scope         jsonb not null,                   -- {"unit_ids":[...]} or {"theme_ids":[...]}
  model         text not null,                    -- e.g. 'claude-...' (recorded, never assumed)
  prompt_version text not null,
  status        run_status not null default 'queued',
  input_tokens  int,
  output_tokens int,
  error         text,
  requested_by  uuid references profiles(id),
  started_at    timestamptz,
  finished_at   timestamptz,
  created_at    timestamptz not null default now()
);
alter table block_versions
  add constraint block_versions_run_fk
  foreign key (generation_run_id) references generation_runs(id);

create table quality_checks (
  id               uuid primary key default gen_random_uuid(),
  block_version_id uuid not null references block_versions(id),
  check_name       text not null,                 -- 'decodable_words', 'standard_alignment', 'response_modes' ...
  passed           boolean not null,
  score            numeric,
  details          jsonb not null default '{}',
  created_at       timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- Review, comments, workflow log, audit
-- ---------------------------------------------------------------------
create table comments (
  id          uuid primary key default gen_random_uuid(),
  block_id    uuid not null references content_blocks(id),
  version_id  uuid references block_versions(id),
  parent_id   uuid references comments(id),
  author_id   uuid not null references profiles(id),
  body        text not null,
  resolved    boolean not null default false,
  created_at  timestamptz not null default now()
);

create table workflow_events (
  id          bigserial primary key,
  entity_type text not null check (entity_type in ('edition','block')),
  entity_id   uuid not null,
  from_status text,
  to_status   text not null,
  actor_id    uuid references profiles(id),
  note        text,
  created_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- Illustrations: each image used once per edition
-- ---------------------------------------------------------------------
create table assets (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null references organisations(id),
  storage_path text not null unique,
  caption      text,
  alt_text     text not null,                     -- required: low-vision learners / screen readers
  tags         text[] not null default '{}',
  uploaded_by  uuid references profiles(id),
  created_at   timestamptz not null default now()
);

create table asset_placements (
  edition_id  uuid not null references editions(id),
  asset_id    uuid not null references assets(id),
  block_id    uuid not null references content_blocks(id),
  primary key (edition_id, asset_id)              -- the "no doubles" rule
);

-- =====================================================================
-- Guards: immutability + locking
-- =====================================================================

-- Nothing is deleted, anywhere.
create or replace function forbid_delete() returns trigger language plpgsql as $$
begin
  raise exception 'Deleting from % is not allowed. Archive instead.', tg_table_name;
end $$;

do $$
declare t text;
begin
  foreach t in array array['editions','themes','units','content_blocks','block_versions',
                           'curriculum_sources','curriculum_standards','comments',
                           'workflow_events','generation_runs','quality_checks','assets']
  loop
    execute format('create trigger %I_no_delete before delete on %I
                    for each row execute function forbid_delete()', t, t);
  end loop;
end $$;

-- Versions are write-once.
create or replace function forbid_update() returns trigger language plpgsql as $$
begin
  raise exception '% rows are immutable. Insert a new version instead.', tg_table_name;
end $$;
create trigger block_versions_immutable before update on block_versions
  for each row execute function forbid_update();

-- Helper: which edition does a block belong to?
create or replace function edition_of_block(p_block uuid) returns uuid
language sql stable as $$
  select t.edition_id from content_blocks b
  join units u on u.id = b.unit_id
  join themes t on t.id = u.theme_id
  where b.id = p_block
$$;

-- Published and archived editions are locked: no new versions, no edits.
create or replace function guard_locked_edition() returns trigger language plpgsql as $$
declare s edition_status; b uuid;
begin
  if tg_table_name = 'block_versions' then
    b := (to_jsonb(new) ->> 'block_id')::uuid;
  else
    b := (to_jsonb(new) ->> 'id')::uuid;
  end if;
  select status into s from editions where id = edition_of_block(b);
  if s in ('published','archived') then
    raise exception 'This edition is % and locked. Start a new edition to make changes.', s;
  end if;
  return new;
end $$;
create trigger block_versions_lock before insert on block_versions
  for each row execute function guard_locked_edition();
create trigger content_blocks_lock before update on content_blocks
  for each row execute function guard_locked_edition();

-- Auto-number versions and point the block at its newest version.
create or replace function next_block_version() returns trigger language plpgsql as $$
begin
  select coalesce(max(version_no), 0) + 1 into new.version_no
    from block_versions where block_id = new.block_id;
  return new;
end $$;
create trigger block_versions_number before insert on block_versions
  for each row execute function next_block_version();

create or replace function set_current_version() returns trigger language plpgsql as $$
begin
  update content_blocks set current_version_id = new.id, status = 'draft'
   where id = new.block_id;
  return new;
end $$;
create trigger block_versions_current after insert on block_versions
  for each row execute function set_current_version();

-- Log every status change.
create or replace function log_status_change() returns trigger language plpgsql as $$
begin
  if new.status is distinct from old.status then
    insert into workflow_events(entity_type, entity_id, from_status, to_status, actor_id)
    values (case when tg_table_name = 'editions' then 'edition' else 'block' end,
            new.id, old.status::text, new.status::text, current_actor());
  end if;
  return new;
end $$;

-- current_actor(): Supabase's auth.uid() when present, otherwise a session setting (tests, jobs).
create or replace function current_actor() returns uuid language plpgsql stable as $$
declare uid uuid;
begin
  begin
    execute 'select auth.uid()' into uid;
  exception when others then
    uid := nullif(current_setting('app.actor_id', true), '')::uuid;
  end;
  return uid;
end $$;

create trigger editions_log after update on editions
  for each row execute function log_status_change();
create trigger blocks_log after update of status on content_blocks
  for each row execute function log_status_change();

-- =====================================================================
-- Yearly cycle
-- =====================================================================

-- Start next year's edition: copies the structure and carries every
-- block forward as version 1 (origin = carried_forward). The generator
-- then regenerates only units whose standards changed.
create or replace function start_new_edition(
  p_from_edition uuid, p_academic_year int, p_source uuid, p_actor uuid
) returns uuid language plpgsql as $$
declare
  e editions; new_id uuid; th record; un record; bl record;
  new_theme uuid; new_unit uuid; new_block uuid; rev int;
begin
  select * into e from editions where id = p_from_edition;
  if not found then raise exception 'Edition % not found', p_from_edition; end if;

  select coalesce(max(revision), 0) + 1 into rev from editions
   where org_id = e.org_id and subject_id = e.subject_id
     and grade_id = e.grade_id and academic_year = p_academic_year;

  insert into editions(org_id, subject_id, grade_id, academic_year, revision, title,
                       curriculum_source_id, blueprint_id, based_on_edition_id, created_by)
  values (e.org_id, e.subject_id, e.grade_id, p_academic_year, rev, e.title,
          coalesce(p_source, e.curriculum_source_id), e.blueprint_id, e.id, p_actor)
  returning id into new_id;

  for th in select * from themes where edition_id = e.id order by number loop
    insert into themes(edition_id, number, title, colour, page_start, page_end)
    values (new_id, th.number, th.title, th.colour, th.page_start, th.page_end)
    returning id into new_theme;

    for un in select * from units where theme_id = th.id order by number loop
      insert into units(theme_id, number, kind, title, page_start, page_end, standard_codes)
      values (new_theme, un.number, un.kind, un.title, un.page_start, un.page_end, un.standard_codes)
      returning id into new_unit;

      for bl in select b.*, v.content as vcontent, v.id as vid
                  from content_blocks b join block_versions v on v.id = b.current_version_id
                 where b.unit_id = un.id order by b.sort_order loop
        insert into content_blocks(unit_id, block_type, sort_order, standard_codes)
        values (new_unit, bl.block_type, bl.sort_order, bl.standard_codes)
        returning id into new_block;

        insert into block_versions(block_id, content, origin, carried_from_version_id, created_by)
        values (new_block, bl.vcontent, 'carried_forward', bl.vid, p_actor);
      end loop;
    end loop;
  end loop;

  return new_id;
end $$;

-- Publish: every block must be approved; the previous live edition is archived.
create or replace function publish_edition(p_edition uuid) returns void language plpgsql as $$
declare e editions; pending int;
begin
  select * into e from editions where id = p_edition for update;
  if e.status <> 'approved' then
    raise exception 'Edition must be approved before publishing (currently %)', e.status;
  end if;

  select count(*) into pending from content_blocks b
    join units u on u.id = b.unit_id join themes t on t.id = u.theme_id
   where t.edition_id = p_edition and b.status <> 'approved';
  if pending > 0 then
    raise exception '% block(s) are not approved yet', pending;
  end if;

  update editions set status = 'archived', archived_at = now()
   where org_id = e.org_id and subject_id = e.subject_id and grade_id = e.grade_id
     and status = 'published';

  update editions set status = 'published', published_at = now() where id = p_edition;
end $$;

-- =====================================================================
-- Row-level security (Supabase): users see only their organisation.
-- Write permissions follow role. Policies are deliberately simple for v0.1.
-- =====================================================================
create or replace function has_role(p_org uuid, p_roles app_role[]) returns boolean
language sql stable security definer as $$
  select exists (select 1 from memberships
                  where org_id = p_org and user_id = current_actor()
                    and active and role = any(p_roles))
$$;

alter table editions       enable row level security;
alter table content_blocks enable row level security;
alter table block_versions enable row level security;
alter table comments       enable row level security;

create policy editions_read on editions for select
  using (has_role(org_id, array['admin','curriculum_lead','writer','designer','reviewer','teacher']::app_role[]));
create policy editions_write on editions for all
  using (has_role(org_id, array['admin','curriculum_lead']::app_role[]));

create policy blocks_read on content_blocks for select
  using (has_role((select org_id from editions where id = edition_of_block(id)),
                  array['admin','curriculum_lead','writer','designer','reviewer','teacher']::app_role[]));
create policy blocks_write on content_blocks for update
  using (has_role((select org_id from editions where id = edition_of_block(id)),
                  array['admin','curriculum_lead','writer','reviewer']::app_role[]));

create policy versions_read on block_versions for select
  using (has_role((select org_id from editions where id = edition_of_block(block_id)),
                  array['admin','curriculum_lead','writer','designer','reviewer','teacher']::app_role[]));
create policy versions_write on block_versions for insert
  with check (has_role((select org_id from editions where id = edition_of_block(block_id)),
                       array['admin','curriculum_lead','writer']::app_role[]));

create policy comments_rw on comments for all
  using (has_role((select org_id from editions where id = edition_of_block(block_id)),
                  array['admin','curriculum_lead','writer','designer','reviewer']::app_role[]));
