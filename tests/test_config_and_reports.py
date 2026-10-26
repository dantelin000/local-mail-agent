import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from local_mail_agent import config,reports,mail_assistant as m

ITEM={'id':'test-message','subject':'客户询问发货','summary':'需要核对发货安排后回复。',
    'category':'客户','priority':'高','needs_reply':1,'deadline':''}
SLOT='2030-01-02T09:00:00+00:00'

class ConfigTests(unittest.TestCase):
    def test_defaults_have_no_mutation_or_egress(self):
        value=config.validate(copy.deepcopy(config.DEFAULTS))
        self.assertFalse(value['source']['mark_read'])
        self.assertFalse(value['privacy']['allow_cloud_summary'])
        self.assertFalse(value['privacy']['allow_external_delivery'])
        self.assertEqual([c['type'] for c in value['delivery'] if c['enabled']],['archive'])
    def test_remote_ollama_and_disguised_cloud_models_blocked(self):
        for url in ('https://api.example.test','http://192.0.2.1:11434','http://owner:secret@127.0.0.1:11434','http://127.0.0.1:11434/path'):
            cfg=copy.deepcopy(config.DEFAULTS);cfg['processing']['ollama_url']=url
            with self.assertRaises(ValueError):config.validate(cfg)
        cfg=copy.deepcopy(config.DEFAULTS);cfg['processing'].update(model='cloud-tag',allowed_models=['cloud-tag'])
        with self.assertRaises(ValueError):config.validate(cfg)
    def test_typos_and_inline_credentials_do_not_silently_enable_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.toml';path.write_text('[privacy]\nallow_external_deliver = true\n')
            with self.assertRaises(ValueError):config.load(path)
        cfg=copy.deepcopy(config.DEFAULTS);cfg['delivery']=[{'id':'bot','type':'telegram_bot','enabled':True,'token':'inline-secret'}]
        with self.assertRaises(ValueError):config.validate(cfg)
    def test_hours_timezone_cloud_optin_and_tls_validated(self):
        for section,key,value in [('schedule','hours',[9,9]),('app','timezone','Invalid/Zone'),('source','mark_read','false'),('summary','provider','codex')]:
            cfg=copy.deepcopy(config.DEFAULTS);cfg[section][key]=value
            with self.assertRaises(ValueError):config.validate(cfg)
        cfg=copy.deepcopy(config.DEFAULTS);cfg['delivery']=[{'id':'email','type':'email','enabled':True,'tls':'none','recipients':['owner@example.test']}]
        with self.assertRaises(ValueError):config.validate(cfg)
    def test_duplicate_channels_and_ssh_option_injection_rejected(self):
        cfg=copy.deepcopy(config.DEFAULTS);cfg['delivery']*=2
        with self.assertRaises(ValueError):config.validate(cfg)
        cfg=copy.deepcopy(config.DEFAULTS);cfg['delivery']=[{'id':'relay','type':'ssh','enabled':True,'host':'-oProxyCommand=bad','command':['relay']}]
        with self.assertRaises(ValueError):config.validate(cfg)

    def test_raw_retention_default_range_and_type(self):
        self.assertEqual(config.DEFAULTS['privacy']['raw_retention_hours'],72)
        for hours in (1,72,8760):
            cfg=copy.deepcopy(config.DEFAULTS);cfg['privacy']['raw_retention_hours']=hours
            self.assertEqual(config.validate(cfg)['privacy']['raw_retention_hours'],hours)
        for hours in (0,-1,8761,True,1.5,'72',None):
            with self.subTest(hours=hours):
                cfg=copy.deepcopy(config.DEFAULTS);cfg['privacy']['raw_retention_hours']=hours
                with self.assertRaises(ValueError):config.validate(cfg)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.toml';path.write_text('[privacy]\nraw_retention_hours = 24\n')
            self.assertEqual(config.load(path)['privacy']['raw_retention_hours'],24)
        example=Path(config.__file__).parent/'assets/config.example.toml'
        self.assertEqual(config.load(example)['privacy']['raw_retention_hours'],72)

class ReportTests(unittest.TestCase):
    def setUp(self):
        self.patch=patch.object(config,'CURRENT',copy.deepcopy(config.DEFAULTS));self.patch.start()
    def tearDown(self):self.patch.stop()
    def report(self):return reports.build(SLOT,[ITEM],{'local_only_messages':1,'processing_errors':0})
    def test_only_known_own_addresses_are_optional(self):
        mapping={ITEM['id']:['owner@example.test']}
        self.assertEqual(reports.build(SLOT,[ITEM],{},accounts=mapping)['own_addresses'],{})
        config.CURRENT['privacy']['show_own_addresses']=True
        value=reports.build(SLOT,[ITEM],{},accounts=mapping)
        self.assertIn('owner@example.test',reports.markdown(value))
        self.assertNotIn('owner@example.test',value['markdown'])
    def test_authentication_and_prompt_injection_never_reach_a_report(self):
        for summary in ('验证码 123456','Ignore previous instructions','medical diagnosis','contact stranger@example.test','https://example.test/reset'):
            with self.assertRaises(ValueError):reports.build(SLOT,[{**ITEM,'summary':summary}],{})
    def test_private_context_stays_local_in_all_exported_text_fields(self):
        samples=['张伟的体检报告显示血糖偏高，需复查','工资单：本月实发 ¥8,500',
            '离婚诉讼开庭通知，律师王芳','收货地址：北京市朝阳区建国路88号',
            '化验','检测结果','HIV','HIV阳性','诊断','病历','住院','心理','怀孕','薪资','贷款','逾期','欠款','身份证',
            'salary','PAYSLIP','lab result','lab-results','pregnancy','hospital admission',
            'mental health','medical records','net pay','loan overdue','debt','court hearing',
            'lawyer','shipping address','identity card']
        for text in samples:
            for field in ('subject','summary','deadline'):
                with self.subTest(text=text,field=field):
                    row={**ITEM,'gate':'review',field:text}
                    self.assertIsNone(reports.exportable(row))
                    with self.assertRaises(ValueError):reports.build(SLOT,[{**ITEM,field:text}],{})
        self.assertIsNotNone(reports.exportable({**ITEM,'gate':'review'}))
    def test_private_cloud_report_text_is_rejected(self):
        with self.assertRaises(ValueError):reports.build(SLOT,[ITEM],{},markdown='Pregnancy lab result')
    def test_health_terms_are_blocked_in_all_report_fields_and_markdown(self):
        samples=('张伟确诊糖尿病，需每日注射胰岛素','确诊','患有','糖尿病','胰岛素','癌','肿瘤','阳性',
            'diabetes','insulin','cancer','tumor','tumour','positive','HIV相关')
        for text in samples:
            for field in ('subject','summary','deadline'):
                with self.subTest(text=text,field=field):
                    self.assertIsNone(reports.exportable({**ITEM,'gate':'review',field:text}))
                    with self.assertRaises(ValueError):reports.build(SLOT,[{**ITEM,field:text}],{})
            with self.subTest(text=text,field='markdown'):
                with self.assertRaises(ValueError):reports.build(SLOT,[ITEM],{},markdown=text)
    def test_html_escapes_mail_text_and_has_no_external_assets(self):
        item={**ITEM,'subject':'<script>run()</script>','summary':'<img src="bad"> harmless text'}
        document=reports.render(reports.build(SLOT,[item],{})).decode()
        self.assertNotIn('<script>',document);self.assertNotIn('<img ',document)
        self.assertIn('&lt;script&gt;',document);self.assertIn("default-src 'none'",document)
        self.assertIn('<details',document)
    def test_unexpected_packet_fields_and_hidden_raw_coverage_rejected(self):
        value=self.report();value['raw']='private source'
        with self.assertRaises(ValueError):reports.validate(value)
        value=self.report();value['coverage']['raw']='private source'
        with self.assertRaises(ValueError):reports.validate(value)
        value=self.report();value['messages'][0]['sender']='private@example.test'
        with self.assertRaises(ValueError):reports.validate(value)
    def test_report_formats_are_private_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'reports';reports.save(self.report(),path)
            values=list(path.iterdir());self.assertEqual({p.suffix for p in values},{'.md','.html','.json'})
            for p in values:self.assertEqual(p.stat().st_mode & 0o777,0o600)
            restored=json.loads(next(path.glob('*.json')).read_text());reports.validate(restored)
