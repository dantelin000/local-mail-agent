import importlib.util
import json
from pathlib import Path
import secrets
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from local_mail_agent import mail_assistant as m

class HTTPBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.patch=patch.object(m,'PRIVATE',Path(cls.tmp.name)/'private');cls.patch.start()
        cls.server=m.ThreadingHTTPServer(('127.0.0.1',0),m.Handler)
        cls.server.origin=f'http://127.0.0.1:{cls.server.server_port}'
        cls.server.csrf=secrets.token_hex(32);cls.server.model=m.MODEL
        cls.worker=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.worker.start()
        cls.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        result={'id':'blocked-test','source':'演示数据','received':'','sender':'[邮箱已隐藏]',
          'subject':'测试','category':'安全','priority':'中','summary':'仅本机',
          'needs_reply':0,'deadline':'','gate':'local_only','reasons':'[]','selected':0,
          'model':m.MODEL,'elapsed':0,'created':m.now()}
        m.save_result(result)
        result={**result,'id':'allowed-test','category':'客户','gate':'review'};m.save_result(result)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.worker.join()
        cls.patch.stop();cls.tmp.cleanup()

    def request(self,path,data=None,origin=True,token=True,host=None):
        headers={}
        if origin:headers['Origin']=self.server.origin
        if token:headers['X-CSRF-Token']=self.server.csrf
        if host:headers['Host']=host
        raw=None if data is None else json.dumps(data).encode()
        req=urllib.request.Request(self.server.origin+path,data=raw,headers=headers)
        try:
            with self.opener.open(req,timeout=5) as r:return r.status,json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:return e.code,json.loads(e.read())
            finally:e.close()

    def test_no_cloud_route_and_health(self):
        status,value=self.request('/api/health')
        self.assertEqual(status,200);self.assertFalse(value['cloud_export_enabled'])
        self.assertEqual(self.request('/api/export',{})[0],404)

    def test_other_origin_and_host_rejected(self):
        self.assertEqual(self.request('/api/demo',{},origin=False)[0],403)
        self.assertEqual(self.request('/api/demo',{},token=False)[0],403)
        self.assertEqual(self.request('/api/health',host='attacker.test')[0],403)

    def test_local_only_cannot_be_selected(self):
        self.assertEqual(self.request('/api/select',{'id':'blocked-test','selected':True})[0],409)
        with m.db() as c:self.assertEqual(c.execute('SELECT selected FROM messages WHERE id=?',('blocked-test',)).fetchone()[0],0)

    def test_selection_is_local_and_reversible(self):
        for selected in (True,False):
            status,value=self.request('/api/select',{'id':'allowed-test','selected':selected})
            self.assertEqual(status,200);self.assertFalse(value['cloud_export_enabled'])
            with m.db() as c:self.assertEqual(c.execute('SELECT selected FROM messages WHERE id=?',('allowed-test',)).fetchone()[0],int(selected))

    def test_non_eml_import_rejected(self):
        self.assertEqual(self.request('/api/import',{'files':[{'name':'sample.exe','data':'AA=='}]})[0],400)

if __name__=='__main__':unittest.main()
