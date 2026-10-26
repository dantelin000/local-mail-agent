import asyncio
import copy
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import smtplib
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,MagicMock,Mock,patch
from local_mail_agent import config,collector as c,delivery as d,mail_assistant as m,reports
from test_config_and_reports import ITEM,SLOT

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.private=Path(self.temp.name)/'data'
        self.credentials={'LMA_TELEGRAM_BOT_TOKEN':'123456:'+('a'*30),'LMA_TELEGRAM_CHAT_ID':'9876543210123456'}
        self.patches=[patch.object(m,'PRIVATE',self.private),patch.object(config,'CURRENT',copy.deepcopy(config.DEFAULTS)),
            patch.object(d.secrets,'read',side_effect=lambda name,required=True:self.credentials.get(name))]
        for p in self.patches:p.start()
        config.CURRENT['delivery']=[self.channel(),{'id':'disk','type':'archive','enabled':True,'path':'archive'}]
        self.value=reports.build(SLOT,[ITEM],{'local_only_messages':1})
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def channel(self,kind='telegram_bot'):
        return {'id':'target','type':kind,'enabled':True}
    def enable(self):config.CURRENT['privacy']['allow_external_delivery']=True
    def test_external_switch_blocks_network_but_archive_still_runs(self):
        d.enqueue(self.value,[self.channel(),{'id':'disk','type':'archive','enabled':True,'path':'archive'}])
        with patch.dict(d.DRIVERS,{'telegram_bot':Mock(side_effect=AssertionError('network'))}):
            self.assertEqual(d.process_one()['state'],'sent');self.assertIsNone(d.process_one())
        self.assertEqual({r['channel_id']:r['state'] for r in d.status()},{'disk':'sent','target':'pending'})
    def test_success_stays_idempotent_after_packet_and_channel_changes(self):
        self.enable();d.enqueue(self.value,[self.channel()]);send=Mock(return_value={'message_id':'42'})
        with patch.dict(d.DRIVERS,{'telegram_bot':send}):
            self.assertEqual(d.process_one()['state'],'sent');d.enqueue(self.value,[self.channel()]);self.assertIsNone(d.process_one())
        send.assert_called_once()
        changed=copy.deepcopy(self.value);changed['messages'][0]['summary']='Changed safe text'
        d.enqueue(changed,[self.channel()])
        d.enqueue(self.value,[{**self.channel(),'token_env':'ANOTHER_TOKEN'}])
        with d.ledger() as conn:
            self.assertEqual([r[0] for r in conn.execute('SELECT state FROM deliveries ORDER BY id')],['sent'])
    def test_only_pending_and_retryable_rows_can_be_superseded(self):
        for state in ('pending','retryable'):
            with self.subTest(state=state):
                old={**self.channel('email'),'id':state,'recipients':['old@example.test']}
                d.enqueue(self.value,[old])
                with d.ledger() as conn:conn.execute('UPDATE deliveries SET state=? WHERE channel_id=?',(state,state))
                new={**old,'recipients':['new@example.test']};d.enqueue(self.value,[new]);d.enqueue(self.value,[new])
                with d.ledger() as conn:
                    rows=conn.execute('SELECT state FROM deliveries WHERE channel_id=? ORDER BY id',(state,)).fetchall()
                    self.assertEqual([r[0] for r in rows],['superseded','pending'])
    def completed(self,value):
        filename=reports.save(value)
        with c.store() as conn:
            conn.execute('INSERT INTO runs(slot,state,report) VALUES (?,?,?)',(value['slot'],'completed',filename))
    def test_new_channel_does_not_backfill_old_reports(self):
        seen='2030-01-04T09:00:00+00:00'
        archive={'id':'disk','type':'archive','enabled':True,'path':'archive'}
        config.CURRENT['delivery']=[archive]
        for day in (1,2,3):self.completed(reports.build(f'2030-01-0{day}T09:00:00+00:00',[ITEM],{}))
        with patch.object(m,'now',return_value=seen):
            d.stage_completed()
            email={**self.channel('email'),'recipients':['new@example.test']}
            config.CURRENT['delivery'].append(email);d.stage_completed()
        with d.ledger() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM deliveries WHERE channel_id='target'").fetchone()[0],0)
            self.assertEqual(conn.execute("SELECT count(*) FROM deliveries WHERE channel_id='disk'").fetchone()[0],3)
            self.assertEqual(conn.execute('SELECT first_seen FROM channel_baseline').fetchone()[0],seen)
        # Equal instants in different timezones are eligible too.
        newer=reports.build('2030-01-04T20:00:00+11:00',[ITEM],{});self.completed(newer)
        with patch.object(m,'now',return_value='2030-01-05T00:00:00+00:00'):d.stage_completed()
        with d.ledger() as conn:
            rows=conn.execute("SELECT report_id FROM deliveries WHERE channel_id='target'").fetchall()
            self.assertEqual([r[0] for r in rows],[newer['report_id']])
    def test_baseline_is_set_even_without_completed_reports(self):
        config.CURRENT['delivery']=[self.channel()]
        with patch.object(m,'now',return_value='2029-01-01T00:00:00+00:00'):d.stage_completed()
        with patch.object(m,'now',return_value='2031-01-01T00:00:00+00:00'):d.enqueue(self.value)
        self.assertEqual(len(d.status()),1)
    def test_first_generated_report_anchors_baseline_to_its_slot(self):
        seen='2030-01-02T09:05:37+00:00'
        with patch.object(m,'now',return_value=seen):d.enqueue(self.value,[self.channel()])
        with d.ledger() as conn:
            self.assertEqual(conn.execute('SELECT first_seen FROM channel_baseline').fetchone()[0],SLOT)
        self.assertEqual(d.status()[0]['state'],'pending')
    def test_first_scan_includes_current_period_but_not_earlier_reports(self):
        config.CURRENT['delivery']=[self.channel('email')]
        earlier=reports.build('2030-01-02T08:59:00+00:00',[ITEM],{})
        self.completed(earlier);self.completed(self.value)
        with patch.object(m,'now',return_value='2030-01-02T09:05:37+00:00'):d.stage_completed()
        self.assertEqual([r['report_id'] for r in d.status()],[self.value['report_id']])
        with patch.object(m,'now',return_value='2030-01-02T10:00:00+00:00'):d.enqueue(earlier)
        with d.ledger() as conn:
            self.assertEqual(conn.execute('SELECT first_seen FROM channel_baseline').fetchone()[0],SLOT)
            self.assertEqual(conn.execute('SELECT count(*) FROM deliveries').fetchone()[0],1)
    def test_empty_scan_during_generation_does_not_drop_first_report(self):
        config.CURRENT['delivery']=[self.channel('email')]
        with patch.object(m,'now',return_value='2030-01-02T09:02:00+00:00'):d.stage_completed()
        self.completed(self.value)
        with patch.object(m,'now',return_value='2030-01-02T09:05:37+00:00'):d.enqueue(self.value)
        self.assertEqual([r['report_id'] for r in d.status()],[self.value['report_id']])
    def test_disabled_changed_or_deleted_channel_cancels_before_driver(self):
        for change in ('disabled','changed','deleted'):
            with self.subTest(change=change):
                channel={**self.channel(),'id':change};config.CURRENT['delivery']=[channel]
                d.enqueue(self.value)
                if change=='disabled':channel['enabled']=False
                elif change=='changed':channel['token_env']='CHANGED_TOKEN'
                else:config.CURRENT['delivery']=[]
                send=Mock()
                with patch.dict(d.DRIVERS,{'telegram_bot':send}):
                    result=d.process_one(channel_id=change)
                self.assertEqual(result['state'],'cancelled')
                self.assertEqual(result['reason'],'channel_disabled_or_changed');send.assert_not_called()
    def test_recipient_change_stages_new_destination_without_blocking_archive(self):
        old={**self.channel('email'),'recipients':['old@example.test']}
        archive={'id':'disk','type':'archive','enabled':True,'path':'archive'}
        config.CURRENT['delivery']=[old,archive];self.completed(self.value);d.stage_completed()
        config.CURRENT['delivery'][0]={**old,'recipients':['new@example.test']};d.stage_completed();d.stage_completed()
        with d.ledger() as conn:
            rows=conn.execute("SELECT state,channel FROM deliveries WHERE channel_id='target' ORDER BY id").fetchall()
            self.assertEqual([r['state'] for r in rows],['superseded','pending'])
            self.assertEqual(json.loads(rows[-1]['channel'])['recipients'],['new@example.test'])
            self.assertEqual(conn.execute("SELECT count(*) FROM deliveries WHERE channel_id='disk'").fetchone()[0],1)
        self.enable();send=Mock(return_value={'message_id':'new'})
        with patch.dict(d.DRIVERS,{'email':send}):self.assertEqual(d.process_one(channel_id='target')['state'],'sent')
        send.assert_called_once();self.assertEqual(send.call_args.args[1]['recipients'],['new@example.test'])
    def test_staging_never_requeues_terminal_or_sending_reports_after_recipient_change(self):
        states=('sent','uncertain','rejected','cancelled','sending')
        config.CURRENT['delivery']=[{**self.channel('email'),'id':state,'recipients':['old@example.test']} for state in states]
        self.completed(self.value);d.stage_completed()
        with d.ledger() as conn:
            for state in states:
                conn.execute("UPDATE deliveries SET state=?,attempts=1,reason='historical',message_id='receipt' WHERE channel_id=?",(state,state))
            before=[dict(r) for r in conn.execute('SELECT * FROM deliveries ORDER BY id')]
        for channel in config.CURRENT['delivery']:channel['recipients']=['new@example.test']
        d.stage_completed();d.stage_completed();self.enable()
        send=Mock()
        with patch.dict(d.DRIVERS,{'email':send}):self.assertIsNone(d.process_one())
        send.assert_not_called()
        with d.ledger() as conn:self.assertEqual([dict(r) for r in conn.execute('SELECT * FROM deliveries ORDER BY id')],before)
    def test_changed_resolved_bot_credentials_cancel_before_driver(self):
        self.enable()
        for field in self.credentials:
            with self.subTest(field=field):
                channel={**self.channel(),'id':field};config.CURRENT['delivery']=[channel]
                d.enqueue(self.value)
                original=self.credentials[field];self.credentials[field]=original+'9'
                send=Mock()
                with patch.dict(d.DRIVERS,{'telegram_bot':send}):result=d.process_one()
                self.assertEqual(result['state'],'cancelled');send.assert_not_called()
                self.credentials[field]=original
    def test_staging_cancels_changed_credentials_without_rebinding_report(self):
        self.completed(self.value)
        for field in self.credentials:
            with self.subTest(field=field):
                channel={**self.channel(),'id':field};config.CURRENT['delivery']=[channel]
                d.stage_completed()
                with d.ledger() as conn:
                    frozen=conn.execute('SELECT channel FROM deliveries WHERE channel_id=?',(field,)).fetchone()[0]
                fingerprints=json.loads(frozen)['credential_fingerprints']
                self.assertEqual(fingerprints,{'token':hashlib.sha256(self.credentials['LMA_TELEGRAM_BOT_TOKEN'].encode()).hexdigest(),
                    'chat_id':hashlib.sha256(self.credentials['LMA_TELEGRAM_CHAT_ID'].encode()).hexdigest()})
                original=self.credentials[field];self.credentials[field]=original+'9'
                d.stage_completed();d.stage_completed()
                with d.ledger() as conn:
                    rows=conn.execute('SELECT state,channel FROM deliveries WHERE channel_id=?',(field,)).fetchall()
                    self.assertEqual([tuple(r) for r in rows],[('cancelled',frozen)])
                database=(self.private/'delivery.sqlite3').read_bytes()
                for secret in (*self.credentials.values(),original):self.assertNotIn(secret.encode(),database)
                self.credentials[field]=original
    def test_legacy_bot_queue_without_fingerprints_is_cancelled(self):
        self.enable();config.CURRENT['delivery']=[self.channel()];d.enqueue(self.value)
        with d.ledger() as conn:conn.execute('UPDATE deliveries SET channel=?',(json.dumps(self.channel(),sort_keys=True),))
        send=Mock()
        with patch.dict(d.DRIVERS,{'telegram_bot':send}):self.assertEqual(d.process_one()['state'],'cancelled')
        d.enqueue(self.value);self.assertEqual(len(d.status()),1);send.assert_not_called()
    def test_bot_rechecks_fingerprints_at_send_boundary(self):
        frozen=json.loads(d.channel_snapshot(self.channel()))
        self.credentials['LMA_TELEGRAM_CHAT_ID']='8765432109876543'
        with patch.object(d,'bot_api') as api:
            with self.assertRaises(d.Cancelled):d.send_bot(self.value,frozen)
        api.assert_not_called()
    def test_staging_continues_after_report_and_channel_errors(self):
        self.completed(self.value)
        other=reports.build('2030-01-03T09:00:00+00:00',[ITEM],{});self.completed(other)
        broken=m.PRIVATE/'reports/broken.json';broken.write_text('{')
        with c.store() as conn:conn.execute("INSERT INTO runs(slot,state,report) VALUES ('broken','completed','broken.md')")
        original=d.enqueue
        def enqueue(value,channels):
            if channels[0]['id']=='target':raise ValueError('private-detail')
            return original(value,channels)
        with patch.object(d,'enqueue',side_effect=enqueue):d.stage_completed()
        self.assertEqual(len(d.status()),2)
        self.assertEqual({r['channel_id'] for r in d.status()},{'disk'})
        self.assertEqual(c.get_state('delivery')['state'],'queue_error')
        self.assertNotIn('private-detail',json.dumps(c.get_state('delivery')))
    def test_legacy_ledger_migrates_without_losing_receipt(self):
        m.setup()
        with closing(sqlite3.connect(m.PRIVATE/'delivery.sqlite3')) as conn,conn:
            conn.execute("CREATE TABLE deliveries(report_id TEXT,channel_id TEXT,digest TEXT,packet TEXT,channel TEXT,state TEXT,attempts INTEGER DEFAULT 0,next_attempt REAL DEFAULT 0,reason TEXT DEFAULT '',message_id TEXT,updated TEXT,PRIMARY KEY(report_id,channel_id))")
            conn.execute('INSERT INTO deliveries VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (self.value['report_id'],'target',reports.fingerprint(self.value),json.dumps(self.value),json.dumps(self.channel(),sort_keys=True),'sent',1,0,'','42',m.now()))
        d.enqueue(self.value,[self.channel()])
        with d.ledger() as conn:
            rows=conn.execute('SELECT state,message_id FROM deliveries').fetchall()
            self.assertEqual([tuple(row) for row in rows],[('sent','42')])
    def test_concurrent_enqueue_keeps_one_active_row_per_channel(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _:d.enqueue(self.value),range(12)))
        with d.ledger() as conn:
            rows=conn.execute('SELECT channel_id,count(*) FROM deliveries GROUP BY channel_id').fetchall()
            self.assertEqual({r[0]:r[1] for r in rows},{'target':1,'disk':1})
    def test_uncertain_result_never_automatically_retries(self):
        self.enable();d.enqueue(self.value,[self.channel()]);send=Mock(side_effect=d.Uncertain('telegram_result_unknown'))
        with patch.dict(d.DRIVERS,{'telegram_bot':send}):
            self.assertEqual(d.process_one()['state'],'uncertain');self.assertIsNone(d.process_one())
        self.assertEqual(send.call_count,1)
    def test_turning_off_own_address_display_blocks_old_external_payload(self):
        self.enable();config.CURRENT['privacy']['show_own_addresses']=True
        value=reports.build(SLOT,[ITEM],{},accounts={ITEM['id']:['owner@example.test']})
        d.enqueue(value,[self.channel()]);config.CURRENT['privacy']['show_own_addresses']=False
        send=Mock(side_effect=AssertionError('must not send old address mapping'))
        with patch.dict(d.DRIVERS,{'telegram_bot':send}):
            result=d.process_one();self.assertEqual(result['state'],'rejected');self.assertEqual(result['reason'],'own_address_export_not_enabled')
        send.assert_not_called()
    def test_crashed_non_idempotent_sends_become_uncertain(self):
        d.enqueue(self.value,[self.channel()])
        with d.ledger() as conn:conn.execute("UPDATE deliveries SET state='sending'")
        d.recover();self.assertEqual(d.status()[0]['state'],'uncertain')
    def test_relay_duplicate_uses_persisted_receipt(self):
        self.enable();config.CURRENT['delivery']=[self.channel()]
        send=Mock(return_value={'message_id':'99'})
        with patch.dict(d.DRIVERS,{'telegram_bot':send}):
            self.assertEqual(d.relay(self.value,'target')['state'],'sent')
            self.assertTrue(d.relay(self.value,'target')['duplicate'])
        send.assert_called_once()
    def test_active_remote_send_is_not_repeated(self):
        self.enable();config.CURRENT['delivery']=[self.channel()];d.enqueue(self.value)
        with d.ledger() as conn:conn.execute("UPDATE deliveries SET state='sending'")
        with patch.dict(d.DRIVERS,{'telegram_bot':Mock(side_effect=AssertionError('duplicate'))}):
            self.assertEqual(d.relay(self.value,'target')['state'],'uncertain')
    def test_telegram_bot_uses_fixed_verified_private_chat(self):
        token='123456:'+('a'*30)
        replies=[{'id':12345,'type':'private'},{'message_id':42,'chat':{'id':12345}}]
        with patch.object(d.secrets,'read',side_effect=[token,'12345']),patch.object(d,'bot_api',side_effect=replies) as api:
            self.assertEqual(d.send_bot(self.value,self.channel()),{'message_id':'42'})
        self.assertEqual(api.call_args_list[0].args[1],'getChat');self.assertEqual(api.call_args_list[1].args[1],'sendDocument')
        self.assertNotIn('allow_paid_broadcast',api.call_args_list[1].args[2].decode())
        with patch.object(d.secrets,'read',side_effect=[token,'12345']),patch.object(d,'bot_api',return_value={'id':12345,'type':'group'}) as api:
            with self.assertRaises(d.DeliveryError):d.send_bot(self.value,self.channel())
            self.assertEqual(api.call_count,1)
    def test_smtp_uses_tls_and_does_not_resend_unknown_data_result(self):
        channel={**self.channel('email'),'host':'smtp.example.test','sender':'sender@example.test','recipients':['owner@example.test'],'tls':'starttls'}
        client=MagicMock();client.send_message.return_value={}
        with patch.object(d.smtplib,'SMTP',return_value=client):d.send_email(self.value,channel)
        client.starttls.assert_called_once();message=client.send_message.call_args.args[0]
        self.assertIn('text/html',message.as_string())
        client.send_message.side_effect=TimeoutError('secret-error')
        with patch.object(d.smtplib,'SMTP',return_value=client):
            with self.assertRaises(d.Uncertain) as exc:d.send_email(self.value,channel)
        self.assertNotIn('secret-error',str(exc.exception))
    def test_smtp_recipient_injection_rejected_before_connect(self):
        with patch.object(d.smtplib,'SMTP') as smtp:
            with self.assertRaises(d.DeliveryError):d.send_email(self.value,{'sender':'bad\r\nBcc:x@example.test','recipients':['owner@example.test']})
            smtp.assert_not_called()
    def test_saved_messages_only_uses_me_and_never_prompts_in_worker(self):
        client=SimpleNamespace(connect=AsyncMock(),is_user_authorized=AsyncMock(return_value=True),
            get_me=AsyncMock(return_value=SimpleNamespace(bot=False)),send_file=AsyncMock(return_value=SimpleNamespace(id=8)),disconnect=AsyncMock())
        with patch.object(d,'telegram_client',return_value=client):self.assertEqual(d.send_saved(self.value,self.channel('telegram_saved')),{'message_id':'8'})
        self.assertEqual(client.send_file.call_args.args[0],'me');self.assertIsNone(client.send_file.call_args.kwargs['parse_mode'])
        client.is_user_authorized.return_value=False;client.send_file.reset_mock()
        with patch.object(d,'telegram_client',return_value=client):
            with self.assertRaises(d.DeliveryError):d.send_saved(self.value,self.channel('telegram_saved'))
        client.send_file.assert_not_called()
    def test_personal_session_disables_updates_entity_caching_and_auto_retries(self):
        client=SimpleNamespace(session=SimpleNamespace(save_entities=True))
        factory=Mock(return_value=client)
        with patch.dict('sys.modules',{'telethon':SimpleNamespace(TelegramClient=factory)}):
            d.telegram_client(self.channel('telegram_saved'),('12345','a'*32))
        self.assertFalse(factory.call_args.kwargs['receive_updates']);self.assertEqual(factory.call_args.kwargs['request_retries'],0)
        self.assertFalse(client.session.save_entities)
    def test_ssh_quotes_remote_arguments_and_sends_no_credential_environment(self):
        channel={**self.channel('ssh'),'host':'relay.example.test','command':['lma','--config','/tmp/path with spaces/config.toml','relay','--channel','bot']}
        response=subprocess.CompletedProcess([],0,'{"state":"sent","message_id":"4"}','')
        with patch.dict(d.os.environ,{'LMA_TELEGRAM_BOT_TOKEN':'private-value'}),patch.object(d.subprocess,'run',return_value=response) as run:
            self.assertEqual(d.send_ssh(self.value,channel)['message_id'],'4')
        self.assertIn("'/tmp/path with spaces/config.toml'",run.call_args.args[0][-1])
        self.assertNotIn('LMA_TELEGRAM_BOT_TOKEN',run.call_args.kwargs['env'])
        self.assertNotIn('raw',json.loads(run.call_args.kwargs['input']))
