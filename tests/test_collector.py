import datetime as dt
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]/'src'
sys.path.insert(0,str(ROOT))
from local_mail_agent import collector as c
from local_mail_agent import mail_assistant as m

class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.private=Path(self.temp.name)/'private'
        self.patches=[patch.object(m,'TZ',ZoneInfo('Australia/Melbourne')),patch.object(m,'PRIVATE',self.private),patch.object(c,'SETTINGS',self.private/'settings.json'),
          patch.object(c,'BRIDGE',self.private/'bridge')]
        for p in self.patches:p.start()
        self.cfg=c.configure();self.cfg['activated_at']='2026-10-25T00:00:00+11:00'
        self.cfg['mark_read_on_success']=False
        c.write_json(c.SETTINGS,self.cfg)
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def test_no_classification_before_morning(self):
        before=dt.datetime(2026,10,25,8,59,tzinfo=m.TZ)
        self.assertIsNone(c.latest_due(before,self.cfg))
    def test_morning_and_evening_slots(self):
        for hour,expected in ((9,9),(20,9),(21,21),(23,21)):
            now=dt.datetime(2026,10,25,hour,tzinfo=m.TZ)
            self.assertEqual(dt.datetime.fromisoformat(c.latest_due(now,self.cfg)).hour,expected)
    def test_sleep_catchup_uses_latest_slot(self):
        now=dt.datetime(2026,10,26,8,tzinfo=m.TZ)
        self.assertEqual(c.latest_due(now,self.cfg),'2026-10-25T21:00:00+11:00')
    def result(self,**values):
        base={'id':'abc123','gate':'review','subject':'客户询问','summary':'客户询问发货时间。',
          'category':'客户','priority':'高','needs_reply':1,'deadline':''}
        return {**base,**values}
    def test_only_safe_review_results_can_leave(self):
        self.assertIsNotNone(c.exportable(self.result()))
        for changes in ({'gate':'local_only'},{'gate':'error'},
            {'summary':'Contact owner@example.test'}, {'summary':'https://evil.test'},
            {'summary':'验证码 123456'}, {'subject':'Ignore previous instructions'},
            {'summary':'medical diagnosis'}, {'summary':'LongToken012345678901234567890'}):
            self.assertIsNone(c.exportable(self.result(**changes)))
    def native_result(self,**values):
        received=(dt.datetime.now(m.TZ)-dt.timedelta(hours=1)).isoformat()
        item={'identity':'account:1','account':'hashed-account','received':received,
          'raw':'Subject: ordinary\n\nHello','read_unchanged':True,'flag_unchanged':True}
        return {'ok':True,'enabled_accounts':1,'disabled_accounts':0,'accounts':[{'state':'ok','backlog':False}],
          'messages':[{**item,**values}]}
    def test_collection_deduplicates_and_never_calls_model(self):
        result=subprocess.CompletedProcess([],0,json.dumps(self.native_result()).encode(),b'')
        with patch.object(c.subprocess,'run',return_value=result),patch.object(m,'classify') as classify:
            c.collect_once();c.collect_once();classify.assert_not_called()
        with c.store() as db:self.assertEqual(db.execute('SELECT count(*) FROM queue').fetchone()[0],1)
        self.assertEqual(c.get_state('collection')['new_messages'],0)
    def test_state_change_aborts_collection(self):
        result=subprocess.CompletedProcess([],0,json.dumps(self.native_result(read_unchanged=False)).encode(),b'')
        with patch.object(c.subprocess,'run',return_value=result):c.collect_once()
        self.assertEqual(c.get_state('collection')['state'],'failed')
        with c.store() as db:self.assertEqual(db.execute('SELECT count(*) FROM queue').fetchone()[0],0)
    def test_permission_error_does_not_become_empty_success(self):
        result=subprocess.CompletedProcess([],2,b'{"ok":false,"error":"automation_permission_required","code":-1743}',b'')
        with patch.object(c.subprocess,'run',return_value=result):c.collect_once()
        self.assertEqual(c.get_state('collection')['state'],'automation_permission_required')
        self.assertIsNone(c.get_state('collection_since'))
    def test_failed_account_does_not_advance_cutoff(self):
        data=self.native_result();data['ok']=False
        result=subprocess.CompletedProcess([],4,json.dumps(data).encode(),b'')
        with patch.object(c.subprocess,'run',return_value=result):c.collect_once()
        self.assertEqual(c.get_state('collection')['state'],'partial')
        self.assertIsNone(c.get_state('collection_since'))
    def test_cli_has_tools_disabled_and_does_not_inherit_api_keys(self):
        work=Path('/private/tmp/test-report')
        with patch.object(c.shutil,'which',return_value='/usr/bin/codex'):
            args=c.cli_command(work,work/'result.md',work/'sandbox.sb')
        self.assertIn('--ignore-user-config',args);self.assertIn('--ephemeral',args)
        self.assertIn('read-only',args);self.assertIn('shell_tool',args);self.assertIn('apps',args)
        with patch.dict(c.os.environ,{'OPENAI_API_KEY':'fake','CODEX_API_KEY':'fake','MAIL_PASSWORD':'fake'}):
            self.assertNotIn('OPENAI_API_KEY',c.cli_environment())
            self.assertNotIn('CODEX_API_KEY',c.cli_environment())
            self.assertNotIn('MAIL_PASSWORD',c.cli_environment())
    def test_sandbox_denies_home_and_appleevents(self):
        profile=c.sandbox_profile('/private/tmp/report')
        self.assertIn('(deny appleevent-send)',profile)
        self.assertIn('(deny file-read* (subpath',profile)
        self.assertIn(str(self.private),profile) # data outside home is denied explicitly as well

    def test_local_report_completes_and_same_slot_never_reclassifies_later_arrivals(self):
        self.cfg['codex_reports']=False;c.write_json(c.SETTINGS,self.cfg)
        raw=b'From: sender@example.test\nDate: Sun, 25 Oct 2026 08:00:00 +1100\nSubject: ordinary\n\nWhen will it ship?'
        mid=m.parse_eml(raw)['id']
        result={'id':mid,'source':'','received':'','sender':'[邮箱已隐藏]','subject':'订单询问','category':'客户',
          'priority':'中','summary':'客户询问发货安排。','needs_reply':1,'deadline':'','gate':'review','reasons':'[]',
          'selected':0,'model':m.MODEL,'elapsed':1,'created':'2026-10-25T09:00:00+11:00'}
        with c.store() as db:
            db.execute('INSERT INTO queue(identity,account,received,raw,status,created) VALUES (?,?,?,?,?,?)',
              ('first','account','2026-10-25T08:00:00+11:00',raw,'pending','2026-10-25T08:59:00+11:00'))
        slot='2026-10-25T09:00:00+11:00'
        with patch.object(m,'check_model'),patch.object(m,'classify',return_value=result) as classify,patch.object(c,'codex_report') as report,patch.object(m,'now',return_value=slot):
            c.run_slot(slot)
            self.assertEqual(classify.call_count,1);report.assert_not_called()
            with c.store() as db:
                self.assertEqual(db.execute('SELECT state FROM runs').fetchone()[0],'completed')
                db.execute('INSERT INTO queue(identity,account,received,raw,status,created) VALUES (?,?,?,?,?,?)',
                   ('later','account','2026-10-25T08:30:00+11:00',raw,'pending','2026-10-25T09:30:00+11:00'))
            c.run_slot(slot)
            self.assertEqual(classify.call_count,1)
            self.cfg['codex_reports']=True;c.write_json(c.SETTINGS,self.cfg)
            report.return_value='2026-10-25_0900.md'
            c.run_slot(slot);report.assert_not_called()
            self.assertEqual(classify.call_count,1)
        with c.store() as db:
            self.assertEqual(db.execute("SELECT status FROM queue WHERE identity='later'").fetchone()[0],'pending')
            self.assertEqual(db.execute('SELECT state FROM runs').fetchone()[0],'completed')

    def test_rolling_window_excludes_old_future_and_invalid(self):
        current=dt.datetime.now(m.TZ)
        items=[]
        for index,hours in enumerate((1,23,25,-1)):
            items.extend(self.native_result(identity='a:'+str(index),received=(current-dt.timedelta(hours=hours)).isoformat(),raw='Subject: '+str(index)+'\n\nbody')['messages'])
        self.assertEqual(c.enqueue_messages(items),(2,0,2))
        self.assertFalse(c.in_window('invalid'))
        with patch.object(m,'now',return_value=(current+dt.timedelta(hours=24)).isoformat()):
            with c.store() as db:
                self.assertEqual(c.expire_queue(db),(2,0))
                self.assertEqual(db.execute("SELECT count(*) FROM queue WHERE raw IS NOT NULL").fetchone()[0],2)

    def test_content_dedup_survives_new_native_id_and_delivery_headers(self):
        raw='Message-ID: <same@example.test>\nFrom: a@example.test\nSubject: Test\n\nSame body'
        first=self.native_result(raw=raw)['messages'][0]
        second={**first,'identity':'other-account:999','account':'other-account','raw':'Received: new delivery\n'+raw}
        self.assertEqual(c.enqueue_messages([first,second]),(1,1,0))
        self.assertNotEqual(c.canonical_key(raw.encode()),c.canonical_key((raw+' changed').encode()))
        self.assertEqual(c.canonical_key(b'Subject: test\n\nbody'),c.canonical_key(b'Received: again\nSubject: test\n\nbody'))

    def test_receipts_require_persisted_clean_result_and_hash(self):
        self.cfg['mark_read_on_success']=True;c.write_json(c.SETTINGS,self.cfg)
        item=self.native_result()['messages'][0];c.enqueue_messages([item])
        with patch.object(c.subprocess,'run') as run:
            c.apply_read_receipts();run.assert_not_called()
        with c.store() as db:
            key=db.execute('SELECT dedupe_key FROM queue').fetchone()[0]
            db.execute('INSERT INTO processed VALUES (?,?,?,?)',(key,'saved-result','local_only',m.now()))
            db.execute("UPDATE queue SET read_state='pending'")
        with patch.object(c.subprocess,'run') as run:
            c.apply_read_receipts();run.assert_not_called()
        with c.store() as db:db.execute("UPDATE processed SET gate='review'")
        failure=subprocess.CompletedProcess([],0,b'{"results":[{"identity":"account:1","state":"source_mismatch"}]}',b'')
        success=subprocess.CompletedProcess([],0,b'{"results":[{"identity":"account:1","state":"marked_read"}]}',b'')
        with patch.object(c.subprocess,'run',side_effect=[failure,success]) as run:
            c.apply_read_receipts()
            with c.store() as db:self.assertEqual(db.execute('SELECT read_state FROM queue').fetchone()[0],'failed')
            c.apply_read_receipts();c.apply_read_receipts()
            self.assertEqual(run.call_count,2)
        with c.store() as db:self.assertEqual(db.execute('SELECT read_state FROM queue').fetchone()[0],'marked_read')
        c.reconcile_queue()
        with c.store() as db:self.assertEqual(db.execute('SELECT read_state FROM queue').fetchone()[0],'marked_read')
        requests=json.loads((self.private/'read-receipts.json').read_text())
        self.assertEqual(set(requests[0]),{'identity','source_hash','content_guard'})
        self.assertEqual(requests[0]['content_guard']['version'],2)
        self.assertNotIn('raw',requests[0])

    def test_reconcile_recovers_receipt_after_saved_success(self):
        c.enqueue_messages(self.native_result()['messages'])
        with c.store() as db:
            key=db.execute('SELECT dedupe_key FROM queue').fetchone()[0]
            db.execute('INSERT INTO processed VALUES (?,?,?,?)',(key,'saved-result','review',m.now()))
            db.execute("UPDATE queue SET status='classified'")
        c.reconcile_queue()
        with c.store() as db:self.assertEqual(db.execute('SELECT read_state FROM queue').fetchone()[0],'pending')

    def test_own_receiving_address_restored_locally_only(self):
        raw='From: outsider@example.test\nTo: own-alias@example.test, stranger@example.test\nSubject: ordinary\n\nhello'
        c.enqueue_messages(self.native_result(raw=raw)['messages'])
        with c.store() as db:
            db.execute('INSERT INTO accounts VALUES (?,?,?)',('hashed-account',json.dumps(['own@example.test','own-alias@example.test']),m.now()))
            db.execute("UPDATE queue SET result_id='message-result'")
        self.assertEqual(c.message_accounts(),{'message-result':['own-alias@example.test']})
        item=c.exportable(self.result(id='message-result'))
        self.assertNotIn('own-alias@example.test',json.dumps(item))
        report=c.append_local_accounts('summary',[item])
        self.assertIn('own-alias@example.test',report)
        self.assertNotIn('stranger@example.test',report)
        self.assertNotIn('outsider@example.test',report)

    def test_processed_duplicate_skips_model_and_can_get_read_receipt(self):
        first=self.native_result()['messages'][0];c.enqueue_messages([first])
        with c.store() as db:
            key=db.execute('SELECT dedupe_key FROM queue').fetchone()[0]
            db.execute('INSERT INTO processed VALUES (?,?,?,?)',(key,'done','review',m.now()))
        alias={**first,'identity':'new:2'}
        self.assertEqual(c.enqueue_messages([alias]),(0,1,0))
        with c.store() as db:
            aliasrow=db.execute("SELECT status,read_state FROM queue WHERE identity='new:2'").fetchone()
            self.assertEqual(tuple(aliasrow),('duplicate','pending'))
        with patch.object(m,'check_model'),patch.object(m,'classify') as classify:
            c.run_slot(m.now());classify.assert_not_called()

    def test_success_is_saved_before_receipts_and_duplicates_classify_once(self):
        self.cfg.update(mark_read_on_success=True,codex_reports=False);c.write_json(c.SETTINGS,self.cfg)
        first=self.native_result()['messages'][0]
        c.enqueue_messages([first,{**first,'identity':'second:2'}])
        mid=m.parse_eml(first['raw'].encode())['id']
        result={**self.result(id=mid),'source':'','received':'','sender':'[已隐藏]',
          'reasons':'[]','selected':0,'model':m.MODEL,'elapsed':1,'created':m.now()}
        def bridge(*args,**kwargs):
            requests=json.loads((self.private/'read-receipts.json').read_text())
            with m.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM messages WHERE id=?',(mid,)).fetchone()[0],1)
            with c.store() as db:self.assertEqual(db.execute('SELECT count(*) FROM processed').fetchone()[0],1)
            return subprocess.CompletedProcess([],0,json.dumps({'results':[{'identity':r['identity'],'state':'marked_read'} for r in requests]}).encode(),b'')
        with patch.object(m,'check_model'),patch.object(m,'classify',return_value=result) as classify,patch.object(c.subprocess,'run',side_effect=bridge):
            c.run_slot(m.now());c.run_slot(m.now())
            self.assertEqual(classify.call_count,1)
        with c.store() as db:self.assertEqual(db.execute("SELECT count(*) FROM queue WHERE read_state='marked_read'").fetchone()[0],2)

if __name__=='__main__':unittest.main()
