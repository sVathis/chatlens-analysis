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
python -m pip install openai
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

## Tests

```bash
python -m unittest discover -s viber -v
```

Tests use synthetic data and a fake API client; they need no network and no key.
