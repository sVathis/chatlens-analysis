# Viber analysis

`viber_analyze.py` reads the `viber_export.json` that
[ViberPC-Export](https://github.com/sVathis/ViberPC-Export) writes, analyses **one chat** and
**one sender**, and writes the reports described below. Status: **not yet run on real data**.

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

Run these from the repository root (the script finds the shared `chatlens_links.py` there):

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
was written by the selected sender, and its quote appears word for word in that message.
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
the analysis.

## Tests

```bash
python -m unittest discover -s viber/tests -v
```

Tests use synthetic data and a fake API client; they need no network and no key.
