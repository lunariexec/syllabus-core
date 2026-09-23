"""
End-to-end test of the yearly cycle on a real Postgres database:

  load 2026 edition (gold Theme 1) → approve → publish → confirm locked
  → start 2027 edition → change one block → approve → publish
  → confirm 2026 archived, history intact, deletes refused.

Usage: python tests/test_yearly_cycle.py <database-name>
"""
import json
import subprocess
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else "sc_test"
ROOT = __file__.rsplit("/tests/", 1)[0]


def psql(sql, expect_error=False):
    r = subprocess.run(["su", "postgres", "-c", f"psql -X -q -t -A -v ON_ERROR_STOP=1 -d {DB}"],
                       input=sql, capture_output=True, text=True)
    if expect_error:
        assert r.returncode != 0, f"Expected an error but it succeeded:\n{sql}"
        return next(l for l in r.stderr.splitlines() if l.startswith("ERROR"))
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def q(s):
    return "'" + s.replace("'", "''") + "'"


results = []
def check(name, cond):
    results.append((name, cond))
    print(("  ✓ " if cond else "  ✗ ") + name)


blueprint = json.load(open(f"{ROOT}/blueprints/nacca-basic1-english.v1.json"))
theme = json.load(open(f"{ROOT}/gold/nacca-basic1-english/theme1.json"))["theme"]

ADMIN = "00000000-0000-0000-0000-000000000001"
LEAD = "00000000-0000-0000-0000-000000000002"

# ---- Seed reference data ------------------------------------------------
seed = f"""
set app.actor_id = '{ADMIN}';
insert into organisations(id, name, country) values ('10000000-0000-0000-0000-000000000001', 'Pilot Publisher (Ghana)', 'GH');
insert into profiles(id, full_name, email) values
  ('{ADMIN}', 'Camille (Admin)', 'admin@example.com'),
  ('{LEAD}',  'Curriculum Lead', 'lead@example.com');
insert into memberships(org_id, user_id, role) values
  ('10000000-0000-0000-0000-000000000001', '{ADMIN}', 'admin'),
  ('10000000-0000-0000-0000-000000000001', '{LEAD}',  'curriculum_lead');
insert into frameworks(id, code, name, country, home_languages) values
  ('20000000-0000-0000-0000-000000000001', 'NACCA', 'NaCCA Standards-Based Curriculum', 'GH', '{{Twi,Ewe,Ga,Dagbani}}');
insert into subjects(id, framework_id, code, name) values ('30000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'ENG', 'English Language');
insert into grades(id, framework_id, code, name, sort_order) values ('40000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 'B1', 'Basic 1', 1);
insert into curriculum_sources(id, subject_id, grade_id, academic_year, title, storage_path, checksum) values
  ('50000000-0000-0000-0000-000000000001', '30000000-0000-0000-0000-000000000001', '40000000-0000-0000-0000-000000000001', 2026, 'NaCCA English Curriculum Draft 1, 2026', 'sources/b1-eng-2026.pdf', 'sha-2026'),
  ('50000000-0000-0000-0000-000000000002', '30000000-0000-0000-0000-000000000001', '40000000-0000-0000-0000-000000000001', 2027, 'NaCCA English Curriculum 2027', 'sources/b1-eng-2027.pdf', 'sha-2027');
insert into blueprints(id, subject_id, grade_id, version, name, spec) values
  ('60000000-0000-0000-0000-000000000001', '30000000-0000-0000-0000-000000000001', '40000000-0000-0000-0000-000000000001', 1, 'Basic 1 English v1', {q(json.dumps(blueprint))}::jsonb);
insert into editions(id, org_id, subject_id, grade_id, academic_year, title, curriculum_source_id, blueprint_id, created_by) values
  ('70000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-000000000001', '30000000-0000-0000-0000-000000000001',
   '40000000-0000-0000-0000-000000000001', 2026, 'Basic 1 English: Our World • Our Words',
   '50000000-0000-0000-0000-000000000001', '60000000-0000-0000-0000-000000000001', '{ADMIN}');
"""

# ---- Load gold Theme 1 as the 2026 edition ------------------------------
load = [f"set app.actor_id = '{ADMIN}';",
        f"insert into themes(id, edition_id, number, title, page_start, page_end) values "
        f"('80000000-0000-0000-0000-000000000001', '70000000-0000-0000-0000-000000000001', 1, {q(theme['title'])}, 5, 11);"]
for u in theme["units"]:
    load.append(f"""with nu as (insert into units(theme_id, number, kind, title, page_start, page_end, standard_codes)
      values ('80000000-0000-0000-0000-000000000001', {u['number']}, '{u['kind']}', {q(u['title'])},
              {u['pages'][0]}, {u['pages'][1]}, '{{{",".join(u.get("standard_codes", []))}}}') returning id)
      select 1 from nu;""")
    for i, b in enumerate(u["blocks"], start=1):
        load.append(f"""with nb as (insert into content_blocks(unit_id, block_type, sort_order)
            select u.id, '{b['type']}', {i} from units u join themes t on t.id = u.theme_id
             where t.edition_id = '70000000-0000-0000-0000-000000000001' and u.number = {u['number']} returning id)
          insert into block_versions(block_id, content, origin, created_by)
          select id, {q(json.dumps(b['content']))}::jsonb, 'imported', '{ADMIN}' from nb;""")

psql(seed + "\n".join(load))

E26 = "'70000000-0000-0000-0000-000000000001'"
count_blocks = sum(len(u["blocks"]) for u in theme["units"])
print("2026 edition")
check(f"Theme 1 loaded with {count_blocks} blocks",
      psql(f"select count(*) from content_blocks b join units u on u.id=b.unit_id join themes t on t.id=u.theme_id where t.edition_id={E26}") == str(count_blocks))

check("Publishing before approval is refused",
      "must be approved" in psql(f"select publish_edition({E26});", expect_error=True))

psql(f"""set app.actor_id = '{LEAD}';
update content_blocks set status='approved' where unit_id in (select u.id from units u join themes t on t.id=u.theme_id where t.edition_id={E26});
update editions set status='approved' where id={E26};
select publish_edition({E26});""")
check("2026 edition published", psql(f"select status from editions where id={E26}") == "published")

check("Published edition is locked against new versions",
      "locked" in psql(f"""insert into block_versions(block_id, content, origin)
        select b.id, '{{}}'::jsonb, 'human_edit' from content_blocks b join units u on u.id=b.unit_id
        join themes t on t.id=u.theme_id where t.edition_id={E26} limit 1;""", expect_error=True))

# ---- Start the 2027 edition ---------------------------------------------
print("2027 edition")
E27 = psql(f"select start_new_edition({E26}, 2027, '50000000-0000-0000-0000-000000000002', '{ADMIN}');")
E27q = f"'{E27}'"
check("2027 draft created from 2026",
      psql(f"select status || ',' || based_on_edition_id from editions where id={E27q}") == f"draft,{E26.strip(chr(39))}")
check("All blocks carried forward",
      psql(f"""select count(*) from block_versions v join content_blocks b on b.id=v.block_id join units u on u.id=b.unit_id
               join themes t on t.id=u.theme_id where t.edition_id={E27q} and v.origin='carried_forward'""") == str(count_blocks))

# Simulate a curriculum change regenerating one block (Unit 1 song gets a new version)
psql(f"""set app.actor_id = '{ADMIN}';
insert into block_versions(block_id, content, origin, created_by)
select b.id, '{{"form":"song","lines":["(regenerated 2027 lines)"],"performance_instruction":"..."}}'::jsonb, 'ai_generated', '{ADMIN}'
  from content_blocks b join units u on u.id=b.unit_id join themes t on t.id=u.theme_id
 where t.edition_id={E27q} and u.number=1 and b.block_type='oral_text';""")
check("Changed block now has 2 versions (history kept)",
      psql(f"""select max(v.version_no) from block_versions v join content_blocks b on b.id=v.block_id join units u on u.id=b.unit_id
               join themes t on t.id=u.theme_id where t.edition_id={E27q} and u.number=1 and b.block_type='oral_text'""") == "2")

psql(f"""set app.actor_id = '{LEAD}';
update content_blocks set status='approved' where unit_id in (select u.id from units u join themes t on t.id=u.theme_id where t.edition_id={E27q});
update editions set status='approved' where id={E27q};
select publish_edition({E27q});""")
check("2027 edition published", psql(f"select status from editions where id={E27q}") == "published")
check("2026 edition automatically archived", psql(f"select status from editions where id={E26}") == "archived")
check("2026 content still intact in the archive",
      psql(f"""select v.content->'lines'->>0 from block_versions v join content_blocks b on b.id=b.id and v.id=b.current_version_id
               join units u on u.id=b.unit_id join themes t on t.id=u.theme_id
               where t.edition_id={E26} and u.number=1 and b.block_type='oral_text'""") == "I see a tree, green and tall!")

print("Safeguards")
check("Deleting an edition is refused", "not allowed" in psql(f"delete from editions where id={E26};", expect_error=True))
check("Editing an old version is refused", "immutable" in psql("update block_versions set content='{}'::jsonb;", expect_error=True))
check("Every status change was logged with who did it",
      psql(f"select count(*) from workflow_events where actor_id is null") == "0"
      and int(psql("select count(*) from workflow_events")) >= 4)

passed = sum(1 for _, c in results if c)
print(f"\n{passed}/{len(results)} checks passed")
sys.exit(0 if passed == len(results) else 1)
