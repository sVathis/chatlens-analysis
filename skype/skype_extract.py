#!/usr/bin/env python3
"""Extract a single conversation from a Skype messages.json export.

Uses Python 3.10+ standard library only. JSON preserves the original conversation
and message order. Markdown is a readable representation, not a forensic replacement.
"""
import argparse
import collections
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from extract_utils import ExtractionWriteError, safe_filename, write_outputs


class SkypeHTML(HTMLParser):
    BLOCKS = {"p", "div", "li", "blockquote", "tr", "h1", "h2", "h3"}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "br": self.parts.append("\n")
        if tag in {"img", "file"}:
            alt = attrs.get("alt") or attrs.get("title") or attrs.get("src")
            if alt: self.parts.append(f"[image/file: {alt}]")
    def handle_endtag(self, tag):
        if tag in self.BLOCKS: self.parts.append("\n")
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

def sender_id(m):
    return str(m.get('from') or m.get('creator') or m.get('sender') or '')

def sender_name(m):
    return str(m.get('displayName') or m.get('imdisplayname') or sender_id(m))

def timestamp(m):
    return str(m.get('originalarrivaltime') or m.get('composetime') or m.get('createdTime') or '')


def sort_key(m):
    return timestamp(m)


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
        sender = (sender_name(m) or "Unknown").replace("\n", " ")
        ts = timestamp(m) or "Unknown date"
        lines += [f"### {ts} — {sender}", ""]
        if sender_id(m): lines.append(f"Sender ID: `{sender_id(m)}`")
        if m.get("id") is not None: lines.append(f"Message ID: `{m['id']}`")
        if m.get("messagetype"): lines.append(f"Type: `{m['messagetype']}`")
        if m.get("content") is not None:
            lines += ["", readable(m["content"]) or "[Empty / non-text message]"]
        else: lines += ["", "[No content in export]"]
        lines += ["", "---", ""]
    return "\n".join(lines)


def folder_name(conversation, index, conversations):
    """Use an ID-derived suffix; source index disambiguates missing or duplicate IDs."""
    identifier = conversation.get("id")
    id_text = "" if identifier is None else str(identifier)
    duplicate = sum(str(c.get("id")) == id_text for c in conversations if c.get("id") is not None) > 1
    identity = f"id:{id_text}" if id_text and not duplicate else f"index:{index}:id:{id_text}"
    digest = hashlib.sha256(identity.encode("utf-8", errors="surrogatepass")).hexdigest()[:16]
    return f"{safe_filename(conversation_name(conversation))} [{digest}]"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("export", type=Path, help="Skype messages.json")
    action = p.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true", help="List all conversations")
    action.add_argument("--contact", help="Exact conversation ID, or part of display name")
    action.add_argument("--index", type=int, help="One-based conversation index shown by --list")
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
    if any(not isinstance(c, dict) for c in conversations):
        p.error("Every conversation must be an object")
    if args.list:
        for index, c in sorted(enumerate(conversations), key=lambda pair: conversation_name(pair[1]).casefold()):
            print(f"{len(messages_of(c)):>7}  {conversation_name(c)}  [ID: {c.get('id', '?')}] [index: {index + 1}]")
        print(f"\n{len(conversations)} conversations")
        return
    if args.index is not None:
        if not 1 <= args.index <= len(conversations):
            p.error("--index must be a conversation index shown by --list")
        index = args.index - 1
        c = conversations[index]
    else:
        q = args.contact.casefold()
        indexed = list(enumerate(conversations))
        exact = [(i, c) for i, c in indexed if str(c.get("id", "")).casefold() == q]
        matches = exact or [(i, c) for i, c in indexed if q in conversation_name(c).casefold()]
        if len(matches) != 1:
            print(f"Found {len(matches)} matching conversations. Select an exact ID or index from --list.", file=sys.stderr)
            for i, c in matches[:30]:
                print(f"  {conversation_name(c)}  [ID: {c.get('id')}] [index: {i + 1}]", file=sys.stderr)
            sys.exit(2)
        index, c = matches[0]
    original_messages = messages_of(c)
    if not isinstance(original_messages, list) or any(not isinstance(m, dict) for m in original_messages):
        p.error("The selected conversation's messages must be a list of objects")
    msgs = sorted(original_messages, key=sort_key)
    folder = args.output_dir / folder_name(c, index, conversations)

    grouped = collections.defaultdict(list)
    for m in msgs: grouped[partition(m, args.split)].append(m)
    if not grouped: grouped["all"] = []
    markdown_files = [
        ("conversation.md" if args.split == "none" else f"conversation-{period}.md", group)
        for period, group in sorted(grouped.items())
    ]
    # Preserve every original conversation field and the original message order.
    archive = {k: v for k, v in data.items() if k != "conversations"}
    archive["conversations"] = [c]
    try:
        outputs = [(folder / "conversation.json", json.dumps(archive, indent=2, ensure_ascii=False).encode("utf-8"))]
        outputs += [(folder / filename, build_markdown(c, group, data.get("userId", "")).encode("utf-8"))
                    for filename, group in markdown_files]
    except UnicodeEncodeError:
        p.error("Selected conversation contains an unpaired Unicode surrogate; no output files were written")
    for path, _contents in outputs:
        if path.exists() or path.is_symlink():
            p.error(f"Output already exists: {path}. Choose a different --output-dir.")
    try:
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_outputs(outputs)
    except ExtractionWriteError as exc:
        p.error(str(exc))
    except OSError as exc:
        p.error(f"Cannot create output directory ({type(exc).__name__})")
    print(f"Extracted {len(msgs)} messages from {conversation_name(c)!r}")
    print(f"Files saved in: {folder.resolve()}")
    print("Keep the original Skype export; Markdown is a lossy text rendering.")


if __name__ == "__main__":
    main()
