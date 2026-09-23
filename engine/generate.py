"""
Generation engine, v0.1: blueprint + curriculum standards -> a whole theme.

Each unit (project, Units 1–3, assessment) is written by Claude, read by the quality
checker in tests/check_against_blueprint.py, and sent back with the checker's problems
until it passes or runs out of repair rounds. The finished theme is scored against the
same checker as the approved book, and a demo page is written next to it.

Usage:
  python engine/generate.py --theme 1
  python engine/generate.py --theme 1 --effort high --max-repairs 3

Needs ANTHROPIC_API_KEY (or an `ant auth login` profile). Writes
runs/<timestamp>-theme<N>/ with theme<N>.generated.json, run.json and demo.html.
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from check_against_blueprint import check_theme  # noqa: E402

import report  # noqa: E402
from prompts import repair_prompt, system_prompt, unit_prompt  # noqa: E402

# The order of a theme, and where each part sits within a 7-page theme.
SLOTS = [("project", 0, (0, 0)), ("unit", 1, (1, 2)), ("unit", 2, (3, 4)),
         ("unit", 3, (5, 5)), ("assessment", 99, (6, 6))]

# USD per million tokens: input, output. Cache writes cost 1.25x input, reads 0.1x.
PRICES = {"claude-opus-5": (5.00, 25.00), "claude-opus-5-5": (4.00, 20.00), "claude-sonnet-5": (2.00, 10.00)}

LIST_FIELDS = {"lines", "questions", "words", "rows", "sounds", "blend_items", "sentences", "sight_words",
               "your_turn", "tasks", "steps", "items", "modes", "points", "levels", "languages"}
STRING_LISTS = {"lines", "questions", "words", "blend_items", "sentences", "sight_words", "points", "steps"}


class GenerationError(Exception):
    pass


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def slot_label(kind, number):
    return {"project": "Project", "assessment": "Assessment"}.get(kind, f"Unit {number}")


def extract_json(text):
    m = re.search(r"```json\s*(\{.*\})\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    obj = json.loads(raw)
    if isinstance(obj, dict) and "blocks" not in obj and isinstance(obj.get("unit"), dict):
        obj = obj["unit"]
    if not isinstance(obj, dict):
        raise ValueError("expected a JSON object")
    return obj


def shape_problems(blueprint, unit):
    """Problems the checker would crash on rather than report, so they are caught first."""
    where = f"Unit {unit['number']} ({unit.get('title')})"
    if not isinstance(unit.get("title"), str):
        return [f"{where}: the unit needs a title"]
    if not isinstance(unit.get("blocks"), list) or not unit["blocks"]:
        return [f"{where}: 'blocks' must be a non-empty list"]
    problems = []
    for i, b in enumerate(unit["blocks"]):
        if not isinstance(b, dict) or not isinstance(b.get("type"), str) or not isinstance(b.get("content"), dict):
            problems.append(f"{where}: block {i + 1} needs a 'type' and a 'content' object")
            continue
        spec = blueprint["block_types"].get(b["type"])
        if spec is None:
            problems.append(f"{where}: unknown block type '{b['type']}'")
            continue
        content = b["content"]
        missing = [f for f in spec["required"] if f not in content]
        if missing:
            problems.append(f"{where}: {b['type']} missing {missing}")
        for field, value in content.items():
            if field in LIST_FIELDS and not isinstance(value, list):
                problems.append(f"{where}: {b['type']}.{field} must be a list")
            elif field in STRING_LISTS and not all(isinstance(v, str) for v in value):
                problems.append(f"{where}: {b['type']}.{field} must be a list of strings")
    return problems


def unit_problems(blueprint, theme_meta, unit):
    problems = shape_problems(blueprint, unit)
    if problems:
        return problems
    try:
        _, found = check_theme(blueprint, dict(theme_meta, units=[unit]))
    except Exception as e:  # the checker assumes well-formed content; report rather than stop the run
        return [f"Unit {unit['number']} ({unit['title']}): checker could not read this unit ({type(e).__name__}: {e})"]
    # The checker judges a whole theme; with one unit, drop the whole-theme checks that don't apply yet.
    return [p for p in found
            if not p.startswith("Theme sequence")
            and (unit["kind"] == "assessment" or not p.startswith("Assessment should"))]


class Engine:
    def __init__(self, args):
        self.args = args
        self.blueprint = load_json(args.blueprint)
        self.theme_bp = next((t for t in self.blueprint["themes"] if t["number"] == args.theme), None)
        if self.theme_bp is None:
            raise SystemExit(f"Theme {args.theme} is not in the blueprint.")
        start, end = self.theme_bp["pages"]
        if end - start != 6:
            raise SystemExit(f"Theme {args.theme} spans {end - start + 1} pages; v0.1 lays out 7-page themes only.")
        self.standards = load_json(args.standards)["standards"] if args.standards else []
        self.system = system_prompt(self.blueprint)
        self.client = anthropic.Anthropic()
        self.events = []
        self.usage = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}

    def call(self, messages):
        started = time.monotonic()
        with self.client.beta.messages.stream(
            model=self.args.model,
            max_tokens=64000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={"effort": self.args.effort},
            system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
        ) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            raise GenerationError(f"Claude declined the request ({msg.stop_details}).")
        if msg.stop_reason == "max_tokens":
            raise GenerationError("The response hit max_tokens before the unit was finished.")
        for k in self.usage:
            self.usage[k] += getattr(msg.usage, k, 0) or 0
        text = "".join(b.text for b in msg.content if b.type == "text")
        return msg, text, round(time.monotonic() - started, 1)

    def produce_unit(self, kind, number, offsets, done_units):
        start = self.theme_bp["pages"][0]
        pages = [start + offsets[0], start + offsets[1]]
        label = slot_label(kind, number)
        theme_meta = {"number": self.theme_bp["number"], "title": self.theme_bp["title"]}
        messages = [{"role": "user", "content": unit_prompt(
            self.blueprint, self.theme_bp, kind, number, pages, self.standards, done_units)}]
        best = None
        for attempt in range(1, self.args.max_repairs + 2):
            action = "generate" if attempt == 1 else "repair"
            print(f"  {label}: {'writing' if attempt == 1 else f'repair round {attempt - 1}'} …", flush=True)
            msg, text, seconds = self.call(messages)
            messages.append({"role": "assistant", "content": msg.content})
            try:
                unit = extract_json(text)
            except (ValueError, json.JSONDecodeError) as e:
                unit, problems = None, [f"{label}: the answer was not one valid JSON object ({e})"]
            else:
                unit = {"number": number, "kind": kind, "title": unit.get("title"), "pages": pages,
                        **({"standard_codes": [s.split(" ")[0] for s in self.standards]}
                           if kind == "unit" and number == 1 and self.standards else {}),
                        "blocks": unit.get("blocks")}
                problems = unit_problems(self.blueprint, theme_meta, unit)
                best = unit
            self.events.append({"slot": label, "unit": number, "attempt": attempt, "action": action,
                                "problems": problems, "seconds": seconds, "model": msg.model,
                                "output_tokens": msg.usage.output_tokens})
            print(f"    {'passed' if not problems else f'{len(problems)} problem(s)'} ({seconds}s)", flush=True)
            for p in problems:
                print(f"      ✗ {p}")
            if not problems or attempt > self.args.max_repairs:
                break
            messages.append({"role": "user", "content": repair_prompt(problems)})
        if best is None:
            raise GenerationError(f"{label}: no usable unit after {self.args.max_repairs + 1} attempts.")
        return best

    def run(self):
        t = self.theme_bp
        print(f"Theme {t['number']}: {t['title']} — {self.args.model}, effort {self.args.effort}")
        started = time.monotonic()
        units = []
        for kind, number, offsets in SLOTS:
            units.append(self.produce_unit(kind, number, offsets, units))
        theme = {"number": t["number"], "title": t["title"], "pages": t["pages"], "units": units}
        seconds = round(time.monotonic() - started, 1)

        passes, problems = check_theme(self.blueprint, theme)
        score = {"generated": {"passes": passes, "total": passes + len(problems), "problems": problems}}
        gold = None
        if self.args.gold and Path(self.args.gold).exists():
            gold = load_json(self.args.gold)["theme"]
            g_passes, g_problems = check_theme(self.blueprint, gold)
            score["gold"] = {"passes": g_passes, "total": g_passes + len(g_problems), "problems": g_problems}

        price = PRICES.get(self.args.model)
        cost = None
        if price:
            u = self.usage
            cost = round((u["input_tokens"] * price[0] + u["cache_creation_input_tokens"] * price[0] * 1.25
                          + u["cache_read_input_tokens"] * price[0] * 0.1 + u["output_tokens"] * price[1]) / 1e6, 2)

        created = datetime.now(timezone.utc)
        out_dir = ROOT / "runs" / f"{created:%Y%m%d-%H%M%S}-theme{t['number']}"
        out_dir.mkdir(parents=True)
        generated_file = {"source": f"Generated by engine v0.1 ({self.args.model}), {created:%d %b %Y}",
                          "blueprint": f"{self.blueprint['blueprint']}.v{self.blueprint['version']}",
                          "theme": theme}
        with open(out_dir / f"theme{t['number']}.generated.json", "w", encoding="utf-8") as f:
            json.dump(generated_file, f, ensure_ascii=False, indent=2)

        run = {"engine": "Syllabus Core engine v0.1", "created": created.isoformat(timespec="seconds"),
               "model": self.args.model, "effort": self.args.effort, "max_repairs": self.args.max_repairs,
               "blueprint": generated_file["blueprint"], "blueprint_title": self.blueprint["title"],
               "theme_colour": t["colour"], "standards": self.standards,
               "events": self.events, "seconds": seconds, "usage": self.usage, "cost_usd": cost,
               "score": score, "generated": theme, "gold": gold}
        with open(out_dir / "run.json", "w", encoding="utf-8") as f:
            json.dump(run, f, ensure_ascii=False, indent=2)
        report.build(run, out_dir / "demo.html")

        s = score["generated"]
        print(f"\nChecker: {s['passes']}/{s['total']} passed for the AI draft", end="")
        if "gold" in score:
            print(f" (approved book: {score['gold']['passes']}/{score['gold']['total']})", end="")
        print(f"\nTime {seconds}s · {self.usage['output_tokens']:,} output tokens"
              + (f" · about ${cost:.2f}" if cost is not None else ""))
        print(f"Demo page: {out_dir / 'demo.html'}")
        return 0 if not problems else 1


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Generate a theme from the blueprint and check it.")
    p.add_argument("--theme", type=int, default=1)
    p.add_argument("--blueprint", default=str(ROOT / "blueprints" / "nacca-basic1-english.v1.json"))
    p.add_argument("--standards", default=str(ROOT / "engine" / "inputs" / "nacca-basic1-english.theme1.standards.json"))
    p.add_argument("--gold", default=str(ROOT / "gold" / "nacca-basic1-english" / "theme1.json"),
                   help="Approved theme to compare against (skipped if the file does not exist)")
    p.add_argument("--model", default="claude-opus-5")
    p.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--max-repairs", type=int, default=3)
    args = p.parse_args()
    if args.theme != 1 and args.gold.endswith("theme1.json"):
        args.gold = str(ROOT / "gold" / "nacca-basic1-english" / f"theme{args.theme}.json")
    if args.theme != 1 and args.standards.endswith("theme1.standards.json"):
        args.standards = None
    try:
        sys.exit(Engine(args).run())
    except anthropic.AuthenticationError:
        sys.exit("No valid API key. Set ANTHROPIC_API_KEY and run again.")
    except anthropic.RateLimitError:
        sys.exit("Rate limited by the API. Wait a minute and run again.")
    except anthropic.APIStatusError as e:
        sys.exit(f"API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        sys.exit("Could not reach the API. Check the internet connection.")
    except GenerationError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
