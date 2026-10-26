"""Sanitized, versioned report documents shared by all delivery adapters."""
import datetime as dt
import hashlib
import html
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo
from . import config,mail_assistant as m

MAX_PACKET=2*1024*1024
ICONS={'客户':'💬','订单':'📦','账单':'🧾','订阅':'📰','广告':'🛍️','个人':'✉️','其他':'📌'}

def exportable(row):
    if not row or row['gate']!='review':return None
    value={key:row[key] for key in ('id','subject','summary','category','priority','needs_reply','deadline')}
    text='\n'.join(str(value[k]) for k in ('subject','summary','deadline'))
    if any(p.search(text) for p in (m.URL,m.EMAIL,m.LONG_SECRET,m.DIGITS,m.SENSITIVE,m.PRIVATE_CONTEXT,m.INJECTION)):
        return None
    return value

def report_id(slot):
    value=dt.datetime.fromisoformat(slot)
    if value.tzinfo is None:raise ValueError('report_slot_requires_timezone')
    return 'mail-'+value.strftime('%Y%m%d-%H%M')

def local_markdown(slot,items,coverage):
    parts=['# 邮件简报','', '本机编排的已脱敏分类摘要；未调用云端汇总。', '',
        '## 覆盖范围',
        '可整理摘要 '+str(len(items))+' 封；仅本机保留 '+str(coverage.get('local_only_messages',0))+' 封；处理异常 '+str(coverage.get('processing_errors',0))+' 封。',
        '服务商同步新鲜度：未独立验证。','']
    sections=[('💬 待回复',[x for x in items if x['needs_reply']]),
              ('🔴 优先处理',[x for x in items if not x['needs_reply'] and x['priority']=='高']),
              ('📰 兴趣与其他动态',[x for x in items if not x['needs_reply'] and x['priority']!='高'])]
    for title,group in sections:
        parts+=['## '+title,'']
        if not group:parts+=['本轮无新事项。','']
        for item in group:
            parts+=['### '+item['subject']+' · '+item['id'],item['summary'],
                '分类：'+item['category']+' · 优先级：'+item['priority']]
            if item['deadline']:parts+=['期限：'+item['deadline']]
            parts+=['']
    return '\n'.join(parts)

def build(slot,items,coverage,markdown=None,accounts=None):
    safe_items=[]
    for item in items:
        row={**item,'gate':'review'}
        safe=exportable(row)
        if safe is None:raise ValueError('unsafe_report_item')
        safe_items.append(safe)
    text=markdown if markdown is not None else local_markdown(slot,safe_items,coverage)
    text,_=m.redact(text.split('## 收件邮箱（本机补充）',1)[0])
    if any(p.search(text) for p in (m.PRIVATE_CONTEXT,m.INJECTION,m.EMAIL,m.URL,m.LONG_SECRET)):
        raise ValueError('unsafe_report_text')
    own={}
    if config.get()['privacy']['show_own_addresses']:
        for item in safe_items:
            addresses=(accounts or {}).get(item['id'],[])
            if any(not isinstance(a,str) or not m.EMAIL.fullmatch(a) for a in addresses):raise ValueError('invalid_own_address')
            if addresses:own[item['id']]=sorted(set(addresses))
    value={'version':1,'report_id':report_id(slot),'slot':slot,'timezone':config.get()['app']['timezone'],
        'markdown':text,'messages':safe_items,'coverage':coverage,'own_addresses':own}
    validate(value)
    return value

def validate(value):
    if not isinstance(value,dict) or set(value)!={'version','report_id','slot','timezone','markdown','messages','coverage','own_addresses'}:
        raise ValueError('invalid_report_packet')
    if value['version']!=1 or value['report_id']!=report_id(value['slot']):raise ValueError('invalid_report_identity')
    ZoneInfo(value['timezone'])
    if not isinstance(value['markdown'],str) or not 0<len(value['markdown'])<=200000:raise ValueError('invalid_report_length')
    if not isinstance(value['messages'],list) or len(value['messages'])>200:raise ValueError('invalid_message_count')
    coverage_keys={'classified_messages','local_only_messages','processing_errors','collection_state','enabled_accounts',
        'last_collection','pending_backlog','remote_mail_sync_freshness','sample'}
    coverage=value['coverage']
    if not isinstance(coverage,dict) or set(coverage)-coverage_keys:raise ValueError('invalid_coverage')
    for key,entry in coverage.items():
        if key in {'classified_messages','local_only_messages','processing_errors','enabled_accounts','pending_backlog'}:
            if entry is not None and (type(entry) is not int or not 0<=entry<=1000000):raise ValueError('invalid_coverage_count')
        elif key=='last_collection':
            if entry is not None:dt.datetime.fromisoformat(entry)
        elif key=='sample':
            if entry!='synthetic':raise ValueError('invalid_sample_marker')
        elif not isinstance(entry,str) or not re.fullmatch(r'[a-z_]{1,60}',entry):raise ValueError('invalid_coverage_state')
    for item in value['messages']:
        if not isinstance(item,dict) or set(item)!={'id','subject','summary','category','priority','needs_reply','deadline'}:
            raise ValueError('invalid_report_message')
        if not isinstance(item['id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',item['id']):raise ValueError('invalid_message_id')
        if any(not isinstance(item[k],str) or len(item[k])>2000 for k in ('subject','summary','category','priority','deadline')):
            raise ValueError('invalid_message_field')
        if item['priority'] not in {'高','中','低'} or item['category'] not in m.CATEGORIES or type(item['needs_reply']) not in {bool,int} or item['needs_reply'] not in {0,1}:
            raise ValueError('invalid_message_enum')
        if exportable({**item,'gate':'review'}) is None:raise ValueError('unsafe_report_message')
    if not isinstance(value['own_addresses'],dict):raise ValueError('invalid_own_addresses')
    ids={item['id'] for item in value['messages']}
    for key,addresses in value['own_addresses'].items():
        if key not in ids or not isinstance(addresses,list) or len(addresses)>50 or any(not isinstance(a,str) or not m.EMAIL.fullmatch(a) for a in addresses):
            raise ValueError('invalid_own_addresses')
    safe,_=m.redact(value['markdown'])
    if safe!=value['markdown'] or any(p.search(safe) for p in (m.PRIVATE_CONTEXT,m.INJECTION)):
        raise ValueError('unsafe_report_text')
    if len(json.dumps(value,ensure_ascii=False).encode())>MAX_PACKET:raise ValueError('packet_too_large')
    return value

def fingerprint(value):
    validate(value)
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def preview(value):
    date=dt.datetime.fromisoformat(value['slot'])
    lines=['📬 邮件简报 · '+date.strftime('%m-%d %H:%M')+' · '+value['timezone'],
           '已脱敏摘要 '+str(len(value['messages']))+' 封；完整内容见附件。']
    ordered=sorted(value['messages'],key=lambda x:(not x['needs_reply'],x['priority']!='高',x['id']))
    for item in ordered[:3]:lines+=['💬 '+item['subject'][:50]+'：'+item['summary'][:110]]
    return '\n\n'.join(lines)[:900]

def markdown(value):
    result=value['markdown']
    if value['own_addresses']:
        result+='\n\n## 本人收件邮箱\n\n'+'\n'.join('- '+key+' · '+'、'.join(addresses) for key,addresses in value['own_addresses'].items())+'\n'
    return result

def render(value):
    validate(value)
    from . import reading_layout
    coverage={k:v for k,v in value['coverage'].items() if k in reading_layout.COUNTS and v is not None}
    return reading_layout.render(value['slot'],value['messages'],coverage,value['markdown'],
        value['own_addresses'],timezone=value['timezone'])

def save(value,directory=None):
    directory=Path(directory or m.PRIVATE/'reports');directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    stem=dt.datetime.fromisoformat(value['slot']).strftime('%Y-%m-%d_%H%M')
    for suffix,content in [('json',json.dumps(value,ensure_ascii=False,indent=2).encode()),('md',markdown(value).encode()),('html',render(value))]:
        path=directory/(stem+'.'+suffix)
        temp=path.with_suffix(path.suffix+'.tmp');temp.write_bytes(content);temp.chmod(0o600);temp.replace(path)
    return stem+'.md'
