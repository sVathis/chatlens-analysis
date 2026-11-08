# chatlens-analysis

Evidence-oriented, resumable analysis of one chat conversation, for conversations from
[ChatLens](https://github.com/sVathis/chatlens)-supported apps. A model reads the messages
in batches and flags **candidate** personally insulting language from one selected sender.
Every finding is checked against the source text, and every finding in the reports links back
to the exact message in ChatLens.

| Folder | Reads | Status |
|---|---|---|
| [`viber/`](viber/README.md) | `viber_export.json` from [ViberPC-Export](https://github.com/sVathis/ViberPC-Export) | **not yet run on real data** |
| [`skype/`](skype/README.md) | a single extracted Skype conversation (`skype_extract.py` output) | in use; has its own tests |

> [!WARNING]
> **Privacy.** Unlike ChatLens (which runs entirely in your browser), these scripts send data
> out: running an analysis sends the **text, sender names and times** of the selected
> conversation, in batches, to the OpenAI API. Listing chats or senders and `--report-only`
> never use the network. Use this only on conversations you are entitled to analyse. The
> output folders hold private conversation text: they are in `.gitignore`, and this
> repository should stay private.

The findings are indications for human review. They are not statements about intent or legal
liability, and the reports say so.

## Layout

```
chatlens_links.py          shared: builds the ChatLens links used in the reports
deep_link_contract.json    shared: the URL format, copied from ChatLens
test_chatlens_links.py     tests for the shared helper
viber/                     the Viber analysis (its own README)
skype/                     the Skype analysis (its own README, tests and requirements)
```

`viber/viber_analyze.py` imports `chatlens_links.py` from the repository root, so run it from
the root (`python viber/viber_analyze.py ...`). `skype/skype_analyze.py` builds its SkypeLens
links itself (`--skypelens-url`, report links in a tab named `skypelens`, which ChatLens treats
like `chatlens`) and does not import the shared helper; run it as its README describes.

## Tests

```bash
python -m unittest discover -v              # the shared link helper
python -m unittest discover -s skype/tests -v   # the Skype analysis
python -m unittest discover -s viber -v     # the Viber analysis (once it has tests)
```

CI runs both on Python 3.10 and 3.13. `deep_link_contract.json` is copied from ChatLens,
where the same file pins the URL format: keep the two in step.

## Credits

`chatlens_links.py` and `deep_link_contract.json` come from
[sVathis/chatlens](https://github.com/sVathis/chatlens), which is MIT-licensed (a fork of
`thongtech/skypelens`).
