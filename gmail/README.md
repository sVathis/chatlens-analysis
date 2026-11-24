# Email (Gmail) analysis

`gmail_extract.py` reads the `.mbox` file of a Google Takeout (Mail) export and extracts the
email threads with **one sender**. `gmail_analyze.py` analyses that sender's emails and writes
the same reports as the Skype and Viber analyses. Each finding links to the email in Gmail
(a search for its Message-ID) and to `history.html`. Status: tested on synthetic emails only;
findings are candidates for human review.

> [!WARNING]
> **Privacy.** Running an analysis sends the **new text, sender names and addresses, recipient
> addresses (To, Cc, and yours), subjects and times** of the extracted emails, in batches, to the OpenAI API (see the
> [main README](../README.md)). Extraction, `--list-senders` and `--report-only` never use the
> network. The mbox, the extracted folder and the output folder hold private email: keep them
> out of version control (they are in `.gitignore`).

## Setup

Python 3.10+. Nothing to install for extraction, listing, tests or `--report-only`; for a real
analysis:

```bash
python -m pip install -r gmail/requirements.txt
export OPENAI_API_KEY=...        # in your shell, never in a file
```

## 1. Export the emails from Gmail

1. In Gmail, search for the person, for example `from:person@example.com OR to:person@example.com`.
2. Select all results (including "Select all conversations that match this search") and apply
   a new label, for example `analysis-person`. With conversation view on (Gmail's default),
   the label goes on every email of each conversation, your replies included, which is what
   the analysis needs for context.
3. In [Google Takeout](https://takeout.google.com/), choose only **Mail**, then "All Mail data
   included" → only the new label. Download the archive and unpack the `.mbox` file.

Takeout writes each email once, with Gmail's thread ID (`X-GM-THRID`) and labels
(`X-Gmail-Labels`), so the threads can be rebuilt.

## 2. Extract the threads with the sender

Run these from the repository root.

```bash
python gmail/gmail_extract.py Takeout.mbox --list-senders
python gmail/gmail_extract.py Takeout.mbox --sender person@example.com
```

`--list-senders` lists each address with its email count; your own addresses (the senders of
emails labelled Sent) are marked `(you)`; pass `--self a@example.com,b@example.com` if that is
wrong. The extractor:

- keeps only the threads in which the sender wrote at least one email (`--all-threads` keeps
  every thread), with every email of those threads as context, in time order;
- keeps only each email's **own new text**. Removed: the quoted earlier messages of a reply
  (Gmail's "On … wrote:" and "Στις … έγραψε:", Outlook's "From:/Sent:" and "Από:/Στάλθηκε:"
  blocks, "-----Original Message-----", Yahoo's attribution with the quote on the same line),
  forwarded messages, `>` quote lines, signatures after `-- ` and app footers ("Sent from my
  iPhone", "Στάλθηκε από …", "Yahoo Mail: …"). This way every sentence is analysed once, under
  the person who wrote it;
- reports, and with `--strip-repeated` removes, signature and disclaimer blocks without a
  `-- ` line: a block of at least 3 lines that ends the email, whose lines ended at least 10 of
  the same sender's emails below the sender's own text. A request the sender sent many times
  looks the same, so this is opt-in: read `conversation.md` first, then extract again with
  `--strip-repeated` into a new `--output-dir` if the reported blocks are signatures;
- uses the plain-text version of an email, or its HTML version as text (without the quoted
  part) when there is no plain text. Attachments are listed by name but not extracted.

It writes `conversation.json` (for the analysis) and `conversation.md` (a reading copy that
shows exactly the text the model will see) to
`gmail-extracted/<address> [<hash>]/`. Existing output is never overwritten; on POSIX systems
the files are owner-only. Read a few emails in `conversation.md` before a real run: if a
client's quote format was missed, the earlier text would still be there. Each email in
`conversation.json` also keeps its full `original_text` (quotes, signatures and all; for an
HTML email, all its visible text, quoted parts included), so every removal can be checked;
`removed_chars` is its length minus that of `text`. The analysis never sends it.

## 3. Analyse

```bash
python gmail/gmail_analyze.py --input 'gmail-extracted/FOLDER/conversation.json' --list-senders
python gmail/gmail_analyze.py --input 'gmail-extracted/FOLDER/conversation.json' \
    --sender person@example.com --workers 3 --max-batches 1     # a first look
python gmail/gmail_analyze.py --input 'gmail-extracted/FOLDER/conversation.json' \
    --sender person@example.com --workers 3
```

Replace `FOLDER` with the folder the extractor printed. Batches default to 15 emails with
4 neighbouring emails of context on each side (`--batch-size`, `--context`), because emails are
longer than chat messages. Each email goes to the model with its thread number, subject and recipients (To and Cc),
together with your own addresses (the extract's `self`, from the Sent label or `--self`), so
the model can tell where one conversation ends and the next begins, and whether a sentence is
addressed to you or to someone else on the email. Language aimed only at another recipient or at a
third person is not a finding; when it is unclear who is meant (you only in Cc, several
recipients), the finding says so in its caveat with a lower confidence.

`--gmail-account` selects which signed-in Gmail account the links open: its number in the
`/mail/u/N/` address (default `0`), or its address. The link is
`https://mail.google.com/mail/u/0/#search/rfc822msgid%3A<Message-ID>`: an identifier only,
never text.

## Output

The same files as the Skype and Viber analyses, in `--output` (default `gmail-analysis/`):
`report.html`, `report.md`, `history.html` (all emails, thread by thread), `findings.json`,
`findings.csv`, `rejected_findings.json`, `summary.json` and the `batches/` cache. Resuming,
`--report-only` and the verification rules (TARGET window, selected sender, quote found word for
word in the email's own text, whitespace and Unicode form aside) work exactly as in
[`viber/`](../viber/README.md#output). Email findings have `gmail_message_id`,
`gmail_thread_id`, `subject` and `gmail_url` where chat findings have their message IDs and
`chatlens_url`; `triage_findings.py` reads them too.

## Limitations

The limits of the [Skype analysis](../skype/README.md#limitations-and-safeguards) apply. Also
specific to email: quote removal relies on the formats above, so an unusual client format can
leave quoted text in place (the reading copy shows it); everything after a reply header is
removed, so text written below the quote (bottom-posting, or answers between quoted lines after
an "On … wrote:" line) is removed with it; attachments and images are not
analysed; an email longer than 30,000 characters is cut; a reply header is recognised only with an
attribution's structure: an email address, or else a date with a year and a time *and* quoted
text after it, either `>` lines or the start of an earlier email of the same thread (this is
how Yahoo's "Στις …, Name έγραψε: quoted text" is found). A header without an address whose
quoted email is not in the export is kept as text, quote included (`conversation.md` shows
it); context at a batch edge can come from
the neighbouring thread (it is marked with its thread number).

CSV fields keep their original values, as in the Skype and Viber analyses: spreadsheet
software may read a subject, name or quote that begins with `=`, `+`, `-` or `@` as a formula.
Import `findings.csv` as text, or use `findings.json`. (`triage_findings.py --format csv` has no
message text and neutralises such cells.)

## Tests

```bash
python -m unittest discover -s gmail/tests -v
```

Tests use synthetic emails and a fake API client; they need no network and no key.
