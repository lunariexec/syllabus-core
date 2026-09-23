"""
Quality checks: does a theme (hand-made or AI-generated) obey the blueprint?

This is the first version of the automatic checker that every generated unit
will pass through before a human reviewer sees it.

Usage: python tests/check_against_blueprint.py blueprints/X.json gold/.../theme1.json
"""
import json
import re
import sys


def words(text):
    return re.findall(r"[A-Za-z’']+", text)


def taught_graphemes(blueprint, theme_no, group):
    """Letters taught up to and including this group (Stage 1 = 3 letters per group)."""
    order = blueprint["phonics_pathway"]["stage1"]["order"]
    per = blueprint["phonics_pathway"]["stage1"]["groups_of"]
    # Theme 1 holds groups 1-3, Theme 2 groups 4-6, Theme 3 groups 7-9.
    absolute_group = (theme_no - 1) * 3 + group
    return set(order[: absolute_group * per])


def check_theme(bp, theme):
    problems, passes = [], 0
    types = bp["block_types"]

    def ok(cond, msg):
        nonlocal passes
        if cond:
            passes += 1
        else:
            problems.append(msg)

    # Theme shape
    kinds = [u["kind"] for u in theme["units"]]
    ok(kinds == ["project", "unit", "unit", "unit", "assessment"],
       f"Theme sequence should be project, 3 units, assessment — got {kinds}")

    oral_forms = {1: "song", 2: "rhyme", 3: "conversation"}
    grammar_focus = next(g["focus"] for g in bp["grammar_roadmap"] if theme["number"] in g["themes"])

    for unit in theme["units"]:
        where = f"Unit {unit['number']} ({unit['title']})"
        blocks = unit["blocks"]
        btypes = [b["type"] for b in blocks]

        # Every block type exists in the blueprint and has its required fields
        for b in blocks:
            spec = types.get(b["type"])
            ok(spec is not None, f"{where}: unknown block type '{b['type']}'")
            if not spec:
                continue
            missing = [f for f in spec["required"] if f not in b["content"]]
            ok(not missing, f"{where}: {b['type']} missing {missing}")

            items = next((b["content"][k] for k in ("words", "questions", "steps", "points", "tasks")
                          if k in b["content"] and isinstance(b["content"][k], list)), None)
            if items is not None:
                if "min_items" in spec:
                    ok(len(items) >= spec["min_items"], f"{where}: {b['type']} has {len(items)} items, min {spec['min_items']}")
                if "max_items" in spec:
                    ok(len(items) <= spec["max_items"], f"{where}: {b['type']} has {len(items)} items, max {spec['max_items']}")
                if "exact_items" in spec:
                    ok(len(items) == spec["exact_items"], f"{where}: {b['type']} needs exactly {spec['exact_items']} items")

            if b["type"] == "anansi_says":
                n = len(words(b["content"]["text"]))
                ok(n <= spec["max_words"], f"{where}: Anansi text is {n} words (max {spec['max_words']})")

            if b["type"] == "hero_illustration":
                ok(bool(b["content"].get("alt_text")), f"{where}: illustration needs alt text")

            if b["type"] == "language_bridge":
                ok(b["content"]["languages"] == spec["languages"], f"{where}: bridge languages should be {spec['languages']}")
                ok(spec["min_rows"] <= len(b["content"]["rows"]) <= spec["max_rows"], f"{where}: bridge table row count")

            if b["type"] == "rubric":
                ok(b["content"]["levels"] == spec["levels"], f"{where}: rubric levels must be {spec['levels']}")

            if b["type"] == "do_it_your_way":
                ok(b["content"]["modes"] == bp["global_rules"]["response_modes"], f"{where}: response modes incomplete")

            if b["type"] == "grammar":
                ok(grammar_focus.lower().rstrip("s") in b["content"]["focus"].lower(),
                   f"{where}: grammar focus '{b['content']['focus']}' should be {grammar_focus}")

        if unit["kind"] == "unit":
            n = unit["number"]
            oral = next((b for b in blocks if b["type"] == "oral_text"), None)
            ok(oral is not None and oral["content"]["form"] == oral_forms[n],
               f"{where}: oral text should be a {oral_forms[n]}")
            ok("teacher_note" in btypes, f"{where}: needs a teacher note")

            # First unit's teacher note cites a content standard
            if n == 1:
                notes = " ".join(p for b in blocks if b["type"] == "teacher_note" for p in b["content"]["points"])
                ok(re.search(r"B\d\.\d+\.\d+", notes) is not None, f"{where}: teacher note must cite a content standard code")

            # Phonics uses only taught letters; reading is decodable or sight words
            ph = next((b for b in blocks if b["type"] == "phonics"), None)
            if ph:
                taught = taught_graphemes(bp, theme["number"], ph["content"]["group"])
                for item in ph["content"]["blend_items"]:
                    target = item.split("→")[-1].strip()
                    bad = set(target.replace("–", "").replace(" ", "")) - taught
                    ok(not bad, f"{where}: blend item '{target}' uses untaught letters {sorted(bad)}")

                rd = next((b for b in blocks if b["type"] == "reading"), None)
                if rd:
                    sight = {w.lower() for w in rd["content"]["sight_words"]}
                    # Picture-supported words: taught in this unit's vocabulary strip with a picture
                    if vocab_words := next((b["content"]["words"] for b in blocks if b["type"] == "vocabulary_strip"), None):
                        sight |= {w.lower() for w in vocab_words}
                    for s in rd["content"]["sentences"]:
                        for w in words(s):
                            wl = w.lower()
                            decodable = set(wl) <= taught
                            ok(decodable or wl in sight,
                               f"{where}: reading word '{w}' is not decodable yet (untaught letters: {sorted(set(wl) - taught)}) and not a sight or picture word")

        # Vocabulary recycling: at least one vocab word reappears later in the unit
        vocab = next((b for b in blocks if b["type"] == "vocabulary_strip"), None)
        if vocab:
            later = json.dumps([b["content"] for b in blocks if b["type"] in ("reading", "grammar", "writing")]).lower()
            reused = [w for w in vocab["content"]["words"] if w.lower() in later]
            ok(len(reused) >= 1, f"{where}: no vocabulary words are recycled in reading/grammar/writing")

    # Assessment checks the project
    assess = theme["units"][-1]
    ok("project" in json.dumps(assess).lower() or "garden" in json.dumps(assess).lower(),
       "Assessment should check the theme project")

    return passes, problems


if __name__ == "__main__":
    bp = json.load(open(sys.argv[1]))
    theme = json.load(open(sys.argv[2]))["theme"]
    passes, problems = check_theme(bp, theme)
    total = passes + len(problems)
    print(f"Theme {theme['number']}: {theme['title']}")
    print(f"  {passes}/{total} checks passed ({100 * passes / total:.1f}%)")
    for p in problems:
        print("  ✗", p)
    sys.exit(1 if problems else 0)
