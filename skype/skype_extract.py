#!/usr/bin/env python3
"""Extract a single conversation from a Skype messages.json export.

Uses Python 3.9+ standard library only. JSON preserves the original conversation
and message order. Markdown is a readable representation, not a forensic replacement.
"""
import argparse
import collections
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys


class SkypeHTML(HTMLParser):
    BLOCKS = {"p", "div", "li", "blockquote", "tr", "h1", "h2", "h3"}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.links = []
        self.href = None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "br": self.parts.append("\n")
        if tag in {"img", "file"}:
            alt = attrs.get("alt") or attrs.get("title") or attrs.get("src")
            if alt: self.parts.append(f"[image/file: {alt}]")
        if tag == "a": self.href = attrs.get("href")
    def handle_endtag(self, tag):
        if tag in self.BLOCKS: self.parts.append("\n")
        if tag == "a":
            self.href = None
    def handle_data(self, data): self.parts.append(data)
    def text(self):
        t = "".join(self.parts).replace("\r\n", "\n")
        return re.sub(r"\n{3,}", "\n\n", t).strip()


def readable(value):
    if value is None: return ""
    if not isinstance(value, str): return json.dumps(value, ensure_ascii=False)
    parser = SkypeHTML()
    try:
        parser.feed(value)
        return parser.text()
    except Exception:
        return html.unescape(re.sub(r"<[^>]*>", "", value))


def messages_of(conversation):
    return conversation.get("MessageList") or conversation.get("messages") or []


def conversation_name(c):
    return str(c.get("displayName") or (c.get("threadProperties") or {}).get("topic") or c.get("id") or "Untitled")

def timestamp(m):
    return str(m.get("originalarrivaltime") or m.get("composeTime") or m.get("createdTime") or "")


def sort_key(m):
    return (timestamp(m), str(m.get("id", "")))


def partition(m, split):
    ts = timestamp(m)
    match = re.match(r"^(\d{4})-(\d{2})", ts)
    if split == "none": return "all"
    if not match: return "undated"
    return match.group(1) if split == "year" else f"{match.group(1)}-{match.group(2)}"


def build_markdown(c, messages, account):
    title = conversation_name(c).replace("\n", " ")
    lines = [f"# Skype conversation: {title}", "", f"Conversation ID: `{c.get('id', '')}`", f"Account ID: `{account}`", f"Messages in this file: {len(messages)}", "", "---", ""]
    for m in messages:
        sender = str(m.get("displayName") or m.get("from") or "Unknown").replace("\n", " ")
        ts = timestamp(m) or "Unknown date"
        lines += [f"### {ts} — {sender}", ""]
        if m.get("from"): lines.append(f"Sender ID: `{m['from']}`")
        if m.get("id") is not None: lines.append(f"Message ID: `{m['id']}`")
        if m.get("messagetype"): lines.append(f"Type: `{m['messagetype']}`")
        if m.get("content") is not None:
            lines += ["", readable(m["content"]) or "[Empty / non-text message]"]
        else: lines += ["", "[No content in export]"]
        lines += ["", "---", ""]
    return "\n".join(lines)


def safe_filename(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return value[:80] or "conversation"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("export", type=Path, help="Skype messages.json")
    action = p.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true", help="List all conversations")
    action.add_argument("--contact", help="Exact conversation ID, or part of display name")
    p.add_argument("--output-dir", type=Path, default=Path("skype-extracted"))
    p.add_argument("--split", choices=["none", "year", "month"], default="none", help="Split Markdown chronologically")
    args = p.parse_args()

    try:
        with args.export.open("r", encoding="utf-8-sig") as f: data = json.load(f)
    except UnicodeDecodeError:
        p.error("Cannot read export: invalid UTF-8")
    except (OSError, json.JSONDecodeError) as e:
        p.error(f"Cannot read export: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("conversations"), list):
        p.error("Expected Skype export with a top-level 'conversations' array")
    conversations = data["conversations"]
    if args.list:
        for c in sorted(conversations, key=lambda c: conversation_name(c).casefold()):
            print(f"{len(messages_of(c)):>7}  {conversation_name(c)}  [ID: {c.get('id', '?')}]")
        print(f"\n{len(conversations)} conversations")
        return
    q = args.contact.casefold()
    exact = [c for c in conversations if str(c.get("id", "")).casefold() == q]
    matches = exact or [c for c in conversations if q in conversation_name(c).casefold()]
    if len(matches) != 1:
        print(f"Found {len(matches)} matching conversations. Select an exact ID from --list.", file=sys.stderr)
        for c in matches[:30]: print(f"  {conversation_name(c)}  [ID: {c.get('id')} ]", file=sys.stderr)
        sys.exit(2)
    c = matches[0]
    original_messages = messages_of(c)
    if not isinstance(original_messages, list) or any(not isinstance(m, dict) for m in original_messages):
        p.error("The selected conversation's messages must be a list of objects")
    msgs = sorted(original_messages, key=sort_key)
    folder = args.output_dir / safe_filename(conversation_name(c))

    grouped = collections.defaultdict(list)
    for m in msgs: grouped[partition(m, args.split)].append(m)
    if not grouped: grouped["all"] = []
    markdown_files = [
        ("conversation.md" if args.split == "none" else f"conversation-{period}.md", group)
        for period, group in sorted(grouped.items())
    ]
    for filename in ["conversation.json", *(name for name, _group in markdown_files)]:
        path = folder / filename
        if path.exists() or path.is_symlink():
            p.error(f"Output already exists: {path}. Choose a different --output-dir.")
    folder.mkdir(parents=True, exist_ok=True)

    # Preserve every original conversation field and the original message order.
    archive = {k: v for k, v in data.items() if k != "conversations"}
    archive["conversations"] = [c]
    def write_new(path, content):
        try:
            with path.open("x", encoding="utf-8") as output:
                output.write(content)
        except FileExistsError:
            p.error(f"Output already exists: {path}. Choose a different --output-dir.")

    write_new(folder / "conversation.json", json.dumps(archive, indent=2, ensure_ascii=False))

    for filename, group in markdown_files:
        write_new(folder / filename, build_markdown(c, group, data.get("userId", "")))
    print(f"Extracted {len(msgs)} messages from {conversation_name(c)!r}")
    print(f"Files saved in: {folder.resolve()}")
    print("Keep the original Skype export; Markdown is a lossy text rendering.")


if __name__ == "__main__":
    main()
