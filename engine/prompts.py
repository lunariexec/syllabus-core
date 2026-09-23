"""
Prompts for the generation engine.

The system prompt holds the whole blueprint and never changes between calls, so it
is cached after the first request. Everything that varies (which unit, the theme so
far, checker problems) goes in the user turn.
"""
import json

BLOCK_SHAPES = """\
A unit is one JSON object:
  {"title": "<unit title>", "blocks": [{"type": "<block type>", "page": "left" | "right", "content": {...}}]}
"page" is only used in two-page units. Block content shapes:

theme_header       {"theme_number": int, "title": str, "pages": "5–11", "phonics_summary": str, "grammar_focus": str, "project_title": str}
unit_header        {"theme_number": int, "unit_number": int, "title": str, "competency_label": str}
hero_illustration  {"brief": str (for the illustrator), "caption": str (printed), "alt_text": str, "labels": [str] (optional, labelled diagrams only)}
oral_text          {"form": "song" | "rhyme" | "conversation", "lines": [str], "performance_instruction": str}
                   Conversation lines are written "Name: words".
talk_about_it      {"questions": [str]}
vocabulary_strip   {"words": [str], "instruction": str}
language_bridge    {"languages": ["Twi", "Ewe", "Ga", "Dagbani"], "rows": [{"English": str, "Twi": str, "Ewe": str, "Ga": str, "Dagbani": str}], "instruction": str}
phonics            {"stage": int, "group": int, "sounds": [{"grapheme": "s", "phoneme": "/s/", "examples": [str, str, str]}],
                    "blend_items": [str], "activity": str}
                   A blend item is either "s – a – t → sat" or just "sat".
reading            {"type": "picture_supported_text" | "match_and_read", "sentences": [str], "sight_words": [str],
                    "decodable_words": [str], "match_words": [str] (optional)}
grammar            {"focus": "<Focus> — <angle>", "explanation": str, "examples": {"<category>": [str]}, "your_turn": [str]}
writing            {"tasks": [str]}
anansi_says        {"text": str}
what_you_will_make {"text": str}
project_steps      {"steps": [str]}   each step starts with a bold verb: "**Look** at ..."
you_will_need      {"items": [str]}
do_it_your_way     {"modes": [the six response modes, exactly as in the blueprint], "text": str}
teacher_note       {"points": [str]}
assessment_intro   {"text": str}
assessment_tasks   {"tasks": [{"area": "oral_or_sign" | "phonics" | "grammar" | "writing", "title": str, "prompt": str}]}
rubric             {"levels": ["Beginning", "Approaching", "Meeting", "Exceeding"],
                    "rows": [{"skill": str, "Beginning": str, "Approaching": str, "Meeting": str, "Exceeding": str}]}
"""

HARD_RULES = """\
An automatic checker reads every unit before a person does. It rejects a unit when:
- a block is missing a required field from the blueprint's block_types, or a list is outside its min/max/exact item count
  (talk_about_it 2–4 questions, vocabulary_strip 5–6 words, project_steps 4–6 steps, teacher_note 3–5 points,
  assessment_tasks exactly 4 tasks, language_bridge 3–4 rows)
- anansi_says is over 45 words (never start it with "Anansi says:"; the book draws the spider and speech bubble)
- language_bridge languages are not exactly Twi, Ewe, Ga, Dagbani; rubric levels are not exactly the four levels;
  do_it_your_way modes are not exactly the six response modes
- the grammar focus does not name the theme's grammar focus from the grammar_roadmap
- Unit 1's oral text is not a song, Unit 2's not a rhyme, Unit 3's not a conversation; a unit has no teacher_note
- Unit 1's teacher notes do not cite a content standard code such as B1.1.1
- a phonics blend item uses a letter that has not been taught yet
- a reading sentence contains a word that is not decodable with taught letters and is not listed in sight_words
  or in this unit's vocabulary_strip (check every word letter by letter; avoid apostrophes in reading sentences)
- no vocabulary_strip word appears again in the unit's reading, grammar or writing blocks
- the assessment does not check the theme project by name
"""


def system_prompt(blueprint):
    return f"""You are the writing engine of Syllabus Core. You write units of a pupil book for Ghanaian Basic 1 learners (about six years old), following the book blueprint below exactly. Each unit you write goes to an automatic quality checker, then to the curriculum lead for approval.

Voice: short, concrete sentences a six-year-old can hear and repeat. Teacher notes are practical and specific: what to do, what to watch for, how to adapt. Settings are Ghanaian: the compound, the market, the farm, the school garden, local foods, plants, animals and place names. Use the recurring cast (Ama, Kojo, Esi, Yaw, Abena). British spelling. Anansi the spider is the guide: warm, a little proud, and fond of his old stories.

Inclusion is part of the design, not an extra. Show a wheelchair user somewhere in the theme's illustrations. Every set of teacher notes includes an adaptation (low vision, deaf or non-speaking learners, fine-motor difficulty, or a helper). Every task accepts any response mode.

Language bridge: give common everyday words you are confident about in Twi, Ewe, Ga and Dagbani. A native speaker checks every row before print.

<blueprint>
{json.dumps(blueprint, ensure_ascii=False, indent=1)}
</blueprint>

<block_shapes>
{BLOCK_SHAPES}</block_shapes>

<checker_rules>
{HARD_RULES}</checker_rules>

Answer with the unit as one JSON object inside a ```json fenced block, and nothing after it."""


def grammar_focus(blueprint, theme_no):
    """Same lookup the checker uses: the first roadmap entry that lists this theme."""
    return next(g for g in blueprint["grammar_roadmap"] if theme_no in g["themes"])


def stage1_letters(blueprint, theme_no, group):
    """(new letters for this group, all letters taught up to and including it), or None after Stage 1."""
    s1 = blueprint["phonics_pathway"]["stage1"]
    if theme_no not in s1["themes"]:
        return None
    per = s1["groups_of"]
    absolute = (theme_no - 1) * 3 + group
    order = s1["order"]
    return order[(absolute - 1) * per: absolute * per], order[: absolute * per]


def _layout(blueprint, name):
    return blueprint["page_layouts"][name]


def unit_prompt(blueprint, theme_bp, kind, number, pages, standards, done_units):
    t = theme_bp["number"]
    g = grammar_focus(blueprint, t)
    parts = [
        f"Theme {t}: {theme_bp['title']} (pages {theme_bp['pages'][0]}–{theme_bp['pages'][1]}). "
        f"Grammar focus: {g['focus']} ({g['label']}). Phonics: {theme_bp['phonics']}."
    ]
    if standards:
        parts.append("Curriculum content standards for this theme:\n" + "\n".join(f"- {s}" for s in standards))

    page_text = f"page {pages[0]}" if pages[0] == pages[1] else f"pages {pages[0]}–{pages[1]}"

    if kind == "project":
        layout = _layout(blueprint, "project")
        parts.append(
            f"Write the theme project page ({page_text}). Blocks in this order: {', '.join(layout['blocks'])}.\n"
            "Choose a project title. The project runs through the whole theme: every unit adds to it, "
            "and the assessment checks it."
        )
    elif kind == "unit":
        form = blueprint["oral_language_rotation"][f"unit_{number}"]
        if number in (1, 2):
            layout = _layout(blueprint, "two_page_unit")
            order = (f"Left page blocks, in order: {', '.join(layout['left_page']['blocks'])}.\n"
                     f"Right page blocks, in order: {', '.join(layout['right_page']['blocks'])}.\n"
                     'Put "page": "left" or "page": "right" on every block.')
        else:
            layout = _layout(blueprint, "one_page_unit")
            order = f"Blocks in this order: {', '.join(layout['blocks'])}."
        parts.append(f"Write Unit {number} ({page_text}). Choose the unit title. The oral text is a {form}.\n{order}")

        letters = stage1_letters(blueprint, t, number)
        if letters:
            new, taught = letters
            parts.append(
                f"Phonics: stage 1, group {number}. New sounds: {' '.join(new)}. "
                f"Letters taught so far: {' '.join(taught)}.\n"
                "Every blend item uses only the taught letters. In reading, every word in every sentence is "
                "spelled only with the taught letters, or is listed in sight_words, or is one of this unit's "
                "vocabulary_strip words. List common words such as I, the, is, this in sight_words when they "
                "are not decodable yet."
            )
        else:
            parts.append("Phonics follows the blueprint's phonics_pathway for this theme.")
        parts.append(f'The grammar focus names {g["focus"]}, for example "{g["focus"]} — <angle>".')
        if number == 1:
            code = standards[0].split(" ")[0] if standards else "the content standard code"
            parts.append(f'The first point of the first teacher note cites the content standard: "CONTENT STANDARD: {code} — ...".')
        parts.append("Recycle at least one vocabulary_strip word in the reading, grammar or writing blocks. "
                     "One writing task adds to the theme project.")
    else:
        layout = _layout(blueprint, "assessment")
        parts.append(
            f"Write the theme assessment ({page_text}). Blocks in this order: {', '.join(layout['blocks'])}.\n"
            "Exactly four tasks, with areas oral_or_sign, phonics, grammar and writing, in that order. "
            "Assess the sounds, words and grammar taught in this theme. One task asks the learner to show the "
            "theme project, named by its title, and the rubric has a Project row. The teacher notes say that a "
            "signed, pointed or card-placed answer scores the same as a spoken one, and that learners at "
            "Beginning on blending carry this theme's phonics groups into the next theme."
        )

    if done_units:
        parts.append(
            "The theme so far. Keep titles, characters, vocabulary and the project consistent with it, "
            "and do not repeat an illustration idea:\n```json\n"
            + json.dumps(done_units, ensure_ascii=False, indent=1) + "\n```"
        )
    parts.append('Answer with the unit as one JSON object: {"title": ..., "blocks": [...]}.')
    return "\n\n".join(parts)


def repair_prompt(problems):
    listed = "\n".join(f"- {p}" for p in problems)
    return (f"The quality checker found these problems in your unit:\n{listed}\n\n"
            "Fix every one. Keep the rest of the unit as it is unless a fix needs a change. "
            "Answer with the whole corrected unit as one JSON object in a ```json block.")
