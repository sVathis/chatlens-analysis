# Viber analysis

`viber_analyze.py` reads the `viber_export.json` that
[ViberPC-Export](https://github.com/sVathis/ViberPC-Export) writes, analyses **one chat** and
**one sender**, and writes the reports described below. Status: run end to end on a real chat, with
the report links checked in ChatLens; findings are candidates for human review.

> [!WARNING]
> **Privacy.** Running an analysis sends the **text, sender names and times** of the selected
> chat, in batches, to the OpenAI API (see the [main README](../README.md)). `--list-chats`,
> `--list-senders` and `--report-only` never use the network.

## Setup

Python 3.10+. Nothing to install for listing, tests or `--report-only`; for a real analysis:

```bash
python -m pip install -r viber/requirements.txt
export OPENAI_API_KEY=...        # in your shell, never in a file
```

## Usage

Run these from the repository root (the scripts find the shared helpers there). Extraction is
optional: the analyzer can select a chat directly from the full export.

To extract one full chat first, list chats and select an exact chat ID or a contact from a
direct chat:

```bash
python viber/viber_extract.py export/viber_export.json --list
python viber/viber_extract.py export/viber_export.json --chat 10 --output-dir viber-extracted
# Or: --contact viber:2  (an exact contact ID, name or part of a direct-chat title)
```

The extractor writes a single-chat `viber_export.json` and chronological `conversation.md`
under an ID-suffixed folder. It keeps all participants, messages, captions, replies, reactions
and other selected-chat fields; contacts are limited to those referenced by the chat or self.
Unknown top-level export fields are omitted. `--contact` searches direct chats only and
refuses ambiguous matches; use `--chat` to choose a specific direct, group or community chat.
`--split year` or `--split month` splits only the Markdown using UTC dates. Reaction-event details remain in
the JSON; the Markdown is a reading copy and raw text can resemble message headings.
Contact retention follows ViberPC-Export version 1: self and member IDs, message author and
call peer IDs, and reaction-event authors. Version 1 reply references are message IDs, while
contact cards contain names and numbers. Unknown future nested contact references are not inferred.

Extraction runs locally without an API key. Media paths are retained, but media files are not
copied; keep the original export and media folder for viewing attachments. Existing output is
never overwritten. On POSIX systems, extracted files are owner-only; on Windows, access depends
on the output folder's ACL. The extractor reads the full export into memory. Both the source and
extracted files are private. If extraction is forcibly terminated, inspect the output folder for
partial files before retrying.

To analyze the extracted chat, use its `viber_export.json` path below; because it has one chat,
`--chat` is optional. For an existing analysis, keep using its original input file: extracting or
re-serializing changes the source hash and prevents resuming its cached batches.

```bash
python viber/viber_analyze.py --input 'viber-extracted/EXTRACTED_FOLDER/viber_export.json' --list-senders
python viber/viber_analyze.py --input 'viber-extracted/EXTRACTED_FOLDER/viber_export.json' --sender viber:2
```

Replace `EXTRACTED_FOLDER` with the path printed by the extractor and `viber:2` with the exact
sender ID from the list.

To analyze the full export directly:

```bash
# 1. Which chats are in the export? (ID, type, title; no network)
python viber/viber_analyze.py --input export/viber_export.json --list-chats

# 2. Who wrote in the chat you picked? Sender IDs are viber:self (you) or viber:<ContactID>
python viber/viber_analyze.py --input export/viber_export.json --chat 10 --list-senders

# 3. Analyse one sender in that chat
python viber/viber_analyze.py --input export/viber_export.json --chat 10 --sender viber:2 \
    --chatlens-url http://10.0.0.10:5173/ --workers 3
```

Try `--max-batches 1` first on a chat you know, to see a real result before a full run.

`--chatlens-url` must be the exact address you open ChatLens at (same host, port, path and
query), so that report links reuse an open ChatLens tab. Only identifiers go into the link
(`#conversationId=viber:10&messageId=203`), never message text.

## Output

In `--output` (default `viber-analysis/`):

- `report.html`, `report.md`: verified findings with context and links
- `history.html`: the whole conversation as a static page; each message has an anchor
- `findings.json`, `findings.csv`
- `rejected_findings.json`: what the model returned that failed verification
- `summary.json`: coverage and token usage
- `batches/`: the cache

**Resuming.** Every batch is cached. Run the same command again to continue after an
interruption or a failed batch; `--workers` and `--max-batches` can change between runs. If
the export, chat, sender, model, `--batch-size` or `--context` changes, the script refuses to
mix results: use a new `--output`. `--report-only` rebuilds the reports from the cache without
any API call.

**Checks.** A finding is kept only if its message is in the batch's scored (TARGET) window,
was written by the selected sender, and its quote appears word for word in that message. A
quote that differs only in whitespace (extra spaces, line breaks, non-breaking spaces) or in how
an accent is stored is still accepted, and the report then shows the message's own text, so a
kept quote is always exactly what the sender wrote. Such findings have `"quote_match":
"normalized"` in `findings.json` (`"exact"` otherwise). A quote with different words, letters
or punctuation is rejected.
Messages without text (photos, stickers, calls, locations, system notices) are context only.

## Differences from the Skype analysis

The pipeline, the prompt (plus two sentences about `kind` and `reply_to`), the batching, the
retries and splitting, the verification and the report layout are the same as in
[`skype/`](../skype/README.md), so the outputs have the same files and the same `findings.csv` and `summary.json` fields,
apart from the ID and link ones below. What differs, on purpose:

- **Input.** The whole `viber_export.json`, with `--chat` to pick the conversation (Skype
  needs a separately extracted `conversation.json`). `run_config.json` and `summary.json` also
  record the `conversation_id`.
- **IDs and links.** Findings carry `viber_id` and `chatlens_url` (`--chatlens-url`), where
  Skype has `skype_id` and the same `chatlens_url` (`--chatlens-url`). Report links open the tab named
  `chatlens`.
- **Messages without text** (photos, stickers, calls, locations) stay in the context and show
  as `[Κενό κείμενο / <kind>]`. A caption on a photo or video is analysed like text.
- **Replies.** The quoted message goes to the model as `reply_to`, for context only, and
  `history.html` shows it above the reply.
- **Times** are UTC (ISO 8601, ending in `Z`), made from Viber's epoch milliseconds.

## Limitations and safeguards

The limits listed for the [Skype analysis](../skype/README.md#limitations-and-safeguards) apply
here too: the model can overlook relevant language, findings are candidates for human review and
not a legal assessment, photos, voice messages and files are not analysed, a message is cut at
30,000 characters, only 5 neighbouring messages are context, every uncached batch costs API
tokens (and failed attempts are billed but missing from `summary.json`), and the verbatim-quote
check rejects altered quotes. The export and the output folders contain personal information:
do not publish them. Also specific to Viber: Viber reactions and edit history are not part of
the analysis. If messages share a Viber ID, their ChatLens links can be ambiguous; use their
distinct `history.html` links to inspect them.

CSV fields retain their original values: spreadsheet software may interpret values beginning
with `=`, `+`, `-`, or `@` as formulas. Import them as text or use JSON. `report.md` may contain
raw HTML from untrusted text; use a Markdown viewer that sanitizes HTML, or open the escaped
`report.html` instead.

## Tests

```bash
python -m unittest discover -s viber/tests -v
```

Tests use synthetic data and a fake API client; they need no network and no key.
