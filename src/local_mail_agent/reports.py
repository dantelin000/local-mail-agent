"""Sanitized, versioned report documents shared by all delivery adapters."""
import datetime as dt
import copy
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
    value={'version':2,'template':config.get()['report']['template'],
        'report_id':report_id(slot),'slot':slot,'timezone':config.get()['app']['timezone'],
        'markdown':text,'messages':safe_items,'coverage':coverage,'own_addresses':own}
    validate(value)
    return value

def validate(value):
    fields={'version','report_id','slot','timezone','markdown','messages','coverage','own_addresses'}
    if not isinstance(value,dict) or type(value.get('version')) is not int or value['version'] not in {1,2}:
        raise ValueError('invalid_report_packet')
    if value['version']==2:fields.add('template')
    if set(value)!=fields:
        raise ValueError('invalid_report_packet')
    if value['version']==2 and value['template'] not in config.REPORT_TEMPLATES:raise ValueError('invalid_report_template')
    if value['report_id']!=report_id(value['slot']):raise ValueError('invalid_report_identity')
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
    seen=set()
    for item in value['messages']:
        if not isinstance(item,dict) or set(item)!={'id','subject','summary','category','priority','needs_reply','deadline'}:
            raise ValueError('invalid_report_message')
        if not isinstance(item['id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',item['id']) or item['id'] in seen:raise ValueError('invalid_message_id')
        seen.add(item['id'])
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

def with_template(value,name):
    """Make an independent, local presentation copy; never alter a queued packet."""
    validate(value)
    if name not in config.REPORT_TEMPLATES:raise ValueError('invalid_report_template')
    result=copy.deepcopy(value)
    result.update(version=2,template=name)
    return validate(result)

def render(value):
    validate(value)
    # Keep pre-template queued reports on their original renderer.
    if value['version']==1:return render_legacy(value)
    from . import report_layouts
    return report_layouts.render(value)

def render_legacy(value):
    validate(value);e=html.escape
    css=''' :root{color-scheme:light dark}*{box-sizing:border-box}body{font:17px/1.75 system-ui,sans-serif;background:#f3f6fc;color:#24334c;margin:0}main{max-width:1040px;margin:auto;padding:28px 20px 60px}header{background:linear-gradient(120deg,#253b75,#6254b5);color:#fff;padding:32px;border-radius:24px;margin-bottom:24px}h1{margin:0;font-size:30px}.meta{opacity:.85;font-size:14px}.stats{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0}.stat{background:#fff;padding:16px 22px;border-radius:14px;flex:1;min-width:140px;border-top:4px solid #6366f1}.stat:nth-child(2){border-color:#059669}.stat:nth-child(3){border-color:#ea8b23}.stat strong{display:block;font-size:28px}details{background:#fff;border-radius:16px;padding:18px;margin:18px 0;border-left:5px solid #6366f1}details:nth-of-type(3n+2){border-color:#059669}details:nth-of-type(3n){border-color:#ea8b23}summary{cursor:pointer;font-weight:700;font-size:20px}.card{padding:18px 0;border-top:1px solid #dbe3f2}.card:first-child{margin-top:16px}h3{font-size:18px;margin:0 0 8px}.badge{display:inline-block;padding:2px 9px;border-radius:20px;background:#eef2ff;color:#4338ca;font-size:13px;margin:4px 4px 8px 0}.high{background:#fee2e2;color:#b91c1c}.address,.id{font-size:13px;color:#64748b;overflow-wrap:anywhere}p{white-space:pre-wrap;overflow-wrap:anywhere}footer{font-size:14px;color:#64748b;margin-top:26px}@media(prefers-color-scheme:dark){body{background:#111827;color:#e2e8f0}.stat,details{background:#1e293b}.card{border-color:#334155}.badge{background:#312e81;color:#e0e7ff}.high{background:#7f1d1d;color:#fecaca}}@media print{header{background:#fff;color:#111}details{break-inside:avoid}body{background:#fff}}'''
    policy="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; object-src 'none'"
    parts=['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="'+policy+'"><title>邮件简报</title><style>'+css+'</style></head><body><main>',
        '<header><div>LOCAL MAIL AGENT</div><h1>📬 每日邮件简报</h1><div class="meta">'+e(value['slot'][:16].replace('T',' '))+' · '+e(value['timezone'])+'</div></header>',
        '<div class="stats">']
    for label,number in [('可读摘要',len(value['messages'])),('待回复',sum(bool(x['needs_reply']) for x in value['messages'])),('仅本机保留',value['coverage'].get('local_only_messages',0))]:
        parts+=['<div class="stat">'+label+'<strong>'+e(str(number))+'</strong></div>']
    parts+=['</div>']
    groups={}
    for item in value['messages']:
        if item['needs_reply']:group='💬 待回复'
        elif item['priority']=='高':group='🔴 优先处理'
        else:
            text=item['subject']+' '+item['summary']
            group=next((label for pattern,label in [(r'游戏|game|steam','🎮 游戏动态'),(r'房产|地产|property','🏠 房产'),(r'投资|股票|基金|invest|stock','📈 投资资讯'),(r'技术|开发|软件|code|release','💻 技术动态'),(r'购物|促销|折扣|sale|优惠','🛍️ 购物与优惠')] if re.search(pattern,text,re.I)),ICONS.get(item['category'],'📌')+' '+item['category'])
        groups.setdefault(group,[]).append(item)
    for group,items in sorted(groups.items(),key=lambda kv:(kv[0] not in {'💬 待回复','🔴 优先处理'},kv[0])):
        opened=' open' if group in {'💬 待回复','🔴 优先处理'} else ''
        parts+=['<details'+opened+'><summary>'+e(group)+' · '+str(len(items))+'封</summary>']
        for item in items:
            parts+=['<article class="card"><h3>'+e(item['subject'])+'</h3><span class="badge'+(' high' if item['priority']=='高' else '')+'">'+e(item['priority'])+'优先级</span><span class="badge">'+e(item['category'])+'</span><p>'+e(item['summary'])+'</p>']
            if item['deadline']:parts+=['<p>期限：'+e(item['deadline'])+'</p>']
            if value['own_addresses'].get(item['id']):parts+=['<p class="address">本人收件邮箱：'+e('、'.join(value['own_addresses'][item['id']]))+'</p>']
            parts+=['<div class="id">'+e(item['id'])+'</div></article>']
        parts+=['</details>']
    # Preserve the cloud/editorial report as well as per-message detail; escape all text.
    parts+=['<details><summary>📝 完整编排报告与覆盖说明</summary><p>'+e(value['markdown'])+'</p></details>',
        '<footer>原始邮件与敏感内容保留在本机。报告只包含通过脱敏检查的摘要；分类可能有误，请按需核对原邮件。此页面无外部脚本、图片或网络请求。</footer></main></body></html>']
    return ''.join(parts).encode()

def save(value,directory=None):
    directory=Path(directory or m.PRIVATE/'reports');directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    stem=dt.datetime.fromisoformat(value['slot']).strftime('%Y-%m-%d_%H%M')
    for suffix,content in [('json',json.dumps(value,ensure_ascii=False,indent=2).encode()),('md',markdown(value).encode()),('html',render(value))]:
        path=directory/(stem+'.'+suffix)
        temp=path.with_suffix(path.suffix+'.tmp');temp.write_bytes(content);temp.chmod(0o600);temp.replace(path)
    return stem+'.md'
