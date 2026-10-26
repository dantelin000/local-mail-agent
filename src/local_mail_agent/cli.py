"""User-facing commands; no live-account access without an explicit command."""
import argparse
import asyncio
import copy
import datetime as dt
import getpass
import html
import json
import mailbox
import os
from pathlib import Path
import shutil
import sys
import threading
from . import __version__,config

def parser():
    p=argparse.ArgumentParser(prog='local-mail-agent',description='Local-first mail triage and configurable digest delivery')
    p.add_argument('--version',action='version',version=__version__)
    p.add_argument('--config',type=Path,default=config.default_config_path())
    sub=p.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init',help='write a safe configuration; starts nothing')
    init.add_argument('--source',choices=['apple_mail','eml'],default='apple_mail')
    init.add_argument('--timezone',default='UTC');init.add_argument('--data-dir',type=Path)
    init.add_argument('--delivery',choices=['archive','email','telegram-bot','telegram-saved','ssh'],default='archive')
    init.add_argument('--allow-external',action='store_true',help='explicitly enable the selected external channel')
    init.add_argument('--template',choices=config.REPORT_TEMPLATES,default='reading')
    sub.add_parser('doctor',help='validate configuration and show credential availability without values')
    demo=sub.add_parser('demo',help='offline synthetic HTML demo; no mailbox, model or network access')
    choice=demo.add_mutually_exclusive_group()
    choice.add_argument('--template',choices=config.REPORT_TEMPLATES)
    choice.add_argument('--all-templates',action='store_true',help='generate all three layouts and a comparison index')
    demo.add_argument('--output-dir',type=Path)
    render=sub.add_parser('render',help='locally reformat a sanitized report JSON; never enqueue or send')
    render.add_argument('input',type=Path)
    render.add_argument('--template',choices=config.REPORT_TEMPLATES)
    render.add_argument('--output',type=Path,required=True)
    sub.add_parser('serve',help='open a loopback-only dashboard')
    run=sub.add_parser('run',help='foreground collection/scheduler; macOS users normally start the native App')
    run.add_argument('--no-dashboard',action='store_true')
    sub.add_parser('collect',help='collect once without calling the model')
    imp=sub.add_parser('import',help='queue EML/mbox files without calling a model')
    imp.add_argument('path',type=Path)
    sub.add_parser('report',help='process the current queue and generate one report now')
    sub.add_parser('status',help='show queue, receipt and delivery states without email content')
    sub.add_parser('pause');sub.add_parser('resume')
    sub.add_parser('deliver',help='process pending delivery attempts without reclassifying mail')
    native=sub.add_parser('apple-mail');native.add_argument('action',choices=['build','start'])
    login=sub.add_parser('telegram-login',help='interactive personal login for Saved Messages only')
    login.add_argument('--channel',default='telegram-saved')
    secret=sub.add_parser('secret',help='store a credential in the optional OS keychain')
    secret.add_argument('action',choices=['set']);secret.add_argument('name')
    relay=sub.add_parser('relay',help='receive one sanitized report through authenticated SSH stdin')
    relay.add_argument('--channel',required=True)
    return p

def initialize(args):
    if args.config.exists():raise ValueError('Configuration exists; edit it instead of overwriting it')
    template=(Path(__file__).parent/'assets/config.example.toml').read_text()
    template=template.replace('timezone = "UTC"','timezone = '+json.dumps(args.timezone))
    template=template.replace('kind = "apple_mail"','kind = '+json.dumps(args.source),1)
    template=template.replace('template = "reading"','template = '+json.dumps(args.template),1)
    if args.data_dir:template=template.replace('data_dir = ""','data_dir = '+json.dumps(str(args.data_dir.expanduser().resolve())))
    if args.delivery!='archive' and args.allow_external:
        template=template.replace('allow_external_delivery = false','allow_external_delivery = true')
        marker='id = "'+args.delivery+'"'
        before,sep,after=template.partition(marker)
        if not sep:raise ValueError('Unknown delivery preset')
        template=before+sep+after.replace('enabled = false','enabled = true',1)
    # Validate before creating a file.
    import tomllib
    config.validate(tomllib.loads(template))
    args.config.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    args.config.write_text(template);args.config.chmod(0o600)
    print('Configuration saved: '+str(args.config)+'\nNo background task has been started.')
    return 0

def doctor(cfg):
    from . import secrets
    channels=[]
    for channel in cfg['delivery']:
        references={k:bool(secrets.read(v,required=False)) for k,v in channel.items() if k.endswith('_env')}
        channels.append({'id':channel['id'],'type':channel['type'],'enabled':channel['enabled'],'credentials_available':references})
    return {'version':__version__,'source':cfg['source']['kind'],'timezone':cfg['app']['timezone'],
        'model':cfg['processing']['model'],'hours':cfg['schedule']['hours'],'privacy':cfg['privacy'],
        'report_template':cfg['report']['template'],
        'native_supported':sys.platform=='darwin','native_compiler_available':bool(shutil.which('clang')),
        'codex_available':bool(shutil.which(cfg['summary']['codex_path'])),'channels':channels}

def demo(args=None):
    from . import mail_assistant as m,reports,report_layouts
    items=[
        {'id':'demo-customer','subject':'客户询问发货安排','summary':'客户希望了解发货时间，需要核对订单后回复。','category':'客户','priority':'高','needs_reply':1,'deadline':''},
        {'id':'demo-tech','subject':'开源工具技术周刊','summary':'本期介绍工具更新、使用技巧和社区进展，可按兴趣阅读。','category':'订阅','priority':'低','needs_reply':0,'deadline':''},
        {'id':'demo-shopping','subject':'周末购物优惠','summary':'常用家居用品有折扣活动，无需立即处理。','category':'广告','priority':'低','needs_reply':0,'deadline':''},
        {'id':'demo-games','subject':'游戏更新动态','summary':'关注的游戏发布内容更新，包含玩法调整和社区活动。展开后可以阅读完整摘要：本次更新增加了合作玩法，调整了关卡难度，并公布下一轮社区活动安排。可按兴趣查看原邮件中的说明。','category':'订阅','priority':'低','needs_reply':0,'deadline':''},
        {'id':'demo-property','subject':'房产与居住周刊','summary':'本期介绍区域房源动态与居住趋势，可留作浏览资料。','category':'订阅','priority':'低','needs_reply':0,'deadline':''},
        {'id':'demo-investing','subject':'投资市场周报','summary':'本期整理市场观察与基金动态；这是资讯摘要，可自行核对原邮件。','category':'订阅','priority':'低','needs_reply':0,'deadline':''},
        {'id':'demo-order','subject':'订单包装方案可确认','summary':'商家提供两种包装方案，可有空时核对并决定是否调整。','category':'订单','priority':'中','needs_reply':0,'deadline':''}]
    slot=dt.datetime(2030,1,2,9,tzinfo=m.TZ).isoformat()
    value=reports.build(slot,items,{'local_only_messages':1,'processing_errors':0,'sample':'synthetic'})
    value['markdown']='# 合成演示报告\n\n这是虚构演示数据，未读取任何邮箱，也未调用模型或网络。\n\n'+value['markdown']
    target=getattr(args,'output_dir',None) or m.PRIVATE/'demo'
    if getattr(args,'all_templates',False):
        links=[]
        for name in config.REPORT_TEMPLATES:
            filename=reports.save(reports.with_template(value,name),target/name)
            href=name+'/'+str(Path(filename).with_suffix('.html'))
            links.append('<li><a href="'+href+'">'+report_layouts.LABELS[name]+'</a><span>'+html.escape(name)+'</span></li>')
        index='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>三种邮件简报样板</title><style>body{font:17px/1.8 system-ui,sans-serif;background:#eef2f8;color:#273448;margin:0;padding:32px 20px}main{max-width:640px;margin:auto}h1{font-size:28px}ul{padding:0;list-style:none}li{margin:18px 0;background:white;padding:20px;border-radius:12px;border-left:5px solid #6554ce}li:nth-child(2){border-color:#087f78}li:nth-child(3){border-color:#b4965e}a{color:#273448;font-weight:700}span{float:right;color:#617086;font-size:14px}p{font-size:14px;color:#617086}code{color:#273448}</style></head><body><main><h1>选择你的邮件阅读方式</h1><p>三份样板采用同一批虚构邮件，未读取邮箱、调用模型或发送报告。</p><ul>'''+''.join(links)+'''</ul><p>配置项：<code>[report] template = "reading"</code><br>可选 reading / compact / print；所有出口使用报告中已固定的选择。</p></main></body></html>'''
        path=target/'index.html';path.write_text(index);path.chmod(0o600)
    else:
        if getattr(args,'template',None):value=reports.with_template(value,args.template)
        filename=reports.save(value,target)
        path=target/Path(filename).with_suffix('.html')
    print(str(path))
    return 0

def render_report(args):
    from . import reports
    if args.input.stat().st_size>reports.MAX_PACKET:raise ValueError('packet_too_large')
    value=json.loads(args.input.read_bytes());reports.validate(value)
    if args.template:value=reports.with_template(value,args.template)
    if args.output.suffix.lower()!='.html' or args.output.resolve()==args.input.resolve():
        raise ValueError('choose_a_separate_html_output')
    document=reports.render(value)
    args.output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    temp=args.output.with_suffix('.html.tmp');temp.write_bytes(document);temp.chmod(0o600);temp.replace(args.output)
    print(str(args.output))
    return 0

def import_mail(path):
    from . import mail_assistant as m,collector as c
    if config.get()['source']['kind']!='eml':raise ValueError('Use source.kind="eml" for file import')
    c.configure();messages=[]
    if path.suffix.lower()=='.eml':
        if path.stat().st_size>10*1024*1024:raise ValueError('message_too_large')
        messages=[path.read_bytes()]
    else:
        if not path.is_dir() and path.suffix.lower()!='.mbox':raise ValueError('Use an EML file or mbox export')
        box=mailbox.mbox(path/'mbox' if path.is_dir() else path,create=False)
        try:
            for _,message in zip(range(200),box):messages.append(message.as_bytes())
        finally:box.close()
    items=[]
    for raw in messages:
        parsed=m.parse_eml(raw)
        items.append({'identity':'eml:'+c.canonical_key(raw),'account':'eml','received':parsed['received'],
            'raw':raw,'read_unchanged':True,'flag_unchanged':True})
    added,duplicates,outside=c.enqueue_messages(items)
    c.purge_raw()
    print(json.dumps({'queued':added,'duplicates':duplicates,'outside_window':outside}))
    return 0

async def personal_login(channel):
    from . import delivery,secrets
    api_id=secrets.read(channel.get('api_id_env','LMA_TELEGRAM_API_ID'),required=False) or getpass.getpass('Telegram API ID (hidden): ')
    api_hash=secrets.read(channel.get('api_hash_env','LMA_TELEGRAM_API_HASH'),required=False) or getpass.getpass('Telegram API hash (hidden): ')
    client=delivery.telegram_client(channel,(api_id,api_hash))
    try:
        await client.start(phone=lambda:getpass.getpass('Telegram phone (hidden): '),
            code_callback=lambda:getpass.getpass('Telegram login code (hidden): '),
            password=lambda:getpass.getpass('Telegram two-step password (hidden): '))
        me=await client.get_me()
        if not me or me.bot:raise ValueError('Saved Messages requires a personal Telegram account')
    finally:
        try:await client.disconnect()
        finally:delivery.protect_sessions()
    print('Personal session saved locally. No report was sent. Keep API credentials in environment variables or the OS keychain for later runs.')

def main(argv=None):
    args=parser().parse_args(argv)
    try:
        os.umask(0o077)
        if args.command=='init':return initialize(args)
        cfg=config.load(args.config);config.activate(cfg,args.config)
        from . import mail_assistant as m,collector as c,delivery,reports
        if args.command=='doctor':print(json.dumps(doctor(cfg),ensure_ascii=False,indent=2));return 0
        if args.command=='demo':return demo(args)
        if args.command=='render':return render_report(args)
        if args.command=='secret':
            from . import secrets
            secrets.store(args.name,getpass.getpass('Credential value (hidden): '));print('Saved to OS keychain.');return 0
        if args.command=='telegram-login':
            matches=[x for x in cfg['delivery'] if x['id']==args.channel and x['type']=='telegram_saved']
            if not matches:raise ValueError('Select a configured telegram_saved channel')
            m.setup();asyncio.run(personal_login(matches[0]));return 0
        if args.command=='relay':
            raw=sys.stdin.buffer.read(reports.MAX_PACKET+1)
            if len(raw)>reports.MAX_PACKET:raise ValueError('packet_too_large')
            value=delivery.relay(json.loads(raw),args.channel)
            print(json.dumps(value));return 0 if value['state']=='sent' else 2
        if args.command=='apple-mail':
            from . import apple_mail
            c.configure();print(str(apple_mail.build() if args.action=='build' else apple_mail.start()));return 0
        if args.command=='import':return import_mail(args.path)
        if args.command=='serve':m.run_server(cfg['app']['port'],m.MODEL);return 0
        c.configure()
        if args.command=='run':
            if not args.no_dashboard:
                def serve():
                    try:m.run_server(cfg['app']['port'],m.MODEL)
                    except OSError:pass
                threading.Thread(target=serve,daemon=True).start()
            c.loop();return 0
        if args.command=='collect':c.collect_once()
        elif args.command=='report':
            slot=dt.datetime.now(m.TZ).replace(second=0,microsecond=0).isoformat();c.run_slot(slot)
            with c.store() as conn:row=conn.execute('SELECT state,report FROM runs WHERE slot=?',(slot,)).fetchone()
            print(json.dumps(dict(row),ensure_ascii=False));return 0 if row['state']=='completed' else 2
        elif args.command in {'pause','resume'}:
            setting=c.settings();setting['enabled']=args.command=='resume';c.write_json(c.SETTINGS,setting)
        elif args.command=='deliver':
            delivery.stage_completed()
            for _ in range(30):
                result=delivery.process_one()
                if not result:break
            print(json.dumps(delivery.status(),ensure_ascii=False));return 0
        print(json.dumps(c.public_status(),ensure_ascii=False,indent=2));return 0
    except KeyboardInterrupt:return 130
    except Exception as exc:
        # Raw exceptions can contain tokens, addresses or message text. Never print them.
        if getattr(args,'command',None)=='relay':print(json.dumps({'state':'blocked','reason':'invalid_configuration_or_report'}))
        else:print('Operation failed ('+type(exc).__name__+'). Check configuration, credentials and prerequisites; private details were not logged.',file=sys.stderr)
        return 2
