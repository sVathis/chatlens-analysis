"""Find a model-supplied quote in the source text without trusting the model's copy.

The analysis keeps a finding only when its quote really is in the message. An exact match
is always preferred. When it fails, the quote is still accepted if it differs from the
message only in whitespace (runs of spaces, tabs, line breaks, non-breaking spaces) or in
Unicode form (the same text with accents stored composed or decomposed). The model often
changes exactly these invisible details when it repeats a quote.

The text returned is always the exact span of the source message, never the model's copy,
so a kept quote is word for word what the sender wrote. A quote whose words, order,
letters or punctuation differ is not accepted.
"""
import re
import unicodedata

EXACT = "exact"
NORMALIZED = "normalized"


def _normalized_with_offsets(text):
    """NFC form of ``text`` and, for each of its characters, the (start, end) span it
    came from in ``text``. A base character and the combining marks after it are
    normalized together, so every normalized character maps back to whole source
    characters."""
    pieces, spans = [], []
    index, length = 0, len(text)
    while index < length:
        end = index + 1
        while end < length and unicodedata.combining(text[end]):
            end += 1
        for char in unicodedata.normalize("NFC", text[index:end]):
            pieces.append(char)
            spans.append((index, end))
        index = end
    return "".join(pieces), spans


def locate_quote(quote, text):
    """Return ``(source_text, how)`` for the span of ``text`` that ``quote`` matches.

    ``how`` is ``"exact"`` (the quote is a substring) or ``"normalized"`` (only whitespace
    or Unicode form differs). Returns ``None`` if the quote is empty or not in the text.

    Whitespace is compared as a run: the quote is cut into tokens at whitespace and the
    tokens must appear in the source in the same order, separated by at least one
    whitespace character of any kind. Punctuation stays attached to its token, so
    whitespace added or removed next to punctuation is a difference, not a match
    (``"a, b"`` does not match ``"a ,b"``, and ``"end. Next"`` does not match ``"end.Next"``).
    """
    if not isinstance(quote, str) or not isinstance(text, str) or not quote:
        return None
    if quote in text:
        return quote, EXACT
    words = unicodedata.normalize("NFC", quote).split()
    if not words:
        return None
    normalized, spans = _normalized_with_offsets(text)
    found = re.search(r"\s+".join(re.escape(word) for word in words), normalized)
    if not found:
        return None
    return text[spans[found.start()][0] : spans[found.end() - 1][1]], NORMALIZED
