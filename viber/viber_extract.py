#!/usr/bin/env python3
"""Extract one Viber conversation into a local JSON export and readable Markdown.

The JSON retains the selected chat, all of its messages and reaction events, and
only the referenced contacts. Media paths remain references; files are not copied.
"""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from extract_utils import ExtractionWriteError, safe_filename, write_outputs
from viber.viber_analyze import AnalysisError, build_messages, find_chat, list_chats, read_export


def contact_ids(data, chat):
    """Contact IDs needed by the selected chat, including self and reaction authors."""
    ids = set()

    def add(value):
        if value is not None and isinstance(value, (str, int)) and not isinstance(value, bool):
            ids.add(str(value))

    self_info = data.get('self') if isinstance(data.get('self'), dict) else {}
    self_ids = self_info.get('contactIds')
    if isinstance(self_ids, list):
        for value in self_ids:
            add(value)
    members = chat.get('memberIds')
    if isinstance(members, list):
        for value in members:
            add(value)
    for field in ('messages', 'reactionEvents'):
        entries = chat.get(field)
        if isinstance(entries, list):
            for item in entries:
                if isinstance(item, dict):
                    add(item.get('authorId'))
                    add(item.get('peerId'))
    return ids


def other_contact_ids(data, chat):
    """Other participants in a direct chat, excluding known and outgoing self IDs."""
    self_info = data.get('self') if isinstance(data.get('self'), dict) else {}
    self_values = self_info.get('contactIds') or []
    if not isinstance(self_values, list):
        raise AnalysisError('Export self.contactIds must be a list')
    self_ids = {str(value) for value in self_values}
    for field in ('messages', 'reactionEvents'):
        entries = chat.get(field)
        if isinstance(entries, list):
            self_ids.update(str(item['authorId']) for item in entries
                            if isinstance(item, dict) and item.get('outgoing') is True
                            and item.get('authorId') is not None)
    return contact_ids(data, chat) - self_ids


def select_contact(data, query):
    """Find a direct chat by contact ID, name or chat title; never pick an ambiguous match."""
    query = query.strip()
    if not query:
        raise AnalysisError('--contact cannot be empty')
    folded = query.casefold()
    is_id = folded.startswith('viber:') or folded.isdecimal()
    wanted = folded[6:] if folded.startswith('viber:') else folded
    contacts = data.get('contacts') if isinstance(data.get('contacts'), dict) else {}
    matches = []
    for chat in data['chats']:
        if not isinstance(chat, dict) or chat.get('type') != 'direct':
            continue
        others = other_contact_ids(data, chat)
        if is_id:
            matched = any(value.casefold() == wanted for value in others)
        else:
            labels = [str(chat.get('title') or '')]
            for contact_id in others:
                card = contacts.get(contact_id)
                if isinstance(card, dict):
                    labels.extend(str(card.get(field) or '') for field in ('name', 'number'))
            matched = any(folded in label.casefold() for label in labels)
        if matched:
            matches.append(chat)
    if len(matches) != 1:
        ids = ', '.join(f"viber:{chat.get('id')}" for chat in matches)
        suffix = f' Matching chat IDs: {ids}.' if ids else ''
        raise AnalysisError(f'Found {len(matches)} matching direct chats.{suffix} Use --list and --chat.')
    return matches[0]


def extracted_export(data, chat):
    """Preserve the chat and metadata while omitting unrelated contacts and chats."""
    if data.get('contacts') is not None and not isinstance(data['contacts'], dict):
        raise AnalysisError('Export contacts must be an object')
    result = {key: value for key, value in data.items() if key != 'chats'}
    if isinstance(data.get('contacts'), dict):
        used = contact_ids(data, chat)
        result['contacts'] = {key: value for key, value in data['contacts'].items() if str(key) in used}
    result['chats'] = [chat]
    return result


def folder_name(chat):
    identifier = str(chat['id'])
    digest = hashlib.sha256(identifier.encode('utf-8', errors='surrogatepass')).hexdigest()[:16]
    title = str(chat.get('title') or f'Chat {identifier}')
    return f'{safe_filename(title)} [{digest}]'


def partition(message, split):
    time = message['time']
    if split == 'none':
        return 'all'
    if not time:
        return 'undated'
    return time[:4] if split == 'year' else time[:7]


def message_body(raw, kind):
    """Use original text and captions, including non-text event descriptions."""
    parts = [raw[field] for field in ('text', 'caption')
             if isinstance(raw.get(field), str) and raw[field]]
    return '\n'.join(parts) if parts else f'[Κενό κείμενο / {kind or "μήνυμα"}]'


def build_markdown(chat, messages, raw_messages):
    title = str(chat.get('title') or f"Chat {chat['id']}").replace('\n', ' ')
    lines = [f'# Viber conversation: {title}', '', f"Conversation ID: `viber:{chat['id']}`",
             f'Messages in this file: {len(messages)}', '', '---', '']
    for message in messages:
        raw = raw_messages[message['source_index']]
        name = (message['name'] or message['sender'] or 'Unknown').replace('\n', ' ')
        lines += [f"### {message['time'] or 'Unknown date'} — {name}", '']
        if message['sender']:
            lines.append(f"Sender ID: `{message['sender']}`")
        if message['viber_id']:
            lines.append(f"Message ID: `{message['viber_id']}`")
        if message['kind']:
            lines.append(f"Type: `{message['kind']}`")
        if message['reply_to']:
            lines += ['', 'Reply:', '', message['reply_to']]
        lines += ['', message_body(raw, message['kind'])]
        for field in ('media', 'sticker'):
            reference = raw.get(field)
            if isinstance(reference, dict) and isinstance(reference.get('path'), str):
                lines += ['', f"{field.title()} reference: `{reference['path']}`"]
        lines += ['', '---', '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('export', type=Path, help='viber_export.json from ViberPC-Export')
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--list', action='store_true', help='List chats and their IDs')
    action.add_argument('--chat', help='Exact chat ID, with or without the viber: prefix')
    action.add_argument('--contact', help='Contact ID, name or chat title; searches direct chats')
    parser.add_argument('--output-dir', type=Path, default=Path('viber-extracted'))
    parser.add_argument('--split', choices=('none', 'year', 'month'), default='none',
                        help='Split only the readable Markdown')
    args = parser.parse_args(argv)

    try:
        data, _digest = read_export(args.export)
        if args.list:
            print('\n'.join(list_chats(data)))
            return
        chat = find_chat(data, args.chat) if args.chat else select_contact(data, args.contact)
        if chat.get('id') is None:
            raise AnalysisError('The selected chat has no ID')
        # Reject duplicate chat IDs: ChatLens and --chat would otherwise be ambiguous.
        chat = find_chat(data, str(chat['id']))
        raw_messages = chat.get('messages') or []
        if any(not isinstance(message, dict) for message in raw_messages):
            raise AnalysisError("The selected chat's messages must be objects")
        messages = build_messages(data, chat)
        archive = extracted_export(data, chat)
        folder = args.output_dir / folder_name(chat)
        grouped = collections.defaultdict(list)
        for message in messages:
            grouped[partition(message, args.split)].append(message)
        if not grouped:
            grouped['all'] = []
        outputs = [(folder / 'viber_export.json', json.dumps(archive, indent=2, ensure_ascii=False).encode('utf-8'))]
        for period, group in sorted(grouped.items()):
            name = 'conversation.md' if args.split == 'none' else f'conversation-{period}.md'
            outputs.append((folder / name, build_markdown(chat, group, raw_messages).encode('utf-8')))
        for path, _contents in outputs:
            if path.exists() or path.is_symlink():
                raise AnalysisError(f'Output already exists: {path}. Choose a different --output-dir.')
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_outputs(outputs)
    except UnicodeEncodeError:
        parser.error('Selected chat contains an unpaired Unicode surrogate; no output files were written')
    except (AnalysisError, ExtractionWriteError, OSError) as exc:
        parser.error(str(exc))
    print(f"Extracted {len(messages)} messages from chat viber:{chat['id']}")
    print(f'Files saved in: {folder.resolve()}')
    print('Media files are not copied; keep the original Viber export and its media folder.')


if __name__ == '__main__':
    main()
