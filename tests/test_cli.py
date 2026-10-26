import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from local_mail_agent import cli,config,mail_assistant as m,secrets

class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.config=self.root/'config.toml';self.data=self.root/'data'
        self.patches=[patch.object(config,'ACTIVE',False),patch.object(config,'CURRENT',config.load(self.config)),
            patch.object(m,'PRIVATE',self.data),patch.object(m,'MODEL',m.MODEL),patch.object(m,'TZ',m.TZ),patch.object(m,'OLLAMA',m.OLLAMA)]
        self.patches.append(patch.object(secrets,'read',return_value='synthetic-credential'))
        from local_mail_agent import collector
        self.patches += [patch.object(collector,'SETTINGS',self.data/'collector-settings.json'),patch.object(collector,'BRIDGE',self.data/'bridge')]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def call(self,*args):
        out=io.StringIO();err=io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):code=cli.main(['--config',str(self.config),*args])
        return code,out.getvalue(),err.getvalue()
    def init(self):return self.call('init','--source','eml','--data-dir',str(self.data))
    def test_init_is_safe_and_wont_overwrite(self):
        self.assertEqual(self.init()[0],0);self.assertFalse(config.load(self.config)['privacy']['allow_external_delivery'])
        before=self.config.read_bytes();self.assertEqual(self.init()[0],2);self.assertEqual(before,self.config.read_bytes())
    def test_offline_demo_requires_no_model_or_mailbox(self):
        self.init()
        with patch.object(m,'local_api',side_effect=AssertionError('network')):
            code,path,_=self.call('demo')
        self.assertEqual(code,0);self.assertTrue(Path(path.strip()).exists())
        self.assertFalse((self.data/'collector.sqlite3').exists())
    def test_doctor_never_prints_credential_values(self):
        self.init()
        with patch.dict('os.environ',{'LMA_TELEGRAM_API_HASH':'sensitive-example-value'}):
            code,out,_=self.call('doctor')
        self.assertEqual(code,0);self.assertNotIn('sensitive-example-value',out);self.assertIn('credentials_available',out)
    def test_exception_details_are_not_logged(self):
        self.init()
        with patch.object(cli,'demo',side_effect=RuntimeError('private-key-content')):code,out,err=self.call('demo')
        self.assertEqual(code,2);self.assertNotIn('private-key-content',out+err)
    def test_import_preserves_non_utf8_source_and_never_calls_a_model(self):
        import datetime as dt
        from email.utils import format_datetime
        from local_mail_agent import collector
        self.init();raw=('Date: '+format_datetime(dt.datetime.now(dt.timezone.utc))+'\nSubject: example\nContent-Type: text/plain; charset=iso-8859-1\n\n').encode()+b'caf\xe9'
        path=self.root/'message.eml';path.write_bytes(raw)
        with patch.object(m,'local_api',side_effect=AssertionError('model')):
            code,out,_=self.call('import',str(path))
        self.assertEqual(code,0);self.assertEqual(json.loads(out)['queued'],1)
        with collector.store() as conn:self.assertEqual(conn.execute('SELECT raw FROM queue').fetchone()[0],raw)
    def test_import_purges_completed_raw_and_keeps_new_unclassified_mail(self):
        import datetime as dt
        from email.utils import format_datetime
        from local_mail_agent import collector as c
        self.init();date=format_datetime(dt.datetime.now(dt.timezone.utc))
        first=self.root/'first.eml';first.write_bytes(('Date: '+date+'\nSubject: first\n\nSynthetic first message').encode())
        second=self.root/'second.eml';second.write_bytes(('Date: '+date+'\nSubject: second\n\nSynthetic second message').encode())
        with patch.object(m,'local_api',side_effect=AssertionError('model')):
            self.assertEqual(self.call('import',str(first))[0],0)
            with c.store() as conn:
                key=conn.execute('SELECT dedupe_key FROM queue').fetchone()[0]
                conn.execute('INSERT INTO processed VALUES (?,?,?,?)',(key,'synthetic-result','review',m.now()))
                conn.execute("UPDATE queue SET status='classified',created=?",((dt.datetime.now(m.TZ)-dt.timedelta(days=4)).isoformat(),))
            self.assertEqual(self.call('import',str(second))[0],0)
        with c.store() as conn:
            self.assertIsNone(conn.execute('SELECT raw FROM queue WHERE dedupe_key=?',(key,)).fetchone()[0])
            self.assertEqual(conn.execute("SELECT raw FROM queue WHERE status='pending'").fetchone()[0],second.read_bytes())
