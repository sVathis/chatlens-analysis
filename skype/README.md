# Skype conversation evidence review (Greek)

A local, resumable Python command-line pipeline that analyzes **one extracted** Skype `conversation.json`, identifies **candidate** offensive messages from a selected sender, verifies that each quote exists verbatim in its originating message, and creates HTML, Markdown, CSV and JSON reports. The model cannot establish subjective intent or legal liability; review every finding and its context manually.

## Requirements

- Python 3.10 or newer
- An OpenAI API key with billing enabled
- `conversation.json` exported using `skype_extract.py` (one conversation, with a `conversations[0].MessageList` or `messages` array)

```powershell
py -m pip install -r requirements.txt
```

## Step 0: Extract one conversation

From the `skype/` directory, list conversations and extract the one you want using its exact ID:

```powershell
python skype_extract.py ".\path\to\messages.json" --list
python skype_extract.py ".\path\to\messages.json" --contact "EXACT_CONVERSATION_ID" --output-dir ".\exports"
```

The extractor writes `conversation.json` and a readable `conversation.md` in a folder named
after the conversation with an ID-derived suffix; use the path printed by the extractor in
the following commands. For missing or duplicate IDs, `--list` shows a one-based `--index`
that selects and distinguishes the conversation. Those suffixes depend on export order.
Use `--split year` or `--split month` to split only the Markdown.
Extraction is local and needs no API key. The JSON preserves the original message order and
metadata; Markdown is a lossy, chronological view. Existing output files are not overwritten:
choose a different `--output-dir` to extract again. On a failed write the extractor removes files
it created in that attempt; after a forced termination, check for partial files before retrying.
Keep the original `conversation.json` for any existing analysis: re-extraction can change its
bytes and keys, causing the cached run's SHA-256 fingerprint check to reject it.
The extractor reads the entire export into memory. The export and both extracted files contain
private data; keep them out of version control. On POSIX systems, extracted files are created
with owner-only permissions; on Windows, access depends on the output folder's ACL.
Raw message text in `conversation.md` can resemble message headings, so treat it as a reading
copy and verify ambiguous boundaries against `conversation.json` before using it with an LLM.
Markdown uses the analyzer's timestamp fields (`originalarrivaltime`, `composetime`, and
`createdTime`); a message with only `composeTime` is undated in both views.

## Step 1: Identify the exact sender ID

```powershell
python skype_analyze.py --input ".\exports\EXTRACTED_FOLDER\conversation.json" --list-senders
```

Use the **`from` sender ID** displayed here, **not necessarily the conversation ID**. It is important not to mix the two.

## Step 2: Set your key for this terminal only

```powershell
$env:OPENAI_API_KEY = "your-api-key"
```

For added security, avoid putting API keys in source code or committed files. The script sends messages to OpenAI over HTTPS when analyzing, so processing is *not fully offline*. Review your privacy requirements before running it.

## Step 3: Start a small, paid API test

```powershell
python skype_analyze.py --input ".\exports\EXTRACTED_FOLDER\conversation.json" `
  --sender "EXACT_SENDER_ID_FROM_STEP_1" `
  --output ".\skype-analysis" `
  --model gpt-6-luna `
  --max-batches 2
```

The default batch contains 60 **target-position messages**, plus 5 messages of context on each side. All messages are in exactly one target window; only messages by `--sender` are scored. The remaining messages are retained for context. An API call processes each nonempty batch. `--max-batches 2` only processes two new batches, for testing, and emits *partial* reports.

## Step 4: Complete or resume

Run the **same command without `--max-batches`**:

```powershell
python skype_analyze.py --input ".\exports\EXTRACTED_FOLDER\conversation.json" `
  --sender "EXACT_SENDER_ID_FROM_STEP_1" `
  --output ".\skype-analysis" `
  --model gpt-6-luna
```

Completed batches are cached. A restart avoids rerunning them and paying twice. To change model, prompt, source input, batch settings, or sender, use a new output directory. The pipeline rejects mixed run configurations.

## Outputs

- `report.html`: readable report with the original quotations, dates, categories and adjacent message context
- `report.md`: equivalent Markdown report suitable for Logseq, searching and LLM uploads
- `findings.csv`: UTF-8 CSV for spreadsheet import
- `findings.json`: structured, verified findings including surrounding message context
- `rejected_findings.json`: model candidates rejected because their reference or exact quote did not match
- `summary.json`: message coverage and API usage reported by the service
- `run_config.json`: source file SHA-256 and configuration to protect against accidental mixed runs
- `batches/`: resumable per-batch analysis data

To reconstruct reports without calling the API again, supply the same options plus `--report-only`.

## Limitations and safeguards

1. **No automatic exhaustive truth guarantee:** every batch is sent, but a model can overlook relevant language. `summary.json` shows coverage of messages submitted, not recall of all possible offenses.
2. **Not a legal assessment:** candidates can be false positives. The script doesn't independently verify interpretation, threat severity, or attribution beyond the sender ID.
3. **Source preservation:** `conversation.json` stays untouched; the report includes original Skype message ID if available, plus a stable internal key. Source-derived cleaned plain text, not the original HTML, is analyzed.
4. **Images, attachments and audio are not analyzed.** HTML-derived text is analyzed; rich text formatting may be lost.
5. **Long texts:** content is truncated at 30,000 characters per message for API transmission. Very large singular messages may need manual review.
6. **Boundary context:** 5 adjacent messages are provided on each side. Messages with distant contextual references may require manual inspection.
7. **Cost:** each uncached batch uses billable OpenAI API tokens. Token usage is accumulated in `summary.json`. Costs depend on the selected model and API pricing.
8. **Data safety:** the original source and analysis results contain personal information. Restrict local access and do not publish these files.
9. **Conservative output verification:** if the model alters a quotation, the finding is rejected and preserved separately for manual inspection. Such rejection is not proof that the underlying message is innocuous. A quote that differs only in whitespace (extra spaces, line breaks, non-breaking spaces) or in how an accent is stored is accepted, and the report then shows the message's own text. Those findings have `"quote_match": "normalized"` in `findings.json` (`"exact"` otherwise); changed words, letters or punctuation are still rejected.
10. **Spreadsheet formulas:** CSV fields preserve source data, so a value beginning with `=`, `+`, `-`, or `@` may be interpreted as a formula by spreadsheet software. Import those fields as text or use the JSON output.
11. **Markdown HTML:** `report.md` preserves untrusted text, which may include raw HTML. Use a Markdown viewer that sanitizes HTML, or open the escaped `report.html` instead.

## Backlinks to original exported conversation

This edition additionally creates `history.html`, containing all messages in the extracted conversation, with stable fragment anchors such as `#m0000123`. Every verified finding links to its corresponding full-history message from `report.html`, `report.md`, `findings.json` (`source_url`) and `findings.csv` (`source_url`). Place the generated files together in the same output directory; open `report.html` or `report.md` and follow its links to `history.html`. The selected message is highlighted. To generate this after a previous run without paying for API calls, use exactly the original input/sender/model/output arguments with `--report-only`.

The identifiers `m0000123` are stable only for the same exported `conversation.json` in the same original message order. Do not regenerate the extraction with different ordering without regenerating the reports. HTML content is escaped and remains entirely local. The HTML does not embed or recreate original media files.

## ChatLens links (`--chatlens-url`)

This version can also add a second, optional hyperlink **to the exact original Skype message** in [ChatLens](https://github.com/sVathis/chatlens), without removing the offline `history.html#...` link. (This replaces the old `--skypelens-url` option and the `skypelens_url` field: SkypeLens became ChatLens.)

ChatLens base URL example: `http://10.0.0.10:5173/`. Use exactly the address you open ChatLens at (same host, port, path and query). The link format is:

```
http://10.0.0.10:5173/#conversationId=8%3Alive%3A.cid.0123456789abcdef&messageId=1700000000001
```

Regenerate previously cached reports **without sending any content to the API** by running the original arguments with `--report-only` and adding the ChatLens base URL:

```powershell
python skype_analyze.py `
  --input ".\exports\EXTRACTED_FOLDER\conversation.json" `
  --sender "8:live:.cid.0123456789abcdef" `
  --model "gpt-5-mini" `
  --output ".\skype-analysis" `
  --chatlens-url "http://10.0.0.10:5173/" `
  --report-only
```

**Important:** Keep `--model`, `--batch-size`, `--context`, `--sender`, `--input` and `--output` identical to the original analysis run (if you used nondefault batch settings, supply them again). Changing only `--chatlens-url` does *not* alter the analysis fingerprint, so the cached results are reused.

This creates:

- `report.html`: **Προβολή στο ChatLens** (named browser tab `chatlens`) and **Προβολή στο πλήρες ιστορικό** links for each finding.
- `report.md`: both links as regular Markdown links; suitable for Logseq.
- `findings.json` and `findings.csv`: retains `source_url` pointing to `history.html`, and adds `chatlens_url` for the new link (empty when `--chatlens-url` is omitted).
- `history.html`: unchanged standalone locally searchable-through-browser transcript with fragment anchors.

The ChatLens URL is constructed from the **original `conversations[0].id`** and each original `MessageList[].id` in the source JSON; it does *not* use the selected `--sender` or an internal `m0000123` key. Only identifiers go into the URL, never message text. If a message lacks an original Skype ID, it will have only the `history.html` link. The link is built by the shared [`chatlens_links.py`](../chatlens_links.py) in the repository root, which pins the URL format together with ChatLens. The base URL can include a hosted application subpath, which is preserved. Only `http` and `https` URLs are accepted. Omit `--chatlens-url` to keep the offline-only reports.

Reports and findings made before the rename, with `skypelens_url` and `target="skypelens"`, still work: ChatLens treats the old tab name like `chatlens`. Run `--report-only` with `--chatlens-url` to regenerate them with the new names.

**Availability and privacy:** Links using `10.0.0.10` resolve when your device can reach that LAN address (or over a suitable VPN); the pipeline does not publish ChatLens or the conversations. Opening a link still requires the matching Skype export to be loaded in ChatLens. Do not publish the HTML/Markdown findings or links with private material without appropriate controls.

Run the included no-network tests with:

```powershell
python -m unittest discover -s tests -v
```

### Same-tab navigation for findings

`report.html` uses `target="chatlens"` on every ChatLens link. A ChatLens tab opened by the report is reused for subsequent clicks; because only the fragment changes, the in-memory Skype export stays loaded. The first click may require loading the matching export. Do **not** open ChatLens manually in a separate tab and expect `target="chatlens"` to target that tab; the report should open it.

`report.md` contains the same URLs but Markdown has no portable way to specify a named browser tab. Whether a Markdown viewer opens a new tab depends on that viewer's behavior; ChatLens's cross-tab handoff may mitigate this.

The named-tab links intentionally do **not** use `rel="noopener"`/`noreferrer` (they prevent named-tab reuse in browsers). Open the generated HTML locally from a trusted location, and only point `--chatlens-url` at a ChatLens you trust; the reports may contain sensitive conversation excerpts.

Existing `history.html#...` backlinks are kept as a standalone fallback.

## Parallel API execution (new)

The script supports `--workers N` (default `1`, maximum `16`). For existing runs,
start with `--workers 4` and adjust downward to `2` if API rate limits cause
retries. Batch analysis results are written separately in `batches/batch-NNNNN.json`.
Completed batches are reused on restart, even if you change `--workers`.
Do **not** run two copies of the script using the same `--output` directory at
the same time. Stop an existing sequential process before switching to this
version. Ctrl+C may wait briefly for in-flight requests to finish.

Run on PowerShell:

```powershell
python skype_analyze.py `
  --input '.\exports\EXTRACTED_FOLDER\conversation.json' `
  --sender '8:live:.cid.0123456789abcdef' `
  --model 'gpt-5-mini' `
  --output '.\skype-analysis' `
  --chatlens-url 'http://10.0.0.10:5173/' `
  --workers 4
```

`--max-batches 2` now restricts **new** API batches in that invocation, not
already-cached batches. `--report-only` remains free of API calls and does not
require an API key. Parallel execution does not alter the message context, the
analysis model, the prompt version, or the run fingerprint; it improves latency
but not the token cost of the same batches. The SDK retries transient failures;
the pipeline additionally backs off with randomized delays. If the API applies
strict requests-per-minute or tokens-per-minute limits, more workers can slow
completion due to retries rather than improve it.

## Recovery from malformed or truncated JSON (2026-11-02 update)

If a request fails with `Expecting ',' delimiter` or an incomplete response,
this version automatically retries just the affected batch with progressively
larger `max_output_tokens` (6000, 12000, 18000). If the API repeatedly returns
invalid/incomplete JSON, the script divides **only that batch's TARGET range**
into two smaller ranges, includes the normal context around each, analyzes
them independently, and recombines their findings into the **original**
`batch-XXXXX.json` cache record. It recursively splits further if necessary.

Previously completed batches are not reprocessed; the cache fingerprint and
output layout remain unchanged. It never marks a failed segment as covered.
Malformed/incomplete attempts are billable API requests, and the saved usage
summary only includes usage from successful responses, so the usage totals may
understate the actual API charges.

To retry just the first missing batch (typically batch 62 if you have the
failure shown in the log), use your original command with `--max-batches 1`.
Then remove `--max-batches` to continue all the remaining work. Continue to
use the same `--input`, `--sender`, `--model`, `--output`, `--batch-size` and
`--context` settings as before.
