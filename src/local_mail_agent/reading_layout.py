"""Approved, offline reading layout shared by local archives and delivery adapters."""
import base64
import datetime as dt
import hashlib
import html
from pathlib import Path
import re
from zoneinfo import ZoneInfo

LAYOUT = 'reading-v1'
FIELDS = {'id', 'subject', 'summary', 'category', 'priority', 'needs_reply', 'deadline'}
TOPICS = [
    ('shopping', '🛍️', '购物与优惠', r'购物|促销|折扣|优惠|特卖|sale\b|discount|shopping|voucher|coupon'),
    ('investing', '📈', '投资与市场', r'投资|股票|基金|证券|股市|财经|invest|\bstocks?\b|trading|market|finance|dividend'),
    ('property', '🏠', '房产与居住', r'房产|地产|房屋|租赁|租房|看房|property|real estate|rental|mortgage|housing'),
    ('technology', '💻', '技术与本地 AI', r'技术|开发|软件|模型|人工智能|\bAI\b|code|release|developer|ollama|qwen|gemma|github|technology'),
    ('games', '🎮', '游戏与娱乐', r'游戏|娱乐|\bgames?\b|gaming|steam|playstation|xbox|nintendo'),
]
ADDRESS = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
COUNTS = {'classified_messages', 'local_only_messages', 'processing_errors', 'pending_backlog',
          'read_confirmed', 'read_needs_review', 'enabled_accounts'}


def content_policy():
    template = (Path(__file__).parent/'assets'/'reading-report.html').read_text()
    script = re.search(r'<script[^>]*>(.*?)</script>', template, re.S).group(1)
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    return "default-src 'none'; script-src 'sha256-"+digest+"'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'"


def validate(items, coverage, accounts):
    if not isinstance(items, list) or len(items) > 200:
        raise ValueError('invalid_reading_items')
    ids = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != FIELDS:
            raise ValueError('invalid_reading_fields')
        if not isinstance(item['id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', item['id']) or item['id'] in ids:
            raise ValueError('invalid_reading_id')
        ids.add(item['id'])
        for key in ('subject', 'summary', 'category', 'priority', 'deadline'):
            if not isinstance(item[key], str) or len(item[key]) > 2000:
                raise ValueError('invalid_reading_text')
        if item['category'] not in {'客户', '订单', '账单', '安全', '个人', '订阅', '广告', '其他'} or item['priority'] not in {'高', '中', '低'}:
            raise ValueError('invalid_reading_enum')
        if type(item['needs_reply']) not in {bool, int} or item['needs_reply'] not in {0, 1}:
            raise ValueError('invalid_reading_reply')
    if not isinstance(coverage, dict) or set(coverage) - COUNTS:
        raise ValueError('invalid_reading_coverage')
    if any(type(v) is not int or not 0 <= v <= 1000000 for v in coverage.values()):
        raise ValueError('invalid_reading_counts')
    if not isinstance(accounts, dict) or set(accounts) - ids:
        raise ValueError('invalid_reading_accounts')
    for addresses in accounts.values():
        if not isinstance(addresses, list) or len(addresses) > 50 or any(not isinstance(a, str) or not ADDRESS.fullmatch(a) for a in addresses):
            raise ValueError('invalid_reading_address')


def group(items):
    actions, optional, topics = [], [], {}
    for item in items:
        if item['needs_reply'] or item['priority'] == '高':
            actions.append(item)
        elif item['priority'] == '中' and item['category'] in {'客户', '订单', '账单', '个人'}:
            optional.append(item)
        else:
            text = item['subject'] + ' ' + item['summary']
            topic = next((key for key, _, _, pattern in reversed(TOPICS) if re.search(pattern, text, re.I)), 'other')
            topics.setdefault(topic, []).append(item)
    actions.sort(key=lambda x: (not x['needs_reply'], x['priority'] != '高', x['id']))
    return actions, optional, topics


def render(slot, items, coverage=None, markdown='', accounts=None, timezone='Australia/Melbourne'):
    coverage, accounts = coverage or {}, accounts or {}
    validate(items, coverage, accounts)
    date = dt.datetime.fromisoformat(slot)
    if date.tzinfo is None:
        raise ValueError('reading_slot_requires_timezone')
    date = date.astimezone(ZoneInfo(timezone))
    if not isinstance(markdown, str) or len(markdown) > 200000:
        raise ValueError('invalid_reading_markdown')
    e = html.escape
    actions, optional, topics = group(items)
    interests = sum(len(v) for v in topics.values())
    label = '早间' if date.hour < 12 else '晚间'
    headline = f'先看这{len(actions)}件，再看你感兴趣的。' if actions else '今天的新邮件，按主题排好了。'
    lead = f'本轮有{len(actions)}封需关注、{len(optional)}封可选决定，另有{interests}封兴趣与其他动态。'
    body = ['<header><div class="mp-topline"><span class="mp-wordmark">DAILY MAIL · 每日邮件</span><span class="mp-sample">每日两次 · 阅读版</span></div>',
            '<h1>'+e(headline)+'</h1><p class="mp-subtitle">'+date.strftime('%Y年%m月%d日')+' · '+label+'阅读版 · '+e(timezone)+' '+date.strftime('%H:%M')+'</p>',
            '<div class="mp-lead">'+e(lead)+'<strong> 想看哪一类，就展开哪一类。</strong></div></header>',
            '<div class="mp-toolbar" aria-label="阅读设置"><label class="mp-setting">字号<select data-control="font" aria-label="阅读字号"><option value="16">标准</option><option value="18" selected>舒适</option><option value="20">大字</option></select></label>',
            '<label class="mp-setting">外观<select data-control="theme" aria-label="阅读外观"><option value="system">随系统</option><option value="light">浅色</option><option value="dark">深色</option></select></label><button type="button" data-control="expand" aria-pressed="false">展开全部</button></div>',
            '<div class="mp-stats" aria-label="本轮概览">']
    for title, number in [('✉️ 可读摘要', len(items)), ('📌 需要关注', len(actions)), ('📰 兴趣与动态', interests)]:
        body.append('<div class="mp-stat"><span class="mp-stat-label">'+title+'</span><span class="mp-stat-number">'+str(number)+'<small>封</small></span></div>')
    body += ['</div><nav class="mp-nav" aria-label="简报目录"><a href="#mp-action">📌 需要关注</a><a href="#mp-optional">💬 可选决定</a><a href="#mp-interests">📰 兴趣资讯</a></nav>']

    def source(item):
        result = '<p class="mp-source">原邮件主题：'+e(item['subject'])+'<br>邮件编号：'+e(item['id'])
        if accounts.get(item['id']):
            result += '<br>本人收件邮箱：'+e('、'.join(accounts[item['id']]))
        return result+'</p>'

    def meta(item):
        return '<p class="mp-source">分类：'+e(item['category'])+' · 优先级：'+e(item['priority'])+'</p>' + ('<p>期限：'+e(item['deadline'])+'</p>' if item['deadline'] else '')

    def section(key, icon, title, count):
        return '<section class="mp-section" data-section="'+key+'" id="mp-'+key+'"><div class="mp-section-title"><h2><span class="mp-section-icon" aria-hidden="true">'+icon+'</span>'+title+'</h2><span>'+str(count)+'封</span></div>'

    body.append(section('action', '📌', '先看这些', len(actions)))
    for item in actions:
        badge = '待回复' if item['needs_reply'] else '优先关注'
        body += ['<article class="mp-action"><div class="mp-action-head"><span class="mp-priority">'+badge+'</span><h3>'+e(item['subject'])+'</h3></div>',
                 '<p>'+e(item['summary'])+'</p>',
                 '<details class="mp-mini" data-detail="source-'+e(item['id'])+'"><summary>邮件来源与本人收件邮箱</summary><div class="mp-detail-body">'+meta(item)+source(item)+'</div></details></article>']
    if not actions: body.append('<p class="mp-interests-intro">本轮没有被标为高优先级或待回复的新邮件。</p>')
    body.append('</section>'+section('optional', '💬', '有空再决定', len(optional)))
    for item in optional:
        body.append('<div class="mp-optional"><h3>'+e(item['subject'])+'</h3><p>'+e(item['summary'])+'</p><details class="mp-mini" data-detail="source-'+e(item['id'])+'"><summary>邮件来源与本人收件邮箱</summary><div class="mp-detail-body">'+meta(item)+source(item)+'</div></details></div>')
    if not optional: body.append('<p class="mp-interests-intro">本轮没有单独列出的可选事项。</p>')
    body.append('</section>'+section('interests', '📰', '留给兴趣的部分', interests))
    body.append('<p class="mp-interests-intro">先看主题提要；展开后保留每封邮件的完整脱敏摘要。</p>')
    for key, icon, title, _ in TOPICS + [('other', '✉️', '其他动态', '')]:
        values = topics.get(key, [])
        if not values: continue
        preview = '；'.join(item['subject'] for item in values[:2])
        if len(values) > 2: preview += f'；另有{len(values)-2}封'
        body.append('<details class="mp-interest" data-topic="'+key+'" data-detail="topic-'+key+'"><summary><span class="mp-interest-icon" aria-hidden="true">'+icon+'</span><span class="mp-interest-label"><span class="mp-interest-heading">'+title+'<span class="mp-interest-count">'+str(len(values))+'封</span></span><span class="mp-interest-preview">'+e(preview)+'</span></span><span class="mp-interest-indicator" aria-hidden="true"></span></summary><div class="mp-interest-body">')
        for item in values:
            body.append('<article class="mp-topic"><h3>'+e(item['subject'])+'</h3><p>'+e(item['summary'])+'</p>'+meta(item)+'<details class="mp-mini" data-detail="source-'+e(item['id'])+'"><summary>邮件来源与本人收件邮箱</summary>'+source(item)+'</details></article>')
        body.append('</div></details>')
    if not topics: body.append('<p class="mp-interests-intro">本轮没有新增兴趣资讯。</p>')
    body.append('</section>')
    if markdown:
        body.append('<details class="mp-private" data-detail="editorial"><summary>📝 完整编排报告</summary><div class="mp-editorial">')
        for line in markdown.splitlines():
            if line.startswith('#'): body.append('<h3>'+e(line.lstrip('#').strip())+'</h3>')
            elif line.strip(): body.append('<p>'+e(line)+'</p>')
        body.append('</div></details>')
    body.append('<details class="mp-private" data-detail="coverage"><summary>🔒 覆盖范围与本机保留</summary><p>本轮可读摘要'+str(len(items))+'封；仅本机保留'+str(coverage.get('local_only_messages', 0))+'封；处理异常'+str(coverage.get('processing_errors', 0))+'封。</p>')
    if 'read_confirmed' in coverage:
        body.append('<p>已读回写记录：'+str(coverage['read_confirmed'])+'封已确认；'+str(coverage.get('read_needs_review', 0))+'封待核对。这是采集程序的回写记录。</p>')
    body += ['<p>本轮为去重后的新处理邮件。邮件服务商的同步新鲜度未独立验证；分类和摘要仍可回原邮件核对。</p></details>',
             '<footer class="mp-footer"><span>原始邮件与敏感内容保留在本机</span><span>阅读版 · '+LAYOUT+'</span></footer><span class="mp-accessible" role="status" aria-live="polite" data-control="notice"></span>']
    template = (Path(__file__).parent/'assets'/'reading-report.html').read_text()
    script = re.search(r'<script[^>]*>(.*?)</script>', template, re.S).group(1)
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    result = template.replace('<!--REPORT_BODY-->', ''.join(body)).replace('<!--SCRIPT_HASH-->', digest)
    return result.encode()
