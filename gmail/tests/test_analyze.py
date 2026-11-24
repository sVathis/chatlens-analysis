"""Offline tests for gmail_analyze.py: no network, no API key, synthetic data only.

Run from the repository root:  python -m unittest discover -s gmail/tests -v
"""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / 'gmail_analyze.py'
spec = importlib.util.spec_from_file_location('gmail_analyze', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

sys.path.insert(0, str(HERE.parent.parent))
import triage_findings  # noqa: E402  pylint: disable=wrong-import-position

ALICE = 'alice@example.com'
ME = 'me@example.org'


def email(index, sender, text, time, message_id=None, **over):
    item = {'index': index, 'message_id': message_id or f'<e{index}@example.test>',
            'thread_id': '', 'time': time, 'from': sender,
            'from_name': 'Alice' if sender == ALICE else 'Me', 'to': [], 'cc': [],
            'subject': '', 'in_reply_to': '', 'labels': [], 'body': 'plain', 'text': text,
            'removed_chars': 0, 'attachments': []}
    item.update(over)
    return item


def extract():
    """Two threads: 'Πρώτο' (Alice, me, Alice) and 'Δεύτερο' (me, Alice)."""
    return {
        'format': 'chatlens-gmail-extract', 'version': 1, 'source_sha256': '0' * 64,
        'sender': ALICE, 'self': [ME],
        'threads': [
            {'id': 't1', 'subject': 'Πρώτο', 'messages': [
                email(0, ALICE, 'Είσαι εντελώς ανίκανος.', '2024-01-01T10:00:00Z',
                      to=['Me@Example.org'], cc=['carol@example.net']),
                email(1, ME, 'Δεν συμφωνώ.', '2024-01-01T11:00:00Z'),
                email(2, ALICE, 'Καλή συνέχεια.', '2024-01-01T12:00:00Z', attachments=['a.pdf']),
            ]},
            {'id': 't2', 'subject': 'Δεύτερο', 'messages': [
                email(3, ME, 'Ερώτηση;', '2024-02-01T10:00:00Z'),
                email(4, ALICE, '', '2024-02-01T11:00:00Z'),
            ]},
        ],
    }


class FakeUsage:
    def model_dump(self):
        return {'input_tokens': 10, 'output_tokens': 20}


class FakeResponse:
    def __init__(self, output_text, status='completed', details=None, usage=None):
        self.output_text = output_text
        self.status = status
        self.incomplete_details = details
        self.usage = usage


class FakeClient:
    """Stands in for the OpenAI client; answer(payload) returns a list of findings."""

    def __init__(self, answer):
        self.answer = answer
        self.payloads = []
        self.kwargs = []
        self.lock = threading.Lock()
        self.responses = self

    def create(self, **kwargs):
        payload = json.loads(kwargs['input'])
        with self.lock:
            self.payloads.append(payload)
            self.kwargs.append(kwargs)
        result = self.answer(payload)
        if isinstance(result, FakeResponse):
            return result
        return FakeResponse(json.dumps({'findings': result}, ensure_ascii=False), usage=FakeUsage())


def finding(key, quote, **over):
    item = {'message_key': key, 'quote': quote, 'category': 'direct_insult', 'severity': 'high',
            'confidence': 'high', 'reason': 'Συνθετική αιτιολόγηση', 'caveat': ''}
    item.update(over)
    return item


@contextlib.contextmanager
def quiet():
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
            patch.object(mod.time, 'sleep', return_value=None):
        yield


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


class TempCase(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)
        self.input = self.tmp / 'conversation.json'
        self.input.write_text(json.dumps(extract(), ensure_ascii=False), encoding='utf-8')

    def args(self, **over):
        values = dict(input=self.input, output=self.tmp / 'out', sender=ALICE, model='test-model',
                      batch_size=2, context=1, workers=1, max_batches=0, gmail_account='0',
                      source_sha256='1' * 64)
        values.update(over)
        return types.SimpleNamespace(**values)


class ReadingTests(TempCase):
    def test_messages_follow_the_threads_with_keys_and_fields(self):
        data, digest = mod.read_extract(self.input)
        self.assertEqual(len(digest), 64)
        msgs = mod.build_messages(data)
        self.assertEqual([m['key'] for m in msgs], [f'm{i:07d}' for i in range(5)])
        self.assertEqual([m['thread'] for m in msgs], [1, 1, 1, 2, 2])
        self.assertEqual(msgs[0]['subject'], 'Πρώτο')
        self.assertEqual(msgs[0]['thread_id'], 't1')
        self.assertEqual(msgs[0]['sender'], ALICE)
        self.assertEqual(msgs[2]['attachments'], 1)
        self.assertEqual(msgs[0]['to'], [ME])
        self.assertEqual(msgs[0]['cc'], ['carol@example.net'])
        self.assertEqual(msgs[1]['to'], [])
        self.assertEqual(mod.user_addresses(data), [ME])

    def test_odd_address_fields_become_empty_lists(self):
        data = extract()
        data['self'] = 'me@example.org'
        data['threads'][0]['messages'][0]['to'] = 'x@example.com'
        data['threads'][0]['messages'][0]['cc'] = [None, 3, ' ']
        msgs = mod.build_messages(data)
        self.assertEqual((msgs[0]['to'], msgs[0]['cc'], mod.user_addresses(data)), ([], [], []))

    def test_rejects_what_is_not_an_extract(self):
        good = extract()
        cases = {'not json': 'nope', 'a list': '[]',
                 'other format': json.dumps({**good, 'format': 'viberpc-export'}),
                 'version 2': json.dumps({**good, 'version': 2}),
                 'threads not a list': json.dumps({**good, 'threads': {}}),
                 'messages not a list': json.dumps({**good, 'threads': [{'messages': {}}]})}
        for name, content in cases.items():
            with self.subTest(name):
                self.input.write_text(content, encoding='utf-8')
                with self.assertRaises(ValueError):
                    mod.read_extract(self.input)

    def test_gmail_link_carries_only_the_message_id(self):
        self.assertEqual(mod.gmail_url('<abc.123@mail.example>'),
                         'https://mail.google.com/mail/u/0/#search/rfc822msgid%3Aabc.123%40mail.example')
        self.assertEqual(mod.gmail_url('<x@y>', 'me@example.org'),
                         'https://mail.google.com/mail/u/me@example.org/#search/rfc822msgid%3Ax%40y')
        self.assertEqual(mod.gmail_url(''), '')

    def test_payload_has_thread_subject_and_roles(self):
        msgs = mod.build_messages(extract())
        payload = json.loads(mod.payload(msgs, 1, 0, 4, 3, ALICE, [ME]))
        self.assertEqual(payload['analyze_only_sender_id'], ALICE)
        self.assertEqual(payload['user_addresses'], [ME])
        self.assertEqual(payload['messages'][0]['to'], [ME])
        self.assertEqual(payload['messages'][0]['cc'], ['carol@example.net'])
        self.assertIn('user_addresses', mod.INSTRUCTIONS)
        self.assertEqual([m['role'] for m in payload['messages']],
                         ['CONTEXT', 'TARGET', 'TARGET', 'CONTEXT'])
        self.assertEqual(payload['messages'][0]['thread'], 1)
        self.assertEqual(payload['messages'][0]['subject'], 'Πρώτο')
        self.assertEqual(payload['messages'][2]['attachments'], 1)
        self.assertNotIn('attachments', payload['messages'][0])


class AnalyzeTests(TempCase):
    def run_analyze(self, answer, **over):
        args = self.args(**over)
        args.user_addresses = [ME]
        msgs = mod.build_messages(extract())
        client = FakeClient(answer)
        with quiet():
            mod.analyze(args, msgs, client=client)
        return args, client

    @staticmethod
    def flag_first(payload):
        keys = [m['key'] for m in payload['messages'] if m['role'] == 'TARGET']
        result = []
        if 'm0000000' in keys:
            result.append(finding('m0000000', 'εντελώς  ανίκανος'))     # whitespace differs
            result.append(finding('m0000000', 'ανίκανος τελείως'))      # not verbatim
        if 'm0000001' in keys:
            result.append(finding('m0000001', 'Δεν συμφωνώ'))           # not the sender
        return result

    def test_findings_are_verified_and_everything_is_written(self):
        args, client = self.run_analyze(self.flag_first)
        out = args.output
        findings = read_json(out / 'findings.json')
        self.assertEqual(len(findings), 1)
        [f] = findings
        self.assertEqual(f['quote'], 'εντελώς ανίκανος')
        self.assertEqual(f['quote_match'], 'normalized')
        self.assertEqual(f['gmail_message_id'], '<e0@example.test>')
        self.assertEqual(f['gmail_thread_id'], 't1')
        self.assertEqual(f['subject'], 'Πρώτο')
        self.assertEqual((f['to'], f['cc']), ([ME], ['carol@example.net']))
        self.assertEqual(f['gmail_url'],
                         'https://mail.google.com/mail/u/0/#search/rfc822msgid%3Ae0%40example.test')
        self.assertEqual(f['source_url'], 'history.html#m0000000')
        # Context stays within the thread
        self.assertEqual([c['key'] for c in f['context']], ['m0000000', 'm0000001', 'm0000002'])
        rejected = read_json(out / 'rejected_findings.json')
        self.assertEqual(sorted(r['reason'] for r in rejected),
                         ['Outside TARGET or wrong sender', 'Quote not verbatim in source text'])
        summary = read_json(out / 'summary.json')
        self.assertEqual((summary['total_messages'], summary['covered_messages']), (5, 5))
        self.assertEqual(summary['total_sender_messages'], 3)
        self.assertTrue(summary['complete'])
        for name in ('report.html', 'report.md', 'history.html', 'findings.csv', 'run_config.json'):
            self.assertTrue((out / name).exists(), name)
        self.assertEqual(client.kwargs[0]['store'], False)
        self.assertEqual(client.kwargs[0]['text']['format']['name'], 'email_findings')
        self.assertEqual(json.loads(client.kwargs[0]['input'])['user_addresses'], [ME])
        report = (out / 'report.html').read_text(encoding='utf-8')
        self.assertIn('carol@example.net', report)

    def test_batches_without_sender_text_are_not_sent(self):
        # batch 3 holds only m0000004, Alice's email without text
        _args, client = self.run_analyze(lambda payload: [])
        sent = [[m['key'] for m in p['messages'] if m['role'] == 'TARGET'] for p in client.payloads]
        self.assertEqual(sent, [['m0000000', 'm0000001'], ['m0000002', 'm0000003']])

    def test_history_groups_by_thread_and_links_to_gmail(self):
        args, _client = self.run_analyze(self.flag_first)
        page = (args.output / 'history.html').read_text(encoding='utf-8')
        self.assertIn('1. Πρώτο', page)
        self.assertIn('2. Δεύτερο', page)
        self.assertIn('id="m0000004"', page)
        self.assertIn('rfc822msgid%3Ae0%40example.test', page)
        self.assertIn('class="message flagged"', page)
        report = (args.output / 'report.html').read_text(encoding='utf-8')
        self.assertIn('target="gmail"', report)

    def test_a_second_run_uses_the_cache_and_a_changed_setting_is_refused(self):
        self.run_analyze(self.flag_first)
        _args, client = self.run_analyze(self.flag_first)
        self.assertEqual(client.payloads, [])
        with self.assertRaises(SystemExit), quiet():
            mod.analyze(self.args(batch_size=3), mod.build_messages(extract()), client=FakeClient(list))

    def test_another_senders_emails_use_the_prompt_about_you(self):
        args, client = self.run_analyze(lambda payload: [])
        self.assertIs(client.kwargs[0]['instructions'], mod.INSTRUCTIONS)
        self.assertEqual(read_json(args.output / 'run_config.json')['prompt_version'],
                         mod.PROMPT_VERSION)

    def test_your_own_emails_use_the_prompt_about_their_recipients(self):
        def flag_mine(payload):
            keys = [m['key'] for m in payload['messages'] if m['role'] == 'TARGET']
            return [finding('m0000001', 'Δεν συμφωνώ')] if 'm0000001' in keys else []

        args, client = self.run_analyze(flag_mine, sender=ME, output=self.tmp / 'own')
        self.assertTrue(client.kwargs)
        for kwargs in client.kwargs:
            self.assertEqual(kwargs['instructions'], mod.OWN_INSTRUCTIONS)
            self.assertEqual(json.loads(kwargs['input'])['analyze_only_sender_id'], ME)
        self.assertEqual(read_json(args.output / 'run_config.json')['prompt_version'],
                         mod.OWN_PROMPT_VERSION)
        [f] = read_json(args.output / 'findings.json')
        self.assertEqual((f['message_key'], f['quote']), ('m0000001', 'Δεν συμφωνώ'))
        self.assertEqual(read_json(args.output / 'summary.json')['total_sender_messages'], 2)

    def test_bcc_only_recipients_reach_the_model_and_the_finding(self):
        data = extract()
        data['threads'][0]['messages'][1].update(to=[ME], bcc=['Alice@Example.com'],
                                                 text='Αλίκη, είσαι εντελώς ανίκανη.')
        self.input.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        msgs = mod.build_messages(data)
        args = self.args(sender=ME, output=self.tmp / 'bcc')
        args.user_addresses = [ME]

        def flag(payload):
            keys = [m['key'] for m in payload['messages'] if m['role'] == 'TARGET']
            return [finding('m0000001', 'εντελώς ανίκανη')] if 'm0000001' in keys else []

        client = FakeClient(flag)
        with quiet():
            mod.analyze(args, msgs, client=client)
        sent = [m for p in client.payloads for m in p['messages'] if m['key'] == 'm0000001']
        self.assertTrue(sent)
        self.assertEqual({(tuple(m['to']), tuple(m['bcc'])) for m in sent}, {((ME,), (ALICE,))})
        self.assertIn('bcc', mod.OWN_INSTRUCTIONS)
        [f] = read_json(args.output / 'findings.json')
        self.assertEqual(f['bcc'], [ALICE])
        self.assertIn('Κρυφή κοιν.', (args.output / 'report.html').read_text(encoding='utf-8'))
        self.assertIn('Κρυφή κοινοποίηση', (args.output / 'report.md').read_text(encoding='utf-8'))

    def test_an_extract_without_bcc_still_reads(self):
        msgs = mod.build_messages(extract())
        self.assertEqual({tuple(m['bcc']) for m in msgs}, {()})

    def test_the_own_prompt_changes_only_who_is_addressed(self):
        self.assertNotEqual(mod.OWN_INSTRUCTIONS, mod.INSTRUCTIONS)
        self.assertNotIn(mod.PURPOSE, mod.OWN_INSTRUCTIONS)
        self.assertNotIn(mod.AUDIENCE, mod.OWN_INSTRUCTIONS)
        own = mod.OWN_INSTRUCTIONS.replace(mod.OWN_PURPOSE, mod.PURPOSE)
        self.assertEqual(own.replace(mod.OWN_AUDIENCE, mod.AUDIENCE), mod.INSTRUCTIONS)
        self.assertIn('user_addresses', mod.OWN_INSTRUCTIONS)

    def test_triage_reads_email_findings(self):
        args, _client = self.run_analyze(self.flag_first)
        records, runs = triage_findings.collect([args.output])
        self.assertEqual(runs[0]['source'], 'gmail')
        self.assertEqual(records[0]['message_id'], '<e0@example.test>')
        self.assertTrue(records[0]['link'].startswith('https://mail.google.com/'))


class CommandLineTests(TempCase):
    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mod.main([str(a) for a in argv])
        return out.getvalue()

    def test_list_senders(self):
        lines = self.run_main('--input', self.input, '--list-senders').splitlines()
        self.assertIn(repr(ALICE), lines[0])
        self.assertTrue(lines[0].strip().startswith('3'))

    def test_bad_options_are_refused(self):
        for argv in (['--sender', 'nobody@example.com'], ['--sender', ALICE, '--gmail-account', 'a/b'],
                     ['--sender', ALICE, '--workers', '0'], []):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.run_main('--input', self.input, '--output', self.tmp / 'out', *argv)

    def test_report_only_needs_a_previous_run(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.run_main('--input', self.input, '--sender', ALICE, '--output', self.tmp / 'none',
                          '--report-only')

    def test_sender_is_matched_without_case(self):
        with patch.object(mod, 'analyze') as fake:
            self.run_main('--input', self.input, '--sender', 'Alice@Example.COM')
        self.assertEqual(fake.call_args[0][0].sender, ALICE)
        self.assertEqual(fake.call_args[0][0].user_addresses, [ME])

    def test_your_own_address_as_sender_is_noted(self):
        err = io.StringIO()
        with patch.object(mod, 'analyze') as fake, contextlib.redirect_stderr(err):
            self.run_main('--input', self.input, '--sender', 'Me@Example.org')
        self.assertTrue(mod.own_emails(fake.call_args[0][0]))
        self.assertIn('your own addresses', err.getvalue())


if __name__ == '__main__':
    unittest.main()
