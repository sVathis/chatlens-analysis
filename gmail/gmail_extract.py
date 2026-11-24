#!/usr/bin/env python3
"""Extract the email threads with one sender from a Google Takeout mbox.

Google Takeout (Mail) writes one ``.mbox`` file with every exported email once. Gmail's
thread ID (``X-GM-THRID``) groups them back into conversations. This script:

- lists the senders and their email counts (``--list-senders``);
- keeps the threads in which the selected sender wrote at least one email (all threads with
  ``--all-threads``), with every email of those threads, yours included, as context;
- keeps only the new text of each email: earlier messages quoted in a reply ("On ... wrote:",
  "Στις ... έγραψε:", Outlook's "From:/Sent:" block, ``>`` lines), forwarded messages,
  signatures and disclaimer blocks a sender repeats in many emails are removed, so the analysis
  reads each sentence once and quotes only what the email's own author wrote;
- writes ``conversation.json`` (read by ``gmail_analyze.py``) and ``conversation.md`` (a
  reading copy) into a private folder.

It runs locally and never uses the network. Attachments are not extracted.

    python gmail/gmail_extract.py Takeout.mbox --list-senders
    python gmail/gmail_extract.py Takeout.mbox --sender person@example.com
"""
from __future__ import annotations

import argparse
import email.policy
import email.utils
import hashlib
import json
import mailbox
import re
import sys
from collections import Counter
from datetime import timezone
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# pylint: disable-next=wrong-import-position
from extract_utils import ExtractionWriteError, safe_filename, write_outputs  # noqa: E402

FORMAT = "chatlens-gmail-extract"
VERSION = 1
SURROGATE = re.compile(r"[\ud800-\udfff]")


class ExtractError(ValueError):
    """An expected input problem, shown as one line."""


# ---------------------------------------------------------------- text helpers


def clean(value):
    """``value`` as text without lone surrogates.

    The email parser keeps undecodable header bytes as escaped surrogates; they are turned
    back into bytes and read as UTF-8 (what nearly every modern client sends), and anything
    still unreadable becomes U+FFFD.
    """
    if value is None:
        return ""
    text = str(value)
    if not SURROGATE.search(text):
        return text
    try:
        return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    except UnicodeEncodeError:
        return SURROGATE.sub("�", text)


def tidy(text):
    """Unix line ends, no trailing spaces, at most one blank line in a row, no blank edges."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")


# A reply's attribution line ("On Mon, 1 Jan 2024 at 10:00, Name <a@b> wrote:", "Στις Δευ 1 Ιαν
# 2024 στις 10:00, ο/η Name <a@b> έγραψε:", "Ο/Η Name <a@b> έγραψε στις ...:"). It may be wrapped
# over two lines. Ordinary sentences can also start with "On"/"Στις" and end with "wrote:"/
# "έγραψε:", so a line counts only with an attribution's structure: an email address, or a
# date with a year together with a time. See find_attribution().
ATTRIBUTION_START = re.compile(r"^[ \t]*(?:On|Στις)\b")
ATTRIBUTION_END = re.compile(r"(?:\bwrote:|έγραψε[^\n]*:)[ \t]*$")
ADDRESS = re.compile(r"[^\s<>@\"']+@[^\s<>@\"']+\.[^\s<>@\"']+")
DATE_WITH_YEAR = re.compile(r"\b(?:19|20)\d{2}\b|\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b")
TIME = re.compile(r"\b\d{1,2}:\d{2}\b")
# Yahoo's plain text can continue the attribution's line with the quoted text itself
INLINE_END = re.compile(r"(?:\bwrote:|έγραψε:)[ \t]+\S")


def is_attribution(block):
    return bool(ADDRESS.search(block)) or bool(DATE_WITH_YEAR.search(block) and TIME.search(block))


def find_attribution(text):
    """Offset of the first reply attribution in ``text``, or None."""
    lines = text.split("\n")
    offset = 0
    for i, line in enumerate(lines):
        for span in (1, 2):
            block_lines = lines[i:i + span]
            if len(block_lines) < span:
                break
            if span == 2 and ATTRIBUTION_END.search(block_lines[0]):
                break  # a one-line attribution, already tried
            block = "\n".join(block_lines)
            opens = ATTRIBUTION_START.match(block_lines[0]) or "<" in block and ADDRESS.search(block)
            if opens and ATTRIBUTION_END.search(block_lines[-1]) and is_attribution(block):
                return offset
        if ATTRIBUTION_START.match(line):
            inline = INLINE_END.search(line)
            if inline and inline.start() <= 300 and is_attribution(line[:inline.start()]):
                return offset
        offset += len(line) + 1
    return None


# Lines that start the quoted part of a reply or a forwarded message, besides the attribution.
# Everything from the first of them on was written by someone else (or earlier) and is removed.
QUOTE_HEADERS = [
    # Separator lines of replies and forwards (Outlook, Gmail, Yahoo; English and Greek). Yahoo
    # can continue the line with the forwarded header ("-----Προωθημένο μήνυμα----- Από: ...").
    re.compile(r"^[ \t]*-{2,}[ \t]*(?:Original Message|Αρχικό μήνυμα|Forwarded message|Προωθημένο μήνυμα)"
               r"[ \t]*-{2,}", re.M | re.I),
    re.compile(r"^[ \t]*Begin forwarded message:[ \t]*$", re.M),
    # Outlook's header block: "From: ..." followed by "Sent:" or "Date:" (or the Greek labels)
    re.compile(r"^[ \t]*\*?(?:From|Από):\*?[ \t][^\n]*\n[ \t]*\*?(?:Sent|Date|Στάλθηκε|Ημερομηνία):\*?[ \t]", re.M),
]
# A signature starts at a line that is exactly "-- " (or "--")
SIGNATURE = re.compile(r"^-- ?$", re.M)
# Footers phone apps add at the end of an email
FOOTER = re.compile(
    r"^(?:Sent from my \S.*|Sent from (?:Mail|Outlook|Yahoo Mail).*|Get Outlook for \S.*"
    r"|Στάλθηκε από (?:το|τη|την|το δικό μου) .*|Αποστολή από .*|Yahoo Mail: .{0,60})$",
    re.I,
)


def strip_quoted(text):
    """The email's own new text, and the number of characters removed.

    Removes the quoted earlier messages of a reply, forwarded messages, ``>`` quote lines,
    the signature and app footers. What is left is what the author wrote in this email.
    """
    text = tidy(text)
    original = len(text)
    cut = len(text)
    attribution = find_attribution(text)
    if attribution is not None:
        cut = attribution
    for pattern in QUOTE_HEADERS:
        found = pattern.search(text)
        if found and found.start() < cut:
            cut = found.start()
    text = text[:cut]
    signature = SIGNATURE.search(text)
    if signature:
        text = text[: signature.start()]
    lines = [line for line in text.split("\n") if not line.lstrip().startswith(">")]
    text = tidy("\n".join(trim_tail(lines)))
    return text, original - len(text)


def trim_tail(lines):
    """Drop blank lines, app footers and separator lines (Outlook's "_____") at the end."""
    lines = list(lines)
    while lines and (not lines[-1].strip() or FOOTER.match(lines[-1].strip())
                     or re.fullmatch(r"[_=\-]{5,}", lines[-1].strip())):
        lines.pop()
    return lines


# A signature or disclaimer without a "-- " line is recognised by repetition: a block of at least
# REPEATED_MIN_LINES consecutive lines, each found in at least REPEATED_MIN_EMAILS of the same
# sender's emails. Shorter repeated lines (a greeting, a one-line phrase) are kept.
REPEATED_MIN_EMAILS = 10
REPEATED_MIN_LINES = 3


def strip_repeated_blocks(emails, min_emails=REPEATED_MIN_EMAILS, min_lines=REPEATED_MIN_LINES):
    """Remove the signature and disclaimer blocks each sender repeats; returns the emails changed."""
    if min_emails <= 0:
        return 0
    seen = {}
    for item in emails:
        counts = seen.setdefault(item["from"], Counter())
        counts.update({line.strip() for line in item["text"].split("\n") if line.strip()})
    # A signature also follows the author's own text in some emails. A message sent many times
    # on its own (a repeated request) never does, so it is kept.
    trailing = {}
    for item in emails:
        counts, below = seen[item["from"]], trailing.setdefault(item["from"], set())
        after_text = False
        for line in (line.strip() for line in item["text"].split("\n")):
            if not line:
                continue
            if counts[line] < min_emails:
                after_text = True
            elif after_text:
                below.add(line)
    changed = 0
    for item in emails:
        counts = Counter({line: n for line, n in seen[item["from"]].items()
                          if line in trailing[item["from"]]})
        lines = item["text"].split("\n")
        drop, run = set(), []
        for i, line in enumerate(lines + [None]):
            key = None if line is None else line.strip()
            if key == "":
                if run:
                    run.append(i)  # a blank line inside a block
                continue
            if key is not None and counts[key] >= min_emails:
                run.append(i)
                continue
            if sum(1 for j in run if lines[j].strip()) >= min_lines:
                drop.update(run)
            run = []
        if not drop:
            continue
        text = tidy("\n".join(trim_tail(line for j, line in enumerate(lines) if j not in drop)))
        item["removed_chars"] += len(item["text"]) - len(text)
        item["text"] = text
        changed += 1
    return changed


class _HTMLText(HTMLParser):
    """Visible text of an HTML email, without the quoted part of a reply."""

    VOID = {"br", "hr", "img", "meta", "link", "input", "wbr", "area", "base", "col", "source"}
    BREAK = {"br", "hr", "p", "div", "li", "tr", "table", "ul", "ol", "h1", "h2", "h3", "h4",
             "h5", "h6", "pre", "section", "article", "header", "footer"}
    SKIP_TAGS = {"style", "script", "head", "title", "blockquote", "template"}
    # Gmail, Yahoo and others wrap the quoted reply in an element with one of these classes
    SKIP_CLASSES = ("gmail_quote", "yahoo_quoted", "moz-cite-prefix", "protonmail_quote")
    # Outlook puts the quoted message after one of these, as siblings, not inside
    STOP_IDS = {"divrplyfwdmsg", "appendonsend", "mail-editor-reference-message-container"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.stack = []  # (tag, skipped) for the open non-void elements
        self.skipping = 0
        self.stopped = False

    def handle_starttag(self, tag, attrs):
        if self.stopped:
            return
        values = dict(attrs)
        if (values.get("id") or "").lower() in self.STOP_IDS:
            self.stopped = True
            return
        if tag in self.BREAK and not self.skipping:
            self.parts.append("\n")
        if tag in self.VOID:
            return
        classes = (values.get("class") or "").lower()
        skip = tag in self.SKIP_TAGS or any(name in classes for name in self.SKIP_CLASSES)
        self.stack.append((tag, skip))
        if skip:
            self.skipping += 1

    def handle_startendtag(self, tag, attrs):
        if not self.stopped and tag in self.BREAK and not self.skipping:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.stopped or not any(open_tag == tag for open_tag, _ in self.stack):
            return
        while self.stack:
            open_tag, skipped = self.stack.pop()
            if skipped:
                self.skipping -= 1
            if open_tag == tag:
                break
        if tag in self.BREAK and not self.skipping:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.stopped and not self.skipping:
            self.parts.append(re.sub(r"[ \t\r\n\f]+", " ", data))

    def text(self):
        # Adjacent block elements each add a break; HTML spacing carries no meaning, so
        # consecutive breaks become one line end.
        lines = (line.strip() for line in "".join(self.parts).split("\n"))
        return re.sub(r"\n+", "\n", "\n".join(lines)).strip("\n")


def html_to_text(markup):
    parser = _HTMLText()
    parser.feed(markup)
    parser.close()
    return parser.text()


# ---------------------------------------------------------------- reading one email


def part_text(part):
    """A text part's content as str, whatever its declared charset."""
    try:
        content = part.get_content()
        if isinstance(content, str):
            return clean(content)
    except (LookupError, UnicodeError, AssertionError, KeyError, ValueError):
        pass
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, "replace")
    except LookupError:
        return payload.decode("utf-8", "replace")


def body_of(message):
    """(text, origin) of an email: its plain-text part, else its HTML part as text."""
    try:
        plain = message.get_body(preferencelist=("plain",))
        html_part = None if plain is not None else message.get_body(preferencelist=("html",))
    except (KeyError, AttributeError, ValueError):
        plain = html_part = None
    if plain is not None:
        text = part_text(plain)
        if text.strip():
            return text, "plain"
        # An empty plain-text version next to an HTML one: the HTML holds the email
        try:
            html_part = message.get_body(preferencelist=("html",))
        except (KeyError, AttributeError, ValueError):
            html_part = None
        if html_part is None:
            return text, "plain"
    if html_part is not None:
        return html_to_text(part_text(html_part)), "html"
    if not message.is_multipart() and message.get_content_maintype() == "text":
        return part_text(message), "plain"
    return "", "none"


def attachment_names(message):
    names = []
    for part in message.walk():
        if part.is_multipart():
            continue
        disposition = (part.get_content_disposition() or "").lower()
        if disposition == "attachment" or (disposition == "inline" and part.get_filename()):
            names.append(clean(part.get_filename() or "") or "(χωρίς όνομα)")
    return names


def header(message, name):
    try:
        values = message.get_all(name) or []
    except Exception:  # pylint: disable=broad-exception-caught  # malformed headers vary
        return ""
    return " ".join(clean(value) for value in values).strip()


def addresses(message, name):
    """(display name, lower-case address) pairs of an address header."""
    raw = []
    try:
        raw = [clean(value) for value in message.get_all(name) or []]
    except Exception:  # pylint: disable=broad-exception-caught  # malformed headers vary
        pass
    pairs = []
    for display, address in email.utils.getaddresses(raw):
        address = address.strip().lower()
        if address:
            pairs.append((display.strip(), address))
    return pairs


def iso_time(value):
    """The Date header as an ISO UTC time (``2024-01-02T03:04:05Z``), or ''."""
    if not value:
        return ""
    try:
        moment = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return ""
    if moment is None:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return ""


def read_email(message, index):
    """The fields the analysis and the reading copy need from one email."""
    sender = addresses(message, "From")
    name, address = sender[0] if sender else ("", "")
    labels = [label.strip() for label in header(message, "X-Gmail-Labels").split(",") if label.strip()]
    raw_text, origin = body_of(message)
    text, removed = strip_quoted(raw_text)
    item = {
        "index": index,
        "message_id": header(message, "Message-ID"),
        "thread_id": header(message, "X-GM-THRID"),
        "time": iso_time(header(message, "Date")),
        "from": address,
        "from_name": name,
        "to": [a for _, a in addresses(message, "To")],
        "cc": [a for _, a in addresses(message, "Cc")],
        "subject": header(message, "Subject"),
        "in_reply_to": header(message, "In-Reply-To"),
        "labels": labels,
        "body": origin,
        "text": text,
        "removed_chars": removed,
        "attachments": attachment_names(message),
    }
    for field, value in item.items():
        values = value if isinstance(value, list) else [value]
        if any(isinstance(v, str) and SURROGATE.search(v) for v in values):
            raise ExtractError(f"Email {index}: {field} contains an unpaired Unicode surrogate.")
    return item


def read_mbox(path):
    """Every email of the mbox file, in file order, and the file's SHA-256."""
    if not path.is_file():
        raise ExtractError(f"Cannot read {path}: not a file")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    parser = BytesParser(policy=email.policy.default)
    box = mailbox.mbox(str(path), factory=parser.parse, create=False)
    try:
        emails = [read_email(message, index) for index, message in enumerate(box)]
    finally:
        box.close()
    return emails, digest.hexdigest()


# ---------------------------------------------------------------- threads and senders


def self_addresses(emails, given=None):
    """Your addresses: --self if given, else the senders of the emails labelled Sent."""
    if given:
        return {a.strip().lower() for a in given.split(",") if a.strip()}
    return {e["from"] for e in emails if "Sent" in e["labels"] and e["from"]}


def thread_key(item):
    """Gmail's thread ID; an email without one is a thread of its own."""
    return item["thread_id"] or f"no-thread-{item['index']}"


def group_threads(emails):
    """Threads, each in time order, ordered by their first email."""
    threads = {}
    for item in emails:
        threads.setdefault(thread_key(item), []).append(item)
    ordered = []
    for key, items in threads.items():
        items.sort(key=lambda e: (e["time"] or "9999", e["index"]))
        subject = next((e["subject"] for e in items if e["subject"]), "")
        ordered.append({"id": key, "subject": subject, "messages": items})
    ordered.sort(key=lambda t: (t["messages"][0]["time"] or "9999", t["messages"][0]["index"]))
    return ordered


def list_senders(emails, mine):
    counts, names = Counter(), {}
    for item in emails:
        counts[item["from"]] += 1
        if item["from_name"]:
            names.setdefault(item["from"], Counter())[item["from_name"]] += 1
    lines = []
    for address, count in counts.most_common():
        shown = ", ".join(n for n, _ in names.get(address, Counter()).most_common(3))
        you = "  (you)" if address in mine else ""
        lines.append(f"{count:>7}  {address or '(no sender)'}{you}   {shown}")
    return lines


def build_extract(emails, digest, sender, mine, all_threads=False):
    threads = group_threads(emails)
    if not all_threads:
        threads = [t for t in threads if any(e["from"] == sender for e in t["messages"])]
    return {
        "format": FORMAT,
        "version": VERSION,
        "source_sha256": digest,
        "sender": sender,
        "self": sorted(mine),
        "threads": threads,
    }


def build_markdown(extract):
    count = sum(len(t["messages"]) for t in extract["threads"])
    lines = [f"# Email threads with {extract['sender']}", "",
             f"Threads: {len(extract['threads'])} · Emails: {count}", "",
             "Each email shows only its own new text: quoted earlier messages, forwarded",
             "messages and signatures were removed.", "", "---", ""]
    for number, thread in enumerate(extract["threads"], 1):
        subject = (thread["subject"] or "(χωρίς θέμα)").replace("\n", " ")
        lines += [f"## {number}. {subject}", "", f"Thread ID: `{thread['id']}`", ""]
        for item in thread["messages"]:
            who = (item["from_name"] + f" <{item['from']}>" if item["from_name"] else item["from"])
            lines += [f"### {item['time'] or 'Unknown date'} — {who.replace(chr(10), ' ')}", ""]
            if item["message_id"]:
                lines.append(f"Message-ID: `{item['message_id']}`  ")
            if item["to"] or item["cc"]:
                lines.append("To: " + ", ".join(item["to"] + item["cc"]) + "  ")
            if item["attachments"]:
                lines.append("Attachments: " + ", ".join(item["attachments"]) + "  ")
            if item["removed_chars"]:
                lines.append(f"Removed quoted text/signature: {item['removed_chars']} characters  ")
            lines += ["", item["text"] or "[Κενό κείμενο]", "", "---", ""]
    return "\n".join(lines)


def folder_name(sender):
    digest = hashlib.sha256(sender.encode("utf-8")).hexdigest()[:16]
    return f"{safe_filename(sender)} [{digest}]"


# ---------------------------------------------------------------- command line


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mbox", type=Path, help="the .mbox file from Google Takeout (Mail)")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list-senders", action="store_true",
                        help="list the senders and their email counts")
    action.add_argument("--sender", help="the sender's email address (from --list-senders)")
    parser.add_argument("--self", dest="self_addresses",
                        help="your own addresses, comma-separated (default: the senders of emails labelled Sent)")
    parser.add_argument("--all-threads", action="store_true",
                        help="keep every thread, also those without an email from the sender")
    parser.add_argument("--keep-repeated", action="store_true",
                        help="keep signature and disclaimer blocks a sender repeats in many emails")
    parser.add_argument("--output-dir", type=Path, default=Path("gmail-extracted"))
    args = parser.parse_args(argv)

    try:
        emails, digest = read_mbox(args.mbox)
        mine = self_addresses(emails, args.self_addresses)
        if args.list_senders:
            print("\n".join(list_senders(emails, mine)))
            return
        sender = args.sender.strip().lower()
        if not any(e["from"] == sender for e in emails):
            raise ExtractError("No email from this exact address (see --list-senders).")
        repeated = 0 if args.keep_repeated else strip_repeated_blocks(emails)
        extract =build_extract(emails, digest, sender, mine, args.all_threads)
        folder = args.output_dir / folder_name(sender)
        outputs = [
            (folder / "conversation.json",
             json.dumps(extract, ensure_ascii=False, indent=2).encode("utf-8")),
            (folder / "conversation.md", build_markdown(extract).encode("utf-8")),
        ]
        for path, _contents in outputs:
            if path.exists() or path.is_symlink():
                raise ExtractError(f"Output already exists: {path}. Choose a different --output-dir.")
        missing, current = [], folder
        while not current.exists():
            missing.append(current)
            current = current.parent
        created = []
        try:
            for directory in reversed(missing):
                try:
                    directory.mkdir(mode=0o700)
                    created.append(directory)
                except FileExistsError:
                    if not directory.is_dir():
                        raise
            write_outputs(outputs)
        except BaseException:
            for directory in reversed(created):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise
    except (ExtractError, ExtractionWriteError, OSError, mailbox.Error) as exc:
        parser.error(str(exc))
    kept = sum(len(t["messages"]) for t in extract["threads"])
    from_sender = sum(e["from"] == sender for t in extract["threads"] for e in t["messages"])
    print(f"Emails in the mbox: {len(emails)}; threads kept: {len(extract['threads'])}; "
          f"emails kept: {kept} ({from_sender} from the sender)")
    if repeated:
        print(f"Repeated signature/disclaimer blocks removed from {repeated} emails "
              "(--keep-repeated keeps them).")
    if not mine:
        print("Note: no email is labelled Sent, so none is marked as yours; pass --self if needed.")
    print(f"Files saved in: {folder.resolve()}")


if __name__ == "__main__":
    main()
