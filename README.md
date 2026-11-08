# chatlens-analysis

Evidence-oriented, resumable analysis of one chat conversation, for conversations from
[ChatLens](https://github.com/sVathis/chatlens)-supported apps. A model reads the messages
in batches and flags **candidate** personally insulting language from one selected sender.
Every finding is checked against the source text, and every finding in the reports links back
to the exact message in ChatLens.

| Script | Reads | Status |
|---|---|---|
| `viber_analyze.py` | `viber_export.json` from [ViberPC-Export](https://github.com/sVathis/ViberPC-Export) | here, **not yet run on real data** |
| `skype_analyze.py` | a single extracted Skype conversation (`skype_extract.py` output) | to be added to this repo |

> [!WARNING]
> **Privacy.** Unlike ChatLens (which runs entirely in your browser), these scripts send data
> out: running an analysis sends the **text, sender names and times** of the selected
> conversation, in batches, to the OpenAI API. `--list-chats`, `--list-senders` and
> `--report-only` never use the network. Use this only on conversations you are entitled to
> analyse. The output folders hold private conversation text: they are in `.gitignore`, and
> this repository should stay private.

The findings are indications for human review. They are not statements about intent or legal
liability, and the reports say so.

## Setup

Python 3.10+. Nothing to install for listing, tests or `--report-only`; for a real analysis:

```bash
python -m pip install openai
export OPENAI_API_KEY=...        # in your shell, never in a file
```

## Analysing a Viber conversation

```bash
# 1. Which chats are in the export? (ID, type, title; no network)
python viber_analyze.py --input export/viber_export.json --list-chats

# 2. Who wrote in the chat you picked? Sender IDs are viber:self (you) or viber:<ContactID>
python viber_analyze.py --input export/viber_export.json --chat 10 --list-senders

# 3. Analyse one sender in that chat
python viber_analyze.py --input export/viber_export.json --chat 10 --sender viber:2 \
    --chatlens-url http://10.0.0.10:5173/ --workers 3
```

`--chatlens-url` must be the exact address you open ChatLens at (same host, port, path and
query), so that report links reuse an open ChatLens tab. Only identifiers go into the link
(`#conversationId=viber:10&messageId=203`), never message text.

**Output** (in `--output`, default `viber-analysis/`):
`report.html` and `report.md` (verified findings with context and links), `history.html` (the
whole conversation as a static page; each message has an anchor), `findings.json`,
`findings.csv`, `rejected_findings.json` (what the model returned that failed verification),
`summary.json` (coverage and token usage) and `batches/` (the cache).

**Resuming.** Every batch is cached. Run the same command again to continue after an
interruption or a failed batch; `--workers` and `--max-batches` can change between runs. If
the export, chat, sender, model, `--batch-size` or `--context` changes, the script refuses to
mix results: use a new `--output`. `--report-only` rebuilds the reports from the cache
without any API call.

**Checks.** A finding is kept only if its message is in the batch's scored (TARGET) window,
was written by the selected sender, and its quote appears word for word in that message.
Messages without text (photos, stickers, calls, locations, system notices) are context only.

## Tests

```bash
python -m unittest discover -v
```

Tests use synthetic data and a fake API client; they need no network and no key.
`chatlens_links.py` builds the ChatLens URLs; `deep_link_contract.json` is copied from
ChatLens, where the same file pins the URL format (keep the two in step).

## Credits

`chatlens_links.py` and `deep_link_contract.json` come from
[sVathis/chatlens](https://github.com/sVathis/chatlens), which is MIT-licensed (a fork of
`thongtech/skypelens`).
