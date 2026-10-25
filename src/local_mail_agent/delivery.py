"""Interchangeable sinks with immutable packets and durable duplicate suppression."""
import asyncio
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets as random
import shlex
import smtplib
import sqlite3
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formatdate
from . import config,mail_assistant as m,reports,secrets

class DeliveryError(Exception):
    state='rejected'
class Retryable(DeliveryError):
    state='retryable'
class Uncertain(DeliveryError):
    state='uncertain'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def bot_api(token,method,data,kind='application/json'):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    request=urllib.request.Request('https://api.telegram.org/bot'+token+'/'+method,data=data,headers={'Content-Type':kind})
    try:
        with opener.open(request,timeout=35) as response:
            content=response.read(2000000)
            value=json.loads(content)
    except urllib.error.HTTPError as exc:
        if exc.code==429:raise Retryable('telegram_rate_limited') from None
        if exc.code in {400,401,403}:raise DeliveryError('telegram_rejected') from None
        raise Uncertain('telegram_result_unknown') from None
    except Exception:raise Uncertain('telegram_result_unknown') from None
    if not value.get('ok'):raise DeliveryError('telegram_rejected')
    return value['result']

def send_bot(value,channel):
    token=secrets.read(channel.get('token_env','LMA_TELEGRAM_BOT_TOKEN'))
    chat=secrets.read(channel.get('chat_id_env','LMA_TELEGRAM_CHAT_ID'))
    if not re.fullmatch(r'\d{5,20}:[A-Za-z0-9_-]{20,100}',token) or not re.fullmatch(r'[1-9]\d{3,19}',chat):
        raise DeliveryError('invalid_telegram_credentials')
    try:target=bot_api(token,'getChat',json.dumps({'chat_id':chat}).encode())
    except Uncertain:raise Retryable('telegram_unreachable_before_send') from None
    if target.get('type')!='private' or str(target.get('id'))!=chat:raise DeliveryError('telegram_private_destination_required')
    boundary='mailagent'+random.token_hex(16);parts=[]
    for key,text in {'chat_id':chat,'caption':reports.preview(value)}.items():
        parts.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+key+'"\r\n\r\n'+text+'\r\n').encode())
    parts.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="document"; filename="'+value['report_id']+'.html"\r\nContent-Type: text/html; charset=utf-8\r\n\r\n').encode()+reports.render(value)+b'\r\n')
    parts.append(('--'+boundary+'--\r\n').encode())
    response=bot_api(token,'sendDocument',b''.join(parts),'multipart/form-data; boundary='+boundary)
    if type(response.get('message_id')) is not int or str(response.get('chat',{}).get('id'))!=chat:raise Uncertain('telegram_response_unverified')
    return {'message_id':str(response['message_id'])}

def valid_address(value):
    return isinstance(value,str) and bool(m.EMAIL.fullmatch(value)) and '\r' not in value and '\n' not in value

def send_email(value,channel):
    sender=channel.get('sender','');recipients=channel.get('recipients',[])
    if not valid_address(sender) or not recipients or any(not valid_address(v) for v in recipients):
        raise DeliveryError('invalid_email_destination')
    message=EmailMessage();message['From']=sender;message['To']=', '.join(recipients)
    message['Subject']='邮件简报 · '+value['slot'][:16].replace('T',' ')
    message['Date']=formatdate(localtime=False)
    message['Message-ID']='<'+value['report_id']+'.'+reports.fingerprint(value)[:16]+'@local-mail-agent.invalid>'
    message.set_content(reports.preview(value)+'\n\n'+reports.markdown(value))
    message.add_attachment(reports.render(value),maintype='text',subtype='html',filename=value['report_id']+'.html')
    host=channel.get('host','');tls=channel.get('tls','starttls');port=channel.get('port',465 if tls=='ssl' else 587)
    if not host or tls not in {'ssl','starttls'}:raise DeliveryError('smtp_tls_configuration_required')
    client=None
    try:
        context=ssl.create_default_context()
        if tls=='ssl':client=smtplib.SMTP_SSL(host,port,timeout=30,context=context)
        else:
            client=smtplib.SMTP(host,port,timeout=30);client.ehlo();client.starttls(context=context);client.ehlo()
        if channel.get('username'):
            client.login(channel['username'],secrets.read(channel.get('password_env','LMA_SMTP_PASSWORD')))
    except smtplib.SMTPAuthenticationError:
        if client:
            try:client.close()
            except Exception:pass
        raise DeliveryError('smtp_authentication_rejected') from None
    except Exception:
        if client:
            try:client.close()
            except Exception:pass
        raise Retryable('smtp_unreachable_before_send') from None
    try:
        try:refused=client.send_message(message,from_addr=sender,to_addrs=recipients)
        except smtplib.SMTPRecipientsRefused:raise DeliveryError('smtp_recipients_rejected') from None
        except smtplib.SMTPDataError as exc:
            if exc.smtp_code<500:raise Retryable('smtp_data_temporarily_rejected') from None
            raise DeliveryError('smtp_data_rejected') from None
        except Exception:raise Uncertain('smtp_result_unknown') from None
        if refused:raise Uncertain('smtp_partial_delivery_do_not_resend')
        return {'message_id':str(message['Message-ID'])}
    finally:
        try:client.quit()
        except Exception:
            try:client.close()
            except Exception:pass

def telegram_client(channel,credentials=None):
    try:from telethon import TelegramClient
    except ImportError:raise DeliveryError('install_the_telegram_extra') from None
    api_id,api_hash=credentials or (secrets.read(channel.get('api_id_env','LMA_TELEGRAM_API_ID')),
        secrets.read(channel.get('api_hash_env','LMA_TELEGRAM_API_HASH')))
    if not str(api_id).isdigit() or not re.fullmatch(r'[a-fA-F0-9]{32}',api_hash):raise DeliveryError('invalid_telegram_api_credentials')
    name=channel.get('session','saved-messages')
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,60}',name):raise DeliveryError('invalid_session_name')
    directory=m.PRIVATE/'sessions';directory.mkdir(mode=0o700,parents=True,exist_ok=True);directory.chmod(0o700)
    client=TelegramClient(str(directory/name),int(api_id),api_hash,receive_updates=False,
        request_retries=0,connection_retries=1,timeout=20,auto_reconnect=False,flood_sleep_threshold=0)
    client.session.save_entities=False
    return client

def protect_sessions():
    directory=m.PRIVATE/'sessions'
    if directory.exists():
        for path in directory.iterdir():
            if path.is_file() and not path.is_symlink():path.chmod(0o600)

async def saved_async(value,channel):
    client=telegram_client(channel)
    try:
        try:
            await client.connect()
            if not await client.is_user_authorized():raise DeliveryError('telegram_login_required')
            me=await client.get_me()
            if not me or me.bot:raise DeliveryError('telegram_personal_account_required')
        except DeliveryError:raise
        except Exception:raise Retryable('telegram_unreachable_before_send') from None
        document=io.BytesIO(reports.render(value));document.name=value['report_id']+'.html'
        try:message=await client.send_file('me',document,caption=reports.preview(value),force_document=True,parse_mode=None)
        except Exception:raise Uncertain('telegram_saved_result_unknown') from None
        if not message or type(message.id) is not int:raise Uncertain('telegram_saved_response_unverified')
        return {'message_id':str(message.id)}
    finally:
        try:await client.disconnect()
        finally:protect_sessions()

def send_saved(value,channel):
    return asyncio.run(saved_async(value,channel))

def send_archive(value,channel):
    path=Path(channel.get('path','archive')).expanduser()
    if not path.is_absolute():path=m.PRIVATE/path
    reports.save(value,path)
    return {'message_id':value['report_id']}

def send_ssh(value,channel):
    host=channel.get('host','');command=channel.get('command',[])
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,200}',host) or not command:raise DeliveryError('invalid_ssh_configuration')
    args=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12',host,shlex.join(command)]
    # Do not inherit mailbox/provider credentials into SSH.
    env={key:os.environ[key] for key in ('HOME','USER','LOGNAME','PATH','LANG','SSH_AUTH_SOCK') if key in os.environ}
    try:response=subprocess.run(args,input=json.dumps(value,ensure_ascii=False),text=True,capture_output=True,
        timeout=channel.get('timeout',90),env=env)
    except Exception:raise Retryable('ssh_transport_unavailable') from None
    try:receipt=json.loads(response.stdout) if len(response.stdout)<10000 else {}
    except ValueError:raise Retryable('ssh_invalid_receipt') from None
    if receipt.get('state')=='sent' and not response.returncode:return {'message_id':str(receipt.get('message_id','remote-confirmed'))}
    if receipt.get('state')=='uncertain':raise Uncertain('remote_delivery_requires_review')
    if receipt.get('state') in {'rejected','conflict','blocked'}:raise DeliveryError('remote_delivery_rejected')
    raise Retryable('ssh_or_remote_delivery_unavailable')

DRIVERS={'archive':send_archive,'email':send_email,'telegram_bot':send_bot,'telegram_saved':send_saved,'ssh':send_ssh}

@contextmanager
def ledger():
    m.setup();conn=sqlite3.connect(m.PRIVATE/'delivery.sqlite3',timeout=20);conn.row_factory=sqlite3.Row
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS deliveries(report_id TEXT,channel_id TEXT,digest TEXT,packet TEXT,channel TEXT,state TEXT,attempts INTEGER DEFAULT 0,next_attempt REAL DEFAULT 0,reason TEXT DEFAULT \'\',message_id TEXT,updated TEXT,PRIMARY KEY(report_id,channel_id))')
        conn.commit()
        with conn:yield conn
    finally:conn.close()

def enqueue(value,channels=None):
    digest=reports.fingerprint(value)
    for channel in (channels if channels is not None else config.get()['delivery']):
        if not channel.get('enabled'):continue
        with ledger() as conn:
            row=conn.execute('SELECT digest,channel FROM deliveries WHERE report_id=? AND channel_id=?',(value['report_id'],channel['id'])).fetchone()
            frozen=json.dumps(channel,sort_keys=True)
            if row:
                if row['digest']!=digest or row['channel']!=frozen:raise ValueError('delivery_packet_or_destination_changed')
                continue
            conn.execute('INSERT INTO deliveries(report_id,channel_id,digest,packet,channel,state,updated) VALUES (?,?,?,?,?,?,?)',
                (value['report_id'],channel['id'],digest,json.dumps(value,ensure_ascii=False),frozen,'pending',m.now()))

def recover():
    # Only invoke once after acquiring the worker lock, never from a second concurrent process.
    with ledger() as conn:
        for row in conn.execute("SELECT report_id,channel_id,channel FROM deliveries WHERE state='sending'").fetchall():
            kind=json.loads(row['channel'])['type']
            conn.execute('UPDATE deliveries SET state=?,reason=? WHERE report_id=? AND channel_id=?',
                ('retryable' if kind in {'archive','ssh'} else 'uncertain','interrupted_delivery',row['report_id'],row['channel_id']))

def process_one(report=None,channel_id=None):
    with ledger() as conn:
        conn.execute('BEGIN IMMEDIATE')
        query="SELECT * FROM deliveries WHERE state IN ('pending','retryable') AND next_attempt<=?";args=[time.time()]
        if report:query+=' AND report_id=?';args.append(report)
        if channel_id:query+=' AND channel_id=?';args.append(channel_id)
        rows=conn.execute(query+' ORDER BY updated',args).fetchall()
        row=next((r for r in rows if config.get()['privacy']['allow_external_delivery'] or json.loads(r['channel'])['type']=='archive'),None)
        if not row:return None
        row=dict(row);channel=json.loads(row['channel'])
        conn.execute("UPDATE deliveries SET state='sending',attempts=attempts+1,updated=? WHERE report_id=? AND channel_id=?",
            (m.now(),row['report_id'],row['channel_id']))
    try:
        value=json.loads(row['packet']);reports.validate(value)
        if channel['type']!='archive' and value['own_addresses'] and not config.get()['privacy']['show_own_addresses']:
            raise DeliveryError('own_address_export_not_enabled')
        receipt=DRIVERS[channel['type']](value,channel);state='sent';reason='';mid=receipt.get('message_id')
    except DeliveryError as exc:state=exc.state;reason=str(exc);mid=None
    except Exception:state='rejected';reason='delivery_configuration_or_packet_invalid';mid=None
    if state=='retryable' and row['attempts']+1>=8:state='rejected';reason='retry_limit'
    with ledger() as conn:
        conn.execute('UPDATE deliveries SET state=?,reason=?,message_id=?,updated=?,next_attempt=? WHERE report_id=? AND channel_id=?',
            (state,reason,mid,m.now(),time.time()+min(3600,60*2**min(row['attempts'],6)),row['report_id'],row['channel_id']))
    return {'state':state,'reason':reason,'message_id':mid,'report_id':row['report_id'],'channel_id':row['channel_id']}

def status():
    with ledger() as conn:
        return [dict(row) for row in conn.execute('SELECT report_id,channel_id,state,attempts,reason,message_id,updated FROM deliveries ORDER BY updated DESC LIMIT 30')]

def stage_completed():
    from . import collector as c
    with c.store() as conn:rows=[dict(r) for r in conn.execute("SELECT report FROM runs WHERE state='completed'")]
    for row in rows:
        path=m.PRIVATE/'reports'/Path(row['report']).with_suffix('.json')
        if path.exists() and not path.is_symlink():enqueue(json.loads(path.read_text()))

def relay(value,channel_id):
    reports.validate(value)
    if not config.get()['privacy']['allow_external_delivery']:return {'state':'blocked','reason':'external_delivery_not_enabled'}
    channels=[c for c in config.get()['delivery'] if c['id']==channel_id and c['enabled'] and c['type'] not in {'ssh','archive'}]
    if len(channels)!=1:return {'state':'blocked','reason':'select_one_enabled_nonrelay_channel'}
    enqueue(value,channels)
    result=process_one(value['report_id'],channel_id)
    if result:return result
    with ledger() as conn:row=conn.execute('SELECT state,reason,message_id FROM deliveries WHERE report_id=? AND channel_id=?',(value['report_id'],channel_id)).fetchone()
    if row:
        result={**dict(row),'duplicate':True}
        if result['state']=='sending':result.update(state='uncertain',reason='active_or_interrupted_remote_delivery')
        return result
    return {'state':'blocked','reason':'missing_receipt'}
