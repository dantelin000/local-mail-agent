import asyncio
import copy
import json
from pathlib import Path
import smtplib
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,MagicMock,Mock,patch
from local_mail_agent import config,delivery as d,mail_assistant as m,reports
from test_config_and_reports import ITEM,SLOT

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.private=Path(self.temp.name)/'data'
        self.patches=[patch.object(m,'PRIVATE',self.private),patch.object(config,'CURRENT',copy.deepcopy(config.DEFAULTS))]
        for p in self.patches:p.start()
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
    def test_success_is_idempotent_and_frozen_payload_conflicts(self):
        self.enable();d.enqueue(self.value,[self.channel()]);send=Mock(return_value={'message_id':'42'})
        with patch.dict(d.DRIVERS,{'telegram_bot':send}):
            self.assertEqual(d.process_one()['state'],'sent');d.enqueue(self.value,[self.channel()]);self.assertIsNone(d.process_one())
        send.assert_called_once()
        changed=copy.deepcopy(self.value);changed['messages'][0]['summary']='Changed safe text'
        with self.assertRaises(ValueError):d.enqueue(changed,[self.channel()])
        with self.assertRaises(ValueError):d.enqueue(self.value,[{**self.channel(),'token_env':'ANOTHER_TOKEN'}])
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
