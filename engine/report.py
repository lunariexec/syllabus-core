"""
Builds the demo page for a run: one self-contained HTML file with the run data inside.

Usage (to rebuild a page from an earlier run):
  python engine/report.py runs/<run folder>/run.json
"""
import json
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent / "demo_template.html"


def build(run, out_path):
    data = json.dumps(run, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("__RUN_DATA__", data)
    Path(out_path).write_text(html, encoding="utf-8")


if __name__ == "__main__":
    run_file = Path(sys.argv[1])
    with open(run_file, encoding="utf-8") as f:
        build(json.load(f), run_file.parent / "demo.html")
    print(f"Demo page: {run_file.parent / 'demo.html'}")
