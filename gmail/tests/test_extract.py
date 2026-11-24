"""Offline tests for gmail_extract.py: synthetic emails only, no network.

Run from the repository root:  python -m unittest discover -s gmail/tests -v
"""
import contextlib
import importlib.util
import io
import json
import mailbox
import os
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / 'gmail_extract.py'
spec = importlib.util.spec_from_file_location('gmail_extract', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class StripQuotedTests(unittest.TestCase):
    def own(self, text):
        return mod.strip_quoted(text)[0]

    def test_text_without_quotes_is_kept(self):
        self.assertEqual(self.own('Hello,\n\nsee you tomorrow.\n'), 'Hello,\n\nsee you tomorrow.')
        self.assertEqual(mod.strip_quoted('Plain text')[1], 0)

    def test_gmail_english_attribution_even_when_wrapped(self):
        text = ('My answer.\n\nOn Mon, 1 Jan 2024 at 10:00, Alice Example <\n'
                'alice@example.com> wrote:\n\n> Earlier text\n> more\n')
        own, removed = mod.strip_quoted(text)
        self.assertEqual(own, 'My answer.')
        self.assertGreater(removed, 0)

    def test_gmail_greek_attribution(self):
        text = ('Η απάντησή μου.\n\nΣτις Δευ 1 Ιαν 2024 στις 10:00 π.μ., ο/η Alice '
                '<alice@example.com> έγραψε:\n\n> Παλαιότερο κείμενο\n')
        self.assertEqual(self.own(text), 'Η απάντησή μου.')

    def test_outlook_header_blocks(self):
        english = ('Reply text\n\n________________________________\nFrom: Alice <alice@example.com>\n'
                   'Sent: Monday, January 1, 2024 10:00 AM\nTo: Bob\nSubject: Re: x\n\nOld text\n')
        greek = ('Κείμενο απάντησης\n\nΑπό: Alice <alice@example.com>\nΣτάλθηκε: Δευτέρα, 1 Ιανουαρίου 2024\n'
                 'Προς: Bob\n\nΠαλιό κείμενο\n')
        separator = 'New text\n\n-----Original Message-----\nFrom: Alice\nOld text\n'
        self.assertEqual(self.own(english), 'Reply text')
        self.assertEqual(self.own(greek), 'Κείμενο απάντησης')
        self.assertEqual(self.own(separator), 'New text')

    def test_forwarded_messages_are_removed(self):
        for marker in ('---------- Forwarded message ---------',
                       '---------- Προωθημένο μήνυμα ---------', 'Begin forwarded message:'):
            with self.subTest(marker=marker):
                self.assertEqual(self.own(f'FYI\n\n{marker}\nFrom: Carol\nForwarded text\n'), 'FYI')

    def test_a_pure_forward_has_no_own_text(self):
        self.assertEqual(self.own('---------- Forwarded message ---------\nFrom: Carol\nText\n'), '')

    def test_inline_quote_lines_are_removed_and_answers_kept(self):
        text = '> Question one?\nAnswer one.\n> Question two?\nAnswer two.'
        self.assertEqual(self.own(text), 'Answer one.\nAnswer two.')

    def test_signature_and_phone_footers_are_removed(self):
        self.assertEqual(self.own('Text\n\n-- \nAlice\n+30 210 0000000'), 'Text')
        self.assertEqual(self.own('Text\n\nSent from my iPhone'), 'Text')
        self.assertEqual(self.own('Κείμενο\n\nΣτάλθηκε από το iPhone μου'), 'Κείμενο')

    def test_a_sentence_mentioning_on_or_wrote_is_kept(self):
        text = 'On Monday I wrote: the report is late.\nOn second thought, fine.'
        self.assertEqual(self.own(text), text)

    def test_narrative_text_with_wrote_is_kept(self):
        for text in ('Στις 10/11 πήγα στο σχολείο.\nΗ δασκάλα έγραψε:\nΑυτό δεν ισχύει.',
                     'On Monday the teacher\nwrote:\nThat is not true.',
                     'On Monday the teacher wrote:\nThat is not true.',
                     'Στις 10:00 η δασκάλα έγραψε:\nΤίποτα.'):
            with self.subTest(text=text):
                self.assertEqual(self.own(text), text)

    def test_attributions_without_an_address_need_a_dated_time(self):
        for header in ('On 1/1/2024 10:00, Alice wrote:',
                       'On Monday, January 1, 2024 10:00 AM, Alice wrote:',
                       'Στις Δευ 1 Ιαν 2024 στις 10:00 π.μ., ο/η Alice έγραψε:'):
            with self.subTest(header=header):
                self.assertEqual(self.own(f'Reply.\n\n{header}\n> old'), 'Reply.')

    def test_greek_attribution_starting_with_the_name(self):
        text = 'Απάντηση.\n\nΟ/Η Alice <alice@example.com> έγραψε στις 1/1/2024:\n> παλιό'
        self.assertEqual(self.own(text), 'Απάντηση.')

    def test_yahoo_attribution_followed_by_the_quote_on_the_same_line(self):
        for header in ('Στις Τρίτη, 5 Μαΐου 2020 στις 10:00, Alice Example έγραψε:',
                       'On Tuesday, May 5, 2020, 10:00:00 AM GMT+3, Alice Example wrote:'):
            with self.subTest(header=header):
                self.assertEqual(self.own(f'Reply.\n\n{header}   Old text here.\nMore old text.'),
                                 'Reply.')

    def test_inline_narrative_with_wrote_is_kept(self):
        for text in ('Στις 10/11 πήγα στο σχολείο. Η δασκάλα έγραψε: αυτό δεν ισχύει.',
                     'On Monday at 10:00 the teacher wrote: that is not true.'):
            with self.subTest(text=text):
                self.assertEqual(self.own(text), text)

    def test_yahoo_forward_header_on_the_separator_line(self):
        text = ('Στάλθηκε από το Ταχυδρομείο Yahoo για Android\n\n'
                '-----Προωθημένο μήνυμα----- Από: "Carol" <carol@example.net> Προς: x\nForwarded text')
        self.assertEqual(self.own(text), '')
        self.assertEqual(self.own('New.\n-----Original Message----- From: Carol\nOld'), 'New.')

    def test_yahoo_tagline_footer(self):
        self.assertEqual(self.own('Text\n\nYahoo Mail: Search, organise, conquer'), 'Text')

    def test_windows_line_ends_and_blank_runs_are_tidied(self):
        self.assertEqual(self.own('a\r\n\r\n\r\n\r\nb  \r\n'), 'a\n\nb')


class RepeatedBlockTests(unittest.TestCase):
    SIGNATURE = 'Alice Example\nExample Ltd, Main St 1\nTel. +30 210 0000000'
    DISCLAIMER = 'This message (including attachments) is confidential. ' * 3

    def emails(self, texts, sender='alice@example.com'):
        return [{'from': sender, 'text': t, 'removed_chars': 0} for t in texts]

    def test_a_signature_and_disclaimer_repeated_in_many_emails_are_removed(self):
        texts = [f'Message {i}.\n\n{self.SIGNATURE}\n\n{self.DISCLAIMER}\n\nSent from my iPhone'
                 for i in range(10)]
        emails = self.emails(texts)
        self.assertEqual(mod.strip_repeated_blocks(emails), 10)
        self.assertEqual([e['text'] for e in emails], [f'Message {i}.' for i in range(10)])
        self.assertTrue(all(e['removed_chars'] > 0 for e in emails))

    def test_short_repeated_phrases_and_rare_blocks_are_kept(self):
        texts = [f'Καλημέρα\nText {i}\nΕυχαριστώ' for i in range(12)]
        texts.append(f'Last.\n{self.SIGNATURE}')
        emails = self.emails(texts)
        self.assertEqual(mod.strip_repeated_blocks(emails), 0)
        self.assertEqual([e['text'] for e in emails], texts)

    def test_a_signature_alone_in_an_email_with_attachments_is_removed(self):
        texts = [f'Message {i}.\n{self.SIGNATURE}' for i in range(10)] + [self.SIGNATURE]
        emails = self.emails(texts)
        mod.strip_repeated_blocks(emails)
        self.assertEqual(emails[-1]['text'], '')

    def test_a_message_sent_many_times_on_its_own_is_kept(self):
        request = 'Please send the documents.\nI asked several times.\nThank you.'
        emails = self.emails([request] * 12 + [f'Other {i}' for i in range(3)])
        self.assertEqual(mod.strip_repeated_blocks(emails), 0)
        self.assertEqual(emails[0]['text'], request)

    def test_counted_per_sender_and_can_be_disabled(self):
        mixed = (self.emails([f'A {i}\n{self.SIGNATURE}' for i in range(5)])
                 + self.emails([f'B {i}\n{self.SIGNATURE}' for i in range(5)], 'bob@example.com'))
        self.assertEqual(mod.strip_repeated_blocks(mixed), 0)
        emails = self.emails([f'A {i}\n{self.SIGNATURE}' for i in range(10)])
        self.assertEqual(mod.strip_repeated_blocks(emails, min_emails=0), 0)


class HtmlTextTests(unittest.TestCase):
    def test_paragraphs_breaks_and_entities(self):
        text = mod.html_to_text('<html><head><style>p{color:red}</style></head><body>'
                                '<p>Hello &amp; welcome</p><div>Line<br>two</div></body></html>')
        self.assertEqual(text, 'Hello & welcome\nLine\ntwo')

    def test_gmail_quote_and_blockquote_are_skipped(self):
        markup = ('<div dir="ltr">My reply</div><br><div class="gmail_quote">'
                  '<div class="gmail_attr">On Mon, Alice wrote:</div>'
                  '<blockquote class="gmail_quote">Old <b>text</b></blockquote></div>')
        self.assertEqual(mod.html_to_text(markup), 'My reply')

    def test_text_after_a_skipped_element_is_kept(self):
        self.assertEqual(mod.html_to_text('<p>Before</p><blockquote><p>Old</p></blockquote><p>After</p>'),
                         'Before\nAfter')

    def test_outlook_reply_marker_stops_the_text(self):
        markup = ('<div>Reply text</div><hr><div id="divRplyFwdMsg"><b>From:</b> Alice</div>'
                  '<div>Old text</div>')
        self.assertEqual(mod.html_to_text(markup), 'Reply text')

    def test_unclosed_and_stray_tags_do_not_break_it(self):
        self.assertEqual(mod.html_to_text('<p>One<p>Two</span></p><div>Three'), 'One\nTwo\nThree')


class BodyTests(unittest.TestCase):
    def test_an_empty_plain_part_falls_back_to_the_html_part(self):
        msg = EmailMessage()
        msg.set_content('')
        msg.add_alternative('<p>Important message</p>', subtype='html')
        self.assertEqual(mod.body_of(msg), ('Important message', 'html'))

    def test_a_plain_part_with_text_is_preferred(self):
        msg = EmailMessage()
        msg.set_content('Plain text\n')
        msg.add_alternative('<p>HTML text</p>', subtype='html')
        self.assertEqual(mod.body_of(msg)[1], 'plain')
        self.assertEqual(mod.body_of(msg)[0].strip(), 'Plain text')

    def test_an_empty_plain_only_email_stays_empty(self):
        msg = EmailMessage()
        msg.set_content('')
        self.assertEqual(mod.body_of(msg)[0].strip(), '')


class TimeTests(unittest.TestCase):
    def test_date_header_becomes_utc(self):
        self.assertEqual(mod.iso_time('Mon, 01 Jan 2024 12:00:00 +0200'), '2024-01-01T10:00:00Z')
        self.assertEqual(mod.iso_time('Mon, 01 Jan 2024 12:00:00 -0000'), '2024-01-01T12:00:00Z')

    def test_unusable_dates_are_empty(self):
        for bad in ('', 'not a date', None):
            with self.subTest(bad=bad):
                self.assertEqual(mod.iso_time(bad), '')


def make_email(sender, to, subject, date, thread, body=None, html=None, labels='Inbox',
               message_id=None, attachment=None):
    msg = EmailMessage()
    msg['From'] = sender
    msg['To'] = to
    msg['Subject'] = subject
    msg['Date'] = date
    msg['Message-ID'] = message_id or f'<{abs(hash((sender, date)))}@example.test>'
    msg['X-GM-THRID'] = thread
    msg['X-Gmail-Labels'] = labels
    if body is not None:
        msg.set_content(body)
        if html is not None:
            msg.add_alternative(html, subtype='html')
    else:
        msg.set_content(html, subtype='html')
    if attachment:
        msg.add_attachment(b'%PDF-1.4 synthetic', maintype='application', subtype='pdf',
                           filename=attachment)
    return msg


ALICE = 'Alice Example <Alice@Example.com>'
ME = 'Me Myself <me@example.org>'


class ExtractTests(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)
        self.mbox = self.tmp / 'Takeout.mbox'
        box = mailbox.mbox(str(self.mbox))
        emails = [
            # Thread 100: Alice writes, I answer, Alice answers quoting me (with an attachment)
            make_email(ALICE, ME, 'Θέμα συνάντησης', 'Mon, 01 Jan 2024 10:00:00 +0000', '100',
                       body='Πρώτο μήνυμα της Alice.\n', message_id='<a1@example.test>'),
            make_email(ME, ALICE, 'Re: Θέμα συνάντησης', 'Mon, 01 Jan 2024 11:00:00 +0000', '100',
                       body='Η απάντησή μου.\n\nOn Mon, 1 Jan 2024, Alice <alice@example.com> wrote:\n'
                            '> Πρώτο μήνυμα της Alice.\n', labels='Sent', message_id='<m1@example.test>'),
            make_email(ALICE, ME, 'Re: Θέμα συνάντησης', 'Mon, 01 Jan 2024 12:00:00 +0000', '100',
                       body='Νέο κείμενο.\n\nΣτις Δευ 1 Ιαν 2024, ο/η Me <me@example.org> έγραψε:\n'
                            '> Η απάντησή μου.\n',
                       html='<div>Νέο κείμενο.</div><div class="gmail_quote">old</div>',
                       message_id='<a2@example.test>', attachment='σχέδιο.pdf'),
            # Thread 200: only me and Bob, no email from Alice
            make_email(ME, 'bob@example.net', 'Other', 'Tue, 02 Jan 2024 09:00:00 +0000', '200',
                       body='To Bob.\n', labels='Sent'),
            make_email('bob@example.net', ME, 'Re: Other', 'Tue, 02 Jan 2024 10:00:00 +0000', '200',
                       body='From Bob.\n'),
            # Thread 50: an HTML-only email from Alice, earlier than thread 100
            make_email(ALICE, ME, 'HTML only', 'Sun, 31 Dec 2023 08:00:00 +0000', '50',
                       html='<p>Μόνο HTML</p><blockquote>quoted</blockquote>',
                       message_id='<a0@example.test>'),
        ]
        for item in emails:
            box.add(item)
        box.close()
        self.out = self.tmp / 'out'

    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mod.main([str(a) for a in argv])
        return out.getvalue()

    def extract(self, *extra):
        self.run_main(self.mbox, '--sender', 'ALICE@example.com', '--output-dir', self.out, *extra)
        [folder] = list(self.out.iterdir())
        return folder, json.loads((folder / 'conversation.json').read_text(encoding='utf-8'))

    def test_list_senders_counts_and_marks_you(self):
        lines = self.run_main(self.mbox, '--list-senders').splitlines()
        self.assertIn('alice@example.com', lines[0])
        self.assertTrue(lines[0].strip().startswith('3'))
        mine = [line for line in lines if 'me@example.org' in line]
        self.assertEqual(len(mine), 1)
        self.assertIn('(you)', mine[0])
        self.assertTrue(all('(you)' not in line for line in lines if 'bob@' in line))

    def test_keeps_only_threads_with_the_sender_in_time_order(self):
        _folder, data = self.extract()
        self.assertEqual(data['format'], 'chatlens-gmail-extract')
        self.assertEqual(data['version'], 1)
        self.assertEqual(data['sender'], 'alice@example.com')
        self.assertEqual(data['self'], ['me@example.org'])
        self.assertEqual([t['id'] for t in data['threads']], ['50', '100'])
        thread = data['threads'][1]
        self.assertEqual(thread['subject'], 'Θέμα συνάντησης')
        self.assertEqual([m['from'] for m in thread['messages']],
                         ['alice@example.com', 'me@example.org', 'alice@example.com'])
        self.assertEqual([m['time'] for m in thread['messages']],
                         ['2024-01-01T10:00:00Z', '2024-01-01T11:00:00Z', '2024-01-01T12:00:00Z'])

    def test_all_threads_keeps_the_others(self):
        _folder, data = self.extract('--all-threads')
        self.assertEqual([t['id'] for t in data['threads']], ['50', '100', '200'])

    def test_only_the_new_text_is_kept(self):
        _folder, data = self.extract()
        first, reply, second = data['threads'][1]['messages']
        self.assertEqual(first['text'], 'Πρώτο μήνυμα της Alice.')
        self.assertEqual(reply['text'], 'Η απάντησή μου.')
        self.assertEqual(second['text'], 'Νέο κείμενο.')
        self.assertGreater(second['removed_chars'], 0)
        self.assertEqual(second['body'], 'plain')
        self.assertEqual(second['attachments'], ['σχέδιο.pdf'])
        self.assertEqual(second['message_id'], '<a2@example.test>')
        self.assertEqual(reply['labels'], ['Sent'])

    def test_html_only_email_is_read_as_text(self):
        _folder, data = self.extract()
        [only] = data['threads'][0]['messages']
        self.assertEqual(only['body'], 'html')
        self.assertEqual(only['text'], 'Μόνο HTML')

    def test_markdown_copy_and_private_files(self):
        folder, _data = self.extract()
        markdown = (folder / 'conversation.md').read_text(encoding='utf-8')
        self.assertIn('Θέμα συνάντησης', markdown)
        self.assertIn('Νέο κείμενο.', markdown)
        self.assertNotIn('> Η απάντησή μου.', markdown)
        if os.name == 'posix':
            self.assertEqual((folder / 'conversation.json').stat().st_mode & 0o777, 0o600)

    def test_existing_output_is_never_overwritten(self):
        self.extract()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.run_main(self.mbox, '--sender', 'alice@example.com', '--output-dir', self.out)

    def test_unknown_sender_and_missing_file_are_errors(self):
        for argv in ([self.mbox, '--sender', 'nobody@example.com'],
                     [self.tmp / 'missing.mbox', '--list-senders']):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.run_main(*argv, '--output-dir', self.out)
        self.assertFalse(self.out.exists())

    def test_self_can_be_given(self):
        lines = self.run_main(self.mbox, '--list-senders', '--self', 'bob@example.net').splitlines()
        self.assertIn('(you)', [line for line in lines if 'bob@' in line][0])


class CleanTests(unittest.TestCase):
    def test_escaped_utf8_bytes_are_restored_and_lone_surrogates_replaced(self):
        escaped = 'Καλη'.encode('utf-8').decode('ascii', 'surrogateescape')
        self.assertEqual(mod.clean(escaped), 'Καλη')
        self.assertEqual(mod.clean('a\udc80b'), 'a�b')
        self.assertEqual(mod.clean('a\ud800b'), 'a�b')
        self.assertEqual(mod.clean(None), '')


if __name__ == '__main__':
    unittest.main()
