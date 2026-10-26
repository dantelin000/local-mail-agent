"""Template selection must preserve privacy, frozen packets and delivery receipts."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from local_mail_agent import cli, config, delivery as d, mail_assistant as m, reports
from test_config_and_reports import ITEM, SLOT


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(config, 'CURRENT', copy.deepcopy(config.DEFAULTS)),
                        patch.object(m, 'PRIVATE', self.root/'data')]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def packet(self, name='reading'):
        config.CURRENT['report']['template'] = name
        config.CURRENT['privacy']['show_own_addresses'] = True
        items = [ITEM, {**ITEM, 'id': 'interest', 'subject': '游戏更新', 'needs_reply': 0,
                       'priority': '低', 'category': '订阅', 'summary': '完整摘要。'*100}]
        return reports.build(SLOT, items, {'local_only_messages': 2},
                             accounts={ITEM['id']: ['owner@example.test']})

    def test_old_configs_default_to_reading_and_only_three_choices_allowed(self):
        path = self.root/'old.toml'; path.write_text('[source]\nkind = "eml"\n')
        self.assertEqual(config.load(path)['report']['template'], 'reading')
        for name in config.REPORT_TEMPLATES:
            config.CURRENT['report']['template'] = name
            config.validate(config.CURRENT)
        for name in ('unknown', '../print', None, True):
            config.CURRENT['report']['template'] = name
            with self.assertRaises(ValueError): config.validate(config.CURRENT)

    def test_all_templates_keep_complete_summaries_and_own_address_mapping(self):
        for name in config.REPORT_TEMPLATES:
            with self.subTest(name=name):
                value = self.packet(name)
                document = reports.render(value).decode()
                for item in value['messages']:
                    self.assertIn(item['summary'], document)
                    self.assertIn(item['id'], document)
                self.assertIn('owner@example.test', document)
                self.assertIn('本机保留', document)
                self.assertNotIn('<!--REPORT_BODY-->', document)
                self.assertNotIn('<!--CONTENT_POLICY-->', document)
                self.assertEqual(document.count('<script'), 1 if name == 'reading' else 0)
                self.assertNotIn('<details', document) if name == 'print' else self.assertIn('<details', document)

    def test_all_templates_escape_text_and_reject_private_input(self):
        for name in config.REPORT_TEMPLATES:
            value = self.packet(name)
            value['messages'][0].update(subject='<script>bad()</script>', summary='<img src="bad">')
            document = reports.render(value).decode()
            self.assertIn('&lt;script&gt;bad()', document)
            self.assertNotIn('<img ', document)
            self.assertNotIn('<script>bad()', document)
            self.assertIn("default-src 'none'", document)
            value['messages'][0]['summary'] = 'Pregnancy lab result'
            with self.assertRaises(ValueError): reports.render(value)

    def test_configuration_changes_never_change_frozen_report_or_fingerprint(self):
        value = self.packet('reading')
        before = reports.render(value); digest = reports.fingerprint(value)
        config.CURRENT['report']['template'] = 'print'
        self.assertEqual(reports.render(value), before)
        self.assertEqual(reports.fingerprint(value), digest)
        alternate = reports.with_template(value, 'compact')
        alternate['messages'][0]['summary'] = 'Independent local copy'
        self.assertEqual(value['template'], 'reading')
        self.assertEqual(value['messages'][0]['summary'], ITEM['summary'])
        self.assertNotEqual(reports.fingerprint(alternate), digest)

    def test_legacy_packets_remain_valid_and_keep_original_rendering(self):
        value = self.packet(); value['version'] = 1; del value['template']
        reports.validate(value)
        before = reports.render(value)
        config.CURRENT['report']['template'] = 'print'
        self.assertEqual(reports.render(value), before)
        self.assertEqual(before, reports.render_legacy(value))
        self.assertEqual(reports.with_template(value, 'print')['version'], 2)
        for bad in ({**value, 'version': True}, {**value, 'template': 'print'},
                    {**self.packet(), 'template': 'unknown'}):
            with self.assertRaises(ValueError): reports.validate(bad)

    def test_delivery_channels_use_identical_frozen_html(self):
        for name in config.REPORT_TEMPLATES:
            value = self.packet(name); expected = reports.render(value)
            config.CURRENT['report']['template'] = 'compact'
            destination = self.root/name
            d.send_archive(value, {'path': str(destination)})
            self.assertEqual(next(destination.glob('*.html')).read_bytes(), expected)
            client = MagicMock(); client.send_message.return_value = {}
            with patch.object(d.smtplib, 'SMTP', return_value=client):
                d.send_email(value, {'host': 'smtp.example.test', 'sender': 'sender@example.test',
                                    'recipients': ['owner@example.test']})
            attachment = next(client.send_message.call_args.args[0].iter_attachments())
            self.assertEqual(attachment.get_payload(decode=True), expected)
            replies = [{'id': 12345, 'type': 'private'}, {'message_id': 42, 'chat': {'id': 12345}}]
            with patch.object(d.secrets, 'read', side_effect=['123456:'+('a'*30), '12345']), \
                    patch.object(d, 'bot_api', side_effect=replies) as api:
                d.send_bot(value, {})
            self.assertIn(expected, api.call_args.args[2])
            personal = SimpleNamespace(connect=AsyncMock(), is_user_authorized=AsyncMock(return_value=True),
                get_me=AsyncMock(return_value=SimpleNamespace(bot=False)),
                send_file=AsyncMock(return_value=SimpleNamespace(id=8)), disconnect=AsyncMock())
            with patch.object(d, 'telegram_client', return_value=personal): d.send_saved(value, {})
            self.assertEqual(personal.send_file.call_args.args[1].getvalue(), expected)

    def test_switching_layout_never_requeues_completed_delivery(self):
        value = self.packet(); channel = {'id': 'disk', 'type': 'archive', 'enabled': True, 'path': 'archive'}
        config.CURRENT['delivery'] = [channel]
        d.enqueue(value); self.assertEqual(d.process_one()['state'], 'sent')
        config.CURRENT['report']['template'] = 'print'
        d.enqueue(reports.with_template(value, 'print'))
        with patch.dict(d.DRIVERS, {'archive': Mock(side_effect=AssertionError('duplicate'))}):
            self.assertIsNone(d.process_one())
        self.assertEqual(len(d.status()), 1)


class TemplateCLITests(unittest.TestCase):
    def test_offline_all_previews_and_render_never_use_collector_or_network(self):
        from local_mail_agent import collector
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path = root/'config.toml'; data = root/'data'; output = root/'previews'
            with patch.object(config, 'CURRENT', copy.deepcopy(config.DEFAULTS)), \
                 patch.object(config, 'ACTIVE', False), patch.object(config, 'CONFIG_PATH', None), \
                 patch.object(m, 'PRIVATE', m.PRIVATE), patch.object(m, 'TZ', m.TZ), \
                 patch.object(m, 'MODEL', m.MODEL), patch.object(m, 'OLLAMA', m.OLLAMA), \
                 patch.object(collector, 'SETTINGS', collector.SETTINGS), patch.object(collector, 'BRIDGE', collector.BRIDGE), \
                 patch.object(collector, 'configure', side_effect=AssertionError('mailbox')), \
                 patch.object(m, 'local_api', side_effect=AssertionError('model')), \
                 patch.object(d, 'enqueue', side_effect=AssertionError('delivery')), \
                 patch.object(d.urllib.request, 'urlopen', side_effect=AssertionError('network')), \
                 contextlib.redirect_stdout(io.StringIO()):
                def call(*args): return cli.main(['--config', str(path), *args])
                self.assertEqual(call('init', '--source', 'eml', '--data-dir', str(data), '--template', 'compact'), 0)
                self.assertEqual(config.load(path)['report']['template'], 'compact')
                self.assertEqual(call('demo', '--all-templates', '--output-dir', str(output)), 0)
                self.assertTrue((output/'index.html').exists())
                for name in config.REPORT_TEMPLATES:
                    self.assertEqual(len(list((output/name).glob('*.html'))), 1)
                    packet = json.loads(next((output/name).glob('*.json')).read_text())
                    self.assertEqual(packet['template'], name)
                    self.assertEqual(packet['coverage']['sample'], 'synthetic')
                source = next((output/'reading').glob('*.json')); original = source.read_bytes()
                target = root/'reformatted.html'
                self.assertEqual(call('render', str(source), '--template', 'print', '--output', str(target)), 0)
                self.assertIn('data-template="print"', target.read_text())
                self.assertEqual(source.read_bytes(), original)
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                self.assertFalse(data.exists())
                bad = root/'unsafe.json'; packet['messages'][0]['summary'] = 'Pregnancy lab result'
                bad.write_text(json.dumps(packet))
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(call('render', str(bad), '--output', str(root/'blocked.html')), 2)
                self.assertFalse((root/'blocked.html').exists())
