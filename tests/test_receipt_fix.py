import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import sys
import unittest
from unittest.mock import patch

import test_collector as fixtures
from local_mail_agent import collector as c
from local_mail_agent import mail_assistant as m
from local_mail_agent.receipt_guard import make_guard

RAW=('Message-ID: <original@example.test>\nFrom: sender@example.test\n'
     'To: owner@example.test\nSubject: receipt test\nDate: Sun, 25 Oct 2026 09:00:00 +1100\n'
     'Content-Type: text/plain; charset=utf-8\nX-SG-ID: original\nList-Unsubscribe: old\n\nHello\n')

class ReceiptFixTests(unittest.TestCase):
    native_result=fixtures.CollectorTests.native_result
    def setUp(self):
        fixtures.CollectorTests.setUp(self)
        self.cfg['mark_read_on_success']=True;c.write_json(c.SETTINGS,self.cfg)
    def tearDown(self):fixtures.CollectorTests.tearDown(self)

    def ready(self,identity='account:1',gate='review',received=None):
        item=self.native_result(identity=identity,raw=RAW)['messages'][0]
        if received:item['received']=received
        c.enqueue_messages([item])
        with c.store() as db:
            key=db.execute('SELECT dedupe_key FROM queue WHERE identity=?',(identity,)).fetchone()[0]
            db.execute('INSERT OR REPLACE INTO processed VALUES (?,?,?,?)',(key,'saved-result',gate,m.now()))
            db.execute("UPDATE queue SET status='classified',read_state='pending' WHERE identity=?",(identity,))
        return item

    def test_identity_change_is_persisted_and_not_retried(self):
        self.ready()
        response=subprocess.CompletedProcess([],0,b'{"results":[{"identity":"account:1","state":"identity_changed"}]}',b'')
        with patch.object(c.subprocess,'run',return_value=response) as run:
            c.apply_read_receipts();c.apply_read_receipts();self.assertEqual(run.call_count,1)
        with c.store() as db:
            row=db.execute('SELECT read_state,read_reason,read_attempts FROM queue').fetchone()
            self.assertEqual(tuple(row),('needs_review','identity_changed',1))
            self.assertEqual(db.execute('SELECT reason FROM read_events').fetchone()[0],'identity_changed')

    def test_global_permission_error_retains_reason_and_code(self):
        self.ready()
        response=subprocess.CompletedProcess([],2,b'{"ok":false,"error":"automation_permission_required","code":-1744}',b'')
        with patch.object(c.subprocess,'run',return_value=response):c.apply_read_receipts()
        with c.store() as db:
            self.assertEqual(tuple(db.execute('SELECT read_state,read_reason FROM queue').fetchone()),('failed','automation_permission_required'))
            self.assertEqual(db.execute('SELECT error_code FROM read_events').fetchone()[0],-1744)

    def test_explicit_retry_overrides_age_and_attempts_only_for_selected_safe_targets(self):
        self.ready();self.ready('other:2')
        with c.store() as db:
            db.execute("UPDATE queue SET received=?,read_attempts=3",((dt.datetime.now(m.TZ)-dt.timedelta(days=2)).isoformat(),))
        response=subprocess.CompletedProcess([],0,b'{"results":[{"identity":"account:1","state":"marked_read","validation":"normalized_content"}]}',b'')
        with patch.object(c.subprocess,'run',return_value=response) as run:
            c.apply_read_receipts();run.assert_not_called()
            result=c.apply_read_receipts(['account:1'],force=True)
            self.assertEqual(result['confirmed'],1);self.assertEqual(run.call_count,1)
        with c.store() as db:
            self.assertEqual(db.execute("SELECT read_attempts FROM queue WHERE identity='account:1'").fetchone()[0],4)
            self.assertEqual(db.execute("SELECT read_state FROM queue WHERE identity='other:2'").fetchone()[0],'pending')
        with self.assertRaises(ValueError):c.apply_read_receipts(force=True)

    def test_explicit_retry_still_cannot_write_local_only(self):
        self.ready(gate='local_only')
        with patch.object(c.subprocess,'run') as run:
            self.assertEqual(c.apply_read_receipts(['account:1'],force=True)['requested'],0);run.assert_not_called()

    def test_manual_job_runs_once_and_verifies_read_in_second_native_call(self):
        self.ready()
        c.write_json(m.PRIVATE/'read-retry-request.json',{'job_id':'repair-test','identities':['account:1']})
        wrote=subprocess.CompletedProcess([],0,b'{"results":[{"identity":"account:1","state":"marked_read","validation":"normalized_content"}]}',b'')
        checked=subprocess.CompletedProcess([],0,b'{"results":[{"identity":"account:1","state":"verified_read","validation":"normalized_content"}]}',b'')
        with patch.object(c.subprocess,'run',side_effect=[wrote,checked]) as run:
            c.process_read_retry_request();c.process_read_retry_request()
            self.assertEqual(run.call_count,2)
            self.assertEqual(run.call_args_list[1].args[0][1],'--verify-read')
        with c.store() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM read_events').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT read_attempts FROM queue').fetchone()[0],1)
        self.assertEqual(c.get_state('read_retry')['counts'],{'marked_read':1})

    def test_post_verification_failure_does_not_claim_success(self):
        self.ready()
        wrote=subprocess.CompletedProcess([],0,b'{"results":[{"identity":"account:1","state":"marked_read"}]}',b'')
        checked=subprocess.CompletedProcess([],0,b'{"results":[{"identity":"account:1","state":"still_unread"}]}',b'')
        with patch.object(c.subprocess,'run',side_effect=[wrote,checked]):
            result=c.apply_read_receipts(['account:1'],force=True,verify_after=True)
        self.assertEqual(result['confirmed'],0)
        with c.store() as db:self.assertEqual(db.execute('SELECT read_reason FROM queue').fetchone()[0],'post_verification_failed')

    def test_transient_native_error_keeps_precise_reason(self):
        self.ready()
        response=subprocess.CompletedProcess([],0,b'{"results":[{"identity":"account:1","state":"not_found","error_code":-1728}]}',b'')
        with patch.object(c.subprocess,'run',return_value=response):c.apply_read_receipts()
        with c.store() as db:
            self.assertEqual(db.execute('SELECT read_reason FROM queue').fetchone()[0],'not_found')
            self.assertEqual(db.execute('SELECT error_code FROM read_events').fetchone()[0],-1728)

    def test_malformed_raw_is_blocked_without_native_write(self):
        self.ready()
        with c.store() as db:db.execute('UPDATE queue SET raw=?',(b'not a MIME message',))
        with patch.object(c.subprocess,'run') as run:c.apply_read_receipts();run.assert_not_called()
        with c.store() as db:self.assertEqual(db.execute('SELECT read_reason FROM queue').fetchone()[0],'guard_unavailable')

@unittest.skipUnless(sys.platform=="darwin", "native guard requires macOS")
class NativeReceiptGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.binary=Path(cls.temp.name)/'guard-test'
        src=Path(__file__).with_name('receipt_guard_harness.m')
        subprocess.run(['clang','-fobjc-arc','-Wno-deprecated-declarations','-framework','Cocoa','-framework','ScriptingBridge','-framework','ApplicationServices',str(src),'-o',str(cls.binary)],check=True,capture_output=True)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_native_guard_accepts_metadata_changes_but_rejects_identity_and_body_changes(self):
        request={'source_hash':hashlib.sha256(RAW.encode()).hexdigest()[:24],'content_guard':make_guard(RAW.encode())}
        cases=[(RAW,'ok'),
          (RAW.replace('\n','\r\n'),'ok'),
          (RAW.replace('X-SG-ID: original','X-SG-ID: changed').replace('List-Unsubscribe: old','List-Unsubscribe: new'),'ok'),
          (RAW.replace('Subject: receipt test','SUBJECT:\treceipt\n test'),'ok'),
          (RAW.replace('Message-ID: <original@example.test>','Message-ID: <changed@example.test>'),'identity_changed'),
          (RAW.replace('sender@example.test','different@example.test'),'identity_fields_changed'),
          (RAW.replace('owner@example.test','other@example.test'),'identity_fields_changed'),
          (RAW.replace('Subject: receipt test','Subject: modified test'),'identity_fields_changed'),
          (RAW.replace('Content-Type: text/plain','Content-Type: text/html'),'identity_fields_changed'),
          (RAW.replace('Hello\n','changed body\n'),'body_mismatch')]
        # Header order is irrelevant; complete MIME body, including attachment data, remains protected.
        head,body=RAW.split('\n\n',1)
        cases.append(('\n'.join(reversed(head.split('\n')))+'\n\n'+body,'ok'))
        path=Path(self.temp.name)/'cases.json'
        payload=[{'source':source,'request':request} for source,_ in cases]
        payload.append({'source':RAW,'request':{'source_hash':request['source_hash']}})
        path.write_text(json.dumps(payload))
        result=json.loads(subprocess.run([str(self.binary),str(path)],capture_output=True,check=True).stdout)
        self.assertEqual([r['state'] for r in result],[expected for _,expected in cases]+['guard_missing'])
        self.assertEqual(result[0]['validation'],'exact_source')
        self.assertEqual(result[2]['validation'],'normalized_content')
