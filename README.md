# Syllabus Core — first version (v0.1)

A curriculum platform that generates pupil books from an official curriculum and a
book blueprint, sends them through review, publishes one edition per year and archives
the old editions. Nothing is ever deleted.

**Owner of the code:** Tatum Camille Kayan. Clients buy a licence; they do not own the code.

## Pilot scope

NaCCA (Ghana) · Basic 1 · English Language · Theme 1 as the reference ("gold standard").

## People and roles (pilot)

| Person  | Role(s)                | Can do                                                        |
|---------|------------------------|---------------------------------------------------------------|
| Camille | admin                  | Everything: users, settings, generation, publishing           |
| Emilia  | curriculum_lead        | Upload curricula, approve content, approve and publish editions |
| Peter   | finance, reviewer      | Billing and usage costs; review and comment on content        |

Other roles, ready for later: writer, designer, teacher (read and export only).

## What's in this folder

| Path | What it is |
|---|---|
| `supabase/migrations/0001_core_schema.sql` | The database: frameworks, curricula, editions, themes, units, content blocks, version history, reviews, illustrations, audit log, yearly-cycle functions, access rules |
| `blueprints/nacca-basic1-english.v1.json` | The Basic 1 book written down as rules: page layout, theme order, song/rhyme/conversation rhythm, phonics pathway, grammar roadmap, inclusion rules |
| `gold/nacca-basic1-english/theme1.json` | Theme 1 (pages 5–11) of the approved book as structured data |
| `tests/check_against_blueprint.py` | Automatic quality checker. Every AI-generated unit will pass through it before a human sees it |
| `tests/test_yearly_cycle.py` | Proves the yearly cycle works on a real database |
| `engine/` | The generation engine: writes a theme with the Claude API, checks and repairs it, and builds a demo page |

## How the yearly cycle works

1. Upload the new official curriculum document.
2. `start_new_edition()` copies last year's edition, carrying every block forward.
3. The engine regenerates only the units whose standards changed. Each change is saved as a new version.
4. The curriculum lead reviews and approves.
5. `publish_edition()` publishes the new edition and archives the old one automatically.

**Safeguards enforced by the database itself:**

- Published and archived editions are locked.
- Old versions can't be edited.
- Deleting is refused.
- Every status change records who made it.

## Test results (22 Sept 2026)

- Yearly cycle: **13/13 passed** on PostgreSQL 16 with pgvector.
- Quality checker on the hand-made Theme 1: **200/202 passed**. The two flags are real findings in the approved book:
  - p.7 "I see an **ant**": *n* isn't taught until Unit 2. Mark "ant" as a sight word, or swap the word.
  - p.9 "A **pig** sat in the pan": *g* isn't taught until Unit 3. Swap "pig" (for example "A pin is in the pan"), or mark it as a sight word.

## Needs confirmation from Emilia

- In the blueprint, the phonics groups and grammar focus for Themes 2–9 were worked out from the "How to use this book" page. Please confirm them.
- Units 2 and 3 have short vocabulary-strip instructions in the gold file that aren't printed in the book.

## Run the tests

```bash
createdb sc_test && psql -d sc_test -f supabase/migrations/0001_core_schema.sql
python3 tests/test_yearly_cycle.py sc_test
python3 tests/check_against_blueprint.py blueprints/nacca-basic1-english.v1.json gold/nacca-basic1-english/theme1.json
```

## Generation engine (demo: regenerate Theme 1)

`engine/generate.py` writes a theme unit by unit with the Claude API. Each unit goes through
the quality checker; any problems go back to the AI to fix (up to 3 rounds) before the next
unit is written. The finished theme is scored with the same checker as the approved book.

```bash
pip install -r engine/requirements.txt
export ANTHROPIC_API_KEY=...            # PowerShell: $env:ANTHROPIC_API_KEY = "..."
python engine/generate.py --theme 1
```

Each run writes `runs/<date-time>-theme1/`:

| File | What it is |
|---|---|
| `theme1.generated.json` | The AI draft, in the same format as the gold file |
| `run.json` | Every AI call, what the checker found, time, tokens and cost |
| `demo.html` | The client demo: replay of the run, and the AI draft beside the approved book. Opens in any browser. |

Options: `--effort low|medium|high|xhigh|max` (default high), `--max-repairs N` (default 3),
`--model` (default claude-opus-5). Rebuild a demo page from an old run with
`python engine/report.py runs/<folder>/run.json`.

The curriculum standard in `engine/inputs/` is taken from the approved book for now; it will
come from the uploaded curriculum document once that step is built.

## Next

1. Connect Supabase (logins, file storage).
2. Generation engine: add the curriculum document → standards step, and Themes 2–9 (the v0.1 engine does 7-page themes with Stage 1 phonics).
3. Build the review screens.
4. Build the PDF export in the book layout.
