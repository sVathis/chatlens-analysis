"""Synthetic tests for locating a model's quote in the source text."""
import unittest

from quote_match import EXACT, NORMALIZED, locate_quote


class LocateQuoteTests(unittest.TestCase):
    def test_exact_substring_is_returned_unchanged(self):
        self.assertEqual(locate_quote('is a test', 'This is a test.'), ('is a test', EXACT))

    def test_whitespace_differences_return_the_source_span(self):
        text = 'Start one  two three\nfour end'
        found = locate_quote('one two three four', text)
        self.assertEqual(found, ('one  two three\nfour', NORMALIZED))
        self.assertIn(found[0], text)

    def test_spaces_around_the_quote_are_ignored(self):
        self.assertEqual(locate_quote('  one two  ', 'x one\ttwo y'), ('one\ttwo', NORMALIZED))

    def test_decomposed_source_matches_a_composed_quote_and_the_reverse(self):
        composed, decomposed = 'κά', 'κά'
        self.assertNotEqual(composed, decomposed)
        self.assertEqual(locate_quote(composed, 'start ' + decomposed + ' end'), (decomposed, NORMALIZED))
        self.assertEqual(locate_quote(decomposed, 'start ' + composed + ' end'), (composed, NORMALIZED))

    def test_span_covers_whole_source_characters(self):
        text = 'a άβ b'      # alpha + combining acute, then beta
        self.assertEqual(locate_quote('άβ', text), ('άβ', NORMALIZED))

    def test_changed_words_letters_or_punctuation_are_not_accepted(self):
        text = 'You are wrong about this.'
        for quote in ('You are mistaken about this', 'you are wrong about this',
                      'You are wrong about this!', 'about this you are wrong',
                      'You are wrong, about this'):
            with self.subTest(quote=quote):
                self.assertIsNone(locate_quote(quote, text))

    def test_words_are_not_joined_or_split(self):
        self.assertIsNone(locate_quote('one two', 'onetwo'))
        self.assertIsNone(locate_quote('onetwo', 'one two'))

    def test_greek_and_latin_look_alike_letters_are_not_accepted(self):
        self.assertIsNone(locate_quote('ΑI', 'ΑΙ'))     # Greek A + Latin I vs Greek A + Greek I

    def test_empty_and_non_text_input_is_not_a_match(self):
        for quote, text in (('', 'text'), (None, 'text'), ('quote', None), ('   ', 'no gaps')):
            with self.subTest(quote=quote, text=text):
                self.assertIsNone(locate_quote(quote, text))


if __name__ == '__main__':
    unittest.main()
