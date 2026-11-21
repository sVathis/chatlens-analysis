#!/usr/bin/env python3
"""List the findings of a Skype or Viber analysis in the order to read them.

Reads the ``findings.json`` of one or more analysis output folders (``skype-analysis-1/``,
``viber-analysis-22/``, ...) and prints the findings sorted by severity, then category,
then the model's confidence. Findings that tie are listed run by run (in the order the folders
are given) and then by time. It works offline and never calls an API.

By default it prints no message text: only severity, category, confidence, time, message
IDs and the link to open the message (the ChatLens link, or the ``history.html`` anchor when
the run was made without ``--chatlens-url``). Add ``--quotes`` or ``--reasons`` to show the
model's text as well. The order is a reading order, not an assessment: the labels are the
model's own, and every finding needs a human review of the message and its context.
"""
import argparse
import csv
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path

SEVERITIES = ("high", "medium", "low")
CONFIDENCES = ("high", "medium", "low")
# Reading order, from the categories that most need a look to the mildest.
CATEGORIES = ("threat", "direct_insult", "personal_accusation", "belittling",
              "hostile_criticism", "sarcasm", "other")
# Characters that can reorder or hide text in a terminal.
BIDI_CONTROLS = set("‪‫‬‭‮⁦⁧⁨⁩")


class TriageError(ValueError):
    """An expected input problem, shown as one line."""


def rank(value, order):
    """Position of ``value`` in ``order``; unknown values sort last."""
    return order.index(value) if value in order else len(order)


def one_line(value, limit=200):
    """``value`` as one safe line for a terminal: control and direction characters become
    spaces, lone surrogates become U+FFFD, runs of whitespace collapse, and text longer than
    ``limit`` characters is cut (``limit=None`` cuts nothing)."""
    pieces = []
    for char in str(value):
        category = unicodedata.category(char)
        if category == "Cs":
            pieces.append("�")
        elif category in ("Cc", "Zl", "Zp") or char in BIDI_CONTROLS:
            pieces.append(" ")
        else:
            pieces.append(char)
    cleaned = " ".join("".join(pieces).split())
    if limit is None or len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def csv_safe(value):
    """Stop spreadsheet software from reading a cell as a formula."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def text_of(item, name):
    value = item.get(name)
    return "" if value is None else value if isinstance(value, str) else str(value)


def findings_file(raw):
    path = Path(raw)
    return path / "findings.json" if path.is_dir() else path


def read_findings(file):
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TriageError(f"Cannot read {file} ({type(exc).__name__})") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TriageError(f"{file} is not valid JSON") from exc
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise TriageError(f"{file} is not a list of findings")
    return data


def make_record(item, order, run, base):
    if "viber_id" in item:
        source, message_id = "viber", text_of(item, "viber_id")
    elif "skype_id" in item:
        source, message_id = "skype", text_of(item, "skype_id")
    else:
        source, message_id = "unknown", ""
    link = text_of(item, "chatlens_url")
    if not link and text_of(item, "source_url"):
        link = str(base / text_of(item, "source_url"))
    return {
        "order": order, "run": run, "source": source,
        "severity": text_of(item, "severity"), "confidence": text_of(item, "confidence"),
        "category": text_of(item, "category"), "time": text_of(item, "time"),
        "message_id": message_id, "key": text_of(item, "message_key"), "link": link,
        "quote": text_of(item, "quote"), "quote_match": text_of(item, "quote_match"),
        "reason": text_of(item, "reason"), "caveat": text_of(item, "caveat"),
    }


def collect(paths):
    """All findings of all runs as records, and a description of each run."""
    records, runs = [], []
    for order, raw in enumerate(paths):
        file = findings_file(raw)
        data = read_findings(file)
        run = file.resolve().parent.name or str(file.parent)
        built = [make_record(item, order, run, file.parent) for item in data]
        sources = sorted({r["source"] for r in built}) or ["unknown"]
        runs.append({"run": run, "source": "/".join(sources), "count": len(built)})
        records.extend(built)
    return records, runs


def select(records, severities=None, categories=None, min_confidence=None, limit=None):
    chosen = [
        r for r in records
        if (not severities or r["severity"] in severities)
        and (not categories or r["category"] in categories)
        and (not min_confidence
             or rank(r["confidence"], CONFIDENCES) <= rank(min_confidence, CONFIDENCES))
    ]
    chosen.sort(key=lambda r: (rank(r["severity"], SEVERITIES), rank(r["category"], CATEGORIES),
                               rank(r["confidence"], CONFIDENCES), r["order"], r["time"], r["key"]))
    return chosen[:limit] if limit else chosen


def ordered(values, order):
    """The values present, known ones in ``order`` first and unknown ones after."""
    present = set(values)
    return [v for v in order if v in present] + sorted(present - set(order))


def summary_lines(records):
    """A severity-by-category table of ``records``."""
    severities = ordered((r["severity"] for r in records), SEVERITIES)
    categories = ordered((r["category"] for r in records), CATEGORIES)
    counts = Counter((r["category"], r["severity"]) for r in records)
    width = max([len(one_line(c or "?", 40)) for c in categories] + [5])
    lines = [" " * width + "".join(f"{one_line(s or '?', 7):>8}" for s in severities) + f"{'total':>8}"]
    for category in categories:
        row = [counts[(category, s)] for s in severities]
        lines.append(f"{one_line(category or '?', 40):<{width}}"
                     + "".join(f"{n:>8}" for n in row) + f"{sum(row):>8}")
    totals = [sum(counts[(c, s)] for c in categories) for s in severities]
    lines.append(f"{'total':<{width}}" + "".join(f"{n:>8}" for n in totals) + f"{len(records):>8}")
    return lines


def print_text(selected, total, runs, args):
    described = ", ".join(f"{one_line(r['run'], 60)} ({r['source']}, {r['count']})" for r in runs)
    print(f"{len(selected)} of {total} findings from {described}")
    if not selected:
        print("No findings match.")
        return
    print()
    print("\n".join(summary_lines(selected)))
    if args.summary_only:
        return
    print("\nReading order (severity, category, confidence):")
    for number, r in enumerate(selected, 1):
        print(f"{number:>5}  {one_line(r['severity'] or '?', 8):<8}"
              f"{one_line(r['confidence'] or '?', 8) + ' conf':<12}"
              f"{one_line(r['category'] or '?', 24):<25}{one_line(r['time'], 30):<31}"
              f"id={one_line(r['message_id'] or '-', 40)}  {one_line(r['key'], 20)}  "
              f"[{one_line(r['run'], 40)}]")
        if args.links and r["link"]:
            print(f"       {one_line(r['link'], 400)}")
        if args.quotes and r["quote"]:
            note = "  (whitespace/Unicode form normalised)" if r["quote_match"] == "normalized" else ""
            print(f'       "{one_line(r["quote"], args.text_length)}"{note}')
        if args.reasons and r["reason"]:
            print(f"       reason: {one_line(r['reason'], args.text_length)}")
        if args.reasons and r["caveat"]:
            print(f"       caveat: {one_line(r['caveat'], args.text_length)}")


def print_csv(selected):
    """Labels, IDs and links only: no message text, so the file is safe to open anywhere."""
    columns = ["order", "run", "source", "severity", "confidence", "category", "time",
               "message_id", "message_key", "quote_match", "link"]
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(columns)
    for number, r in enumerate(selected, 1):
        writer.writerow([number] + [csv_safe(one_line(r[name], None)) for name in
                                    ("run", "source", "severity", "confidence", "category", "time",
                                     "message_id", "key", "quote_match", "link")])


def choice_list(allowed, option):
    def parse(value):
        items = [v.strip() for v in value.split(",") if v.strip()]
        if not items or any(v not in allowed for v in items):
            raise argparse.ArgumentTypeError(f"{option} takes a comma-separated list of: "
                                             + ", ".join(allowed))
        return items
    return parse


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", metavar="OUTPUT_DIR",
                        help="an analysis output folder, or a findings.json file; several may be given")
    parser.add_argument("--severity", type=choice_list(SEVERITIES, "--severity"),
                        help="only these severities, e.g. high,medium")
    parser.add_argument("--category", type=choice_list(CATEGORIES, "--category"),
                        help="only these categories, e.g. threat,direct_insult")
    parser.add_argument("--min-confidence", choices=CONFIDENCES,
                        help="only findings with at least this confidence")
    parser.add_argument("--limit", type=int, help="print only the first N findings of the order")
    parser.add_argument("--format", choices=("text", "csv"), default="text",
                        help="csv has labels, IDs and links only, never message text")
    parser.add_argument("--summary-only", action="store_true", help="print only the counts table")
    parser.add_argument("--no-links", dest="links", action="store_false", help="leave out the links")
    parser.add_argument("--quotes", action="store_true", help="also show each finding's quote (text format)")
    parser.add_argument("--reasons", action="store_true",
                        help="also show the model's reason and caveat (text format)")
    parser.add_argument("--text-length", type=int, default=240, metavar="N",
                        help="longest quote or reason shown, in characters (default 240)")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.limit is not None and args.limit < 1:
        sys.exit("error: --limit must be 1 or more")
    if args.text_length < 20:
        sys.exit("error: --text-length must be at least 20")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    try:
        records, runs = collect(args.paths)
    except TriageError as exc:
        sys.exit(f"error: {exc}")
    selected = select(records, args.severity, args.category, args.min_confidence, args.limit)
    if args.format == "csv":
        print_csv(selected)
    else:
        print_text(selected, len(records), runs, args)


if __name__ == "__main__":
    main()
