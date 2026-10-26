import base64
import hashlib
import re
import shutil
import subprocess
import unittest
from local_mail_agent import reading_layout as layout

SLOT='2026-10-26T21:00:00+11:00'

ITEM={'id':'sample-one','subject':'本机模型工具更新','summary':'模型管理工具增加本机使用说明。',
      'category':'订阅','priority':'低','needs_reply':0,'deadline':''}

class LayoutTests(unittest.TestCase):
    def test_interest_detail_preserves_full_summary_and_own_address(self):
        full='技术摘要；'*120
        doc=layout.render(SLOT,[{**ITEM,'summary':full}],{'local_only_messages':2},accounts={ITEM['id']:['owner@example.test']}).decode()
        self.assertIn(full,doc)
        self.assertIn('owner@example.test',doc)
        self.assertIn('data-topic="technology"',doc)
        self.assertNotIn('data-detail="topic-technology" open',doc)
        self.assertIn('仅本机保留2封',doc)
        self.assertNotIn('虚构',doc)
    def test_disjoint_groups_are_complete_without_invented_actions(self):
        items=[ITEM,{**ITEM,'id':'reply','needs_reply':1},
               {**ITEM,'id':'event','category':'个人','priority':'中'},
               {**ITEM,'id':'shop','subject':'优惠券与促销','summary':'周末购物优惠。'},
               {**ITEM,'id':'game','subject':'游戏促销','summary':'Steam sale'}]
        actions,optional,topics=layout.group(items)
        self.assertEqual([i['id'] for i in actions],['reply'])
        self.assertEqual([i['id'] for i in optional],['event'])
        self.assertEqual(sum(map(len,topics.values()))+len(actions)+len(optional),5)
        self.assertEqual(topics['games'][0]['id'],'game')
        self.assertEqual(topics['shopping'][0]['id'],'shop')
    def test_html_escaped_and_only_fixed_hash_authorized_script(self):
        doc=layout.render(SLOT,[{**ITEM,'subject':'<script>evil()</script>','summary':'<img src="https://evil.test">'}]).decode()
        self.assertNotIn('<script>evil()',doc);self.assertNotIn('<img ',doc)
        self.assertIn('&lt;script&gt;',doc)
        scripts=re.findall(r'<script[^>]*>(.*?)</script>',doc,re.S)
        self.assertEqual(len(scripts),1)
        digest=base64.b64encode(hashlib.sha256(scripts[0].encode()).digest()).decode()
        self.assertIn("script-src 'sha256-"+digest+"'",doc)
        self.assertNotRegex(scripts[0],r'fetch\s*\(|XMLHttpRequest|WebSocket')
        if not shutil.which('node'): return
        result=subprocess.run(['node','--check'],input=scripts[0],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
    def test_private_or_unknown_fields_cannot_enter_render_payload(self):
        for item,coverage,accounts in [({**ITEM,'raw':'private'}, {}, {}),
                (ITEM,{'raw':'private'},{}),(ITEM,{}, {'unrelated':['owner@example.test']}),
                (ITEM,{}, {ITEM['id']:['bad\r\naddress']})]:
            with self.assertRaises(ValueError):layout.render(SLOT,[item],coverage,accounts=accounts)
        with self.assertRaises(ValueError):layout.render(SLOT,[ITEM,ITEM])
