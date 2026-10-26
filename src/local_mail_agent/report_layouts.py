"""Three offline presentations of the same validated report packet."""
import datetime as dt
import html
from pathlib import Path
from zoneinfo import ZoneInfo
from . import reading_layout

LABELS = {'reading': '彩色阅读版', 'compact': '紧凑速览版', 'print': '正式打印版'}
STATIC_POLICY = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; object-src 'none'; frame-ancestors 'none'"


def content_policy():
    # Only the fixed, hash-checked reading controls may run, even for older files.
    return reading_layout.content_policy()


def render(value):
    name = value['template']
    if name == 'reading':
        counts = {k: v for k, v in value['coverage'].items()
                  if k in reading_layout.COUNTS and v is not None}
        result = reading_layout.render(value['slot'], value['messages'], counts,
            value['markdown'], value['own_addresses'], value['timezone'])
        if value['coverage'].get('sample') == 'synthetic':
            result = result.replace('每日两次 · 阅读版'.encode(), '虚构邮件 · 版面演示'.encode())
        return result
    if name not in {'compact', 'print'}:
        raise ValueError('invalid_report_template')
    e = html.escape
    date = dt.datetime.fromisoformat(value['slot']).astimezone(ZoneInfo(value['timezone']))
    items = value['messages']
    actions, optional, topics = reading_layout.group(items)
    groups = [('📌', '需要关注', actions), ('💬', '可选决定', optional)]
    groups += [(icon, title, topics[key]) for key, icon, title, _ in
               reading_layout.TOPICS + [('other', '✉️', '其他动态', '')] if topics.get(key)]
    body = ['<header><div class="eyebrow">LOCAL MAIL AGENT <span>'+LABELS[name]+'</span></div>',
        '<h1>邮件简报</h1><p class="dateline">'+date.strftime('%Y年%m月%d日 · %H:%M')+' · '+e(value['timezone'])+'</p>']
    if value['coverage'].get('sample') == 'synthetic':
        body.append('<p class="sample">虚构邮件 · 版面演示 · 未读取邮箱或调用模型</p>')
    body.append('</header><div class="metrics" aria-label="本轮概览">')
    for label, count in [('可读摘要', len(items)), ('需要关注', len(actions)),
                         ('仅本机保留', value['coverage'].get('local_only_messages', 0))]:
        body.append('<div><strong>'+e(str(count or 0))+'</strong><span>'+label+'</span></div>')
    body.append('</div>')
    if name == 'compact':
        body.append('<p class="hint">先扫主题与标签，展开每一行查看完整摘要。兴趣资讯也全部保留。</p>')
    for index, (icon, title, messages) in enumerate(groups):
        if not messages: continue
        body.append('<section><h2><span aria-hidden="true">'+icon+'</span> '+title+'<small>'+str(len(messages))+'封</small></h2>')
        for item in messages:
            priority = {'高': 'high', '中': 'medium', '低': 'low'}[item['priority']]
            badges = '<span class="badge '+priority+'">'+e(item['priority'])+'优先级</span>'
            if item['needs_reply']: badges += '<span class="badge reply">待回复</span>'
            badges += '<span class="category">'+e(item['category'])+'</span>'
            if name == 'compact':
                body.append('<details class="message"'+(' open' if index == 0 else '')+'><summary><span class="subject">'+e(item['subject'])+'</span><span class="tags">'+badges+'</span></summary><div class="message-body">')
            else:
                body.append('<article class="message"><h3>'+e(item['subject'])+'</h3><div class="tags">'+badges+'</div><div class="message-body">')
            body.append('<p class="summary">'+e(item['summary'])+'</p>')
            if item['deadline']: body.append('<p class="deadline">期限：'+e(item['deadline'])+'</p>')
            addresses = value['own_addresses'].get(item['id'], [])
            if addresses: body.append('<p class="source">本人收件邮箱：'+e('、'.join(addresses))+'</p>')
            body.append('<p class="source">邮件编号：'+e(item['id'])+'</p></div>'+('</details>' if name == 'compact' else '</article>'))
        body.append('</section>')
    if not items: body.append('<p class="empty">本轮没有通过脱敏检查的新摘要。</p>')
    editorial = '<h2>完整编排报告</h2><div class="editorial">'+e(value['markdown'])+'</div>'
    if name == 'compact':
        body.append('<details class="appendix"><summary>📝 完整编排报告</summary><div class="editorial">'+e(value['markdown'])+'</div></details>')
    else: body.append('<section class="appendix">'+editorial+'</section>')
    body.append('<aside><h2>覆盖说明</h2><p>可读摘要 '+str(len(items))+' 封；仅本机保留 '+str(value['coverage'].get('local_only_messages', 0) or 0)+' 封；处理异常 '+str(value['coverage'].get('processing_errors', 0) or 0)+' 封。</p><p>邮件服务商的同步新鲜度未独立验证。原始邮件与敏感内容保留在本机；分类和摘要仍可回原邮件核对。</p></aside>')
    body.append('<footer>'+LABELS[name]+' · '+name+'-v1 <span>无外部脚本、图片或字体</span></footer>')
    template = (Path(__file__).parent/'assets'/(name+'-report.html')).read_text()
    return template.replace('<!--REPORT_BODY-->', ''.join(body)).replace('<!--CONTENT_POLICY-->', STATIC_POLICY).encode()
