"""Validated public configuration. Secrets are references, never TOML values."""
import copy
import ipaddress
import os
from pathlib import Path
import re
import sys
import tomllib
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULTS = {
    'app': {'data_dir': '', 'timezone': 'UTC', 'port': 8766},
    'source': {'kind': 'apple_mail', 'window_hours': 24, 'mark_read': False, 'max_per_run': 200},
    'processing': {'model': 'qwen3.5:9b', 'ollama_url': 'http://127.0.0.1:11434',
                   'allowed_models': ['qwen3.5:9b', 'gemma4:e4b', 'gemma4:12b-mlx', 'nemotron-3-nano:4b']},
    'schedule': {'collection_seconds': 900, 'hours': [9, 21]},
    'summary': {'provider': 'local', 'codex_path': 'codex'},
    'privacy': {'allow_cloud_summary': False, 'allow_external_delivery': False, 'show_own_addresses': False, 'raw_retention_hours': 72},
    'delivery': [{'id': 'archive', 'type': 'archive', 'enabled': True, 'path': 'archive'}],
}
DRIVER_KEYS = {
    'archive': {'path'},
    'email': {'host', 'port', 'tls', 'username', 'password_env', 'sender', 'recipients'},
    'telegram_bot': {'token_env', 'chat_id_env'},
    'telegram_saved': {'api_id_env', 'api_hash_env', 'session'},
    'ssh': {'host', 'command', 'timeout'},
}
ACTIVE = False
CURRENT = copy.deepcopy(DEFAULTS)
CONFIG_PATH = None

def default_config_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))/'local-mail-agent/config.toml'

def data_path(cfg=None):
    cfg = cfg or CURRENT
    value = cfg['app']['data_dir']
    if value:
        path=Path(value).expanduser()
        if not path.is_absolute():path=(CONFIG_PATH or default_config_path()).parent/path
        return path.resolve()
    if sys.platform == 'darwin':
        return Path.home()/'Library/Application Support/LocalMailAgent'
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))/'local-mail-agent'

def get():
    return CURRENT

def validate(cfg):
    if cfg['source']['kind'] not in {'apple_mail', 'eml'}:
        raise ValueError('source.kind must be apple_mail or eml')
    if cfg['summary']['provider'] not in {'local', 'codex'}:
        raise ValueError('summary.provider must be local or codex')
    if cfg['summary']['provider']=='codex' and not cfg['privacy']['allow_cloud_summary']:
        raise ValueError('Codex summaries require privacy.allow_cloud_summary=true')
    for section,key in [('source','mark_read'),('privacy','allow_cloud_summary'),
                        ('privacy','allow_external_delivery'),('privacy','show_own_addresses')]:
        if type(cfg[section][key]) is not bool:raise ValueError(section+'.'+key+' must be boolean')
    for section,key,low,high in [('app','port',1024,65535),('source','window_hours',1,168),
                                ('source','max_per_run',1,200),('schedule','collection_seconds',30,86400),
                                ('privacy','raw_retention_hours',1,8760)]:
        v=cfg[section][key]
        if type(v) is not int or not low<=v<=high:raise ValueError(section+'.'+key+' out of range')
    hours=cfg['schedule']['hours']
    if not isinstance(hours,list) or not hours or len(set(hours))!=len(hours) or any(type(h) is not int or not 0<=h<=23 for h in hours):
        raise ValueError('schedule.hours must contain distinct hours from 0 to 23')
    try:ZoneInfo(cfg['app']['timezone'])
    except (ZoneInfoNotFoundError,TypeError):raise ValueError('invalid app.timezone') from None
    parsed=urlsplit(cfg['processing']['ollama_url'])
    try:loopback=ipaddress.ip_address(parsed.hostname or '').is_loopback
    except ValueError:loopback=parsed.hostname=='localhost'
    if parsed.scheme!='http' or not loopback or parsed.username or parsed.password or parsed.path not in {'','/'} or parsed.query or parsed.fragment:
        raise ValueError('Ollama must use an HTTP loopback address without credentials or a path')
    models=cfg['processing']['allowed_models'];model=cfg['processing']['model']
    if not isinstance(models,list) or not models or any(not isinstance(v,str) or 'cloud' in v.casefold() for v in models) or model not in models:
        raise ValueError('choose an explicitly allowed local model')
    if not isinstance(cfg['app']['data_dir'],str) or not isinstance(cfg['summary']['codex_path'],str):raise ValueError('invalid path')
    if not isinstance(cfg['delivery'],list):raise ValueError('delivery must be an array of tables')
    identifiers=set()
    for item in cfg['delivery']:
        if not isinstance(item,dict):raise ValueError('invalid delivery table')
        kind=item.get('type');name=item.get('id')
        if kind not in DRIVER_KEYS or not isinstance(name,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}',name) or name in identifiers:
            raise ValueError('delivery requires a supported type and distinct safe id')
        identifiers.add(name)
        if set(item)-{'id','type','enabled'}-DRIVER_KEYS[kind]:raise ValueError('unknown delivery key; use environment references for secrets')
        if type(item.get('enabled')) is not bool:raise ValueError('delivery.enabled must be boolean')
        for key,value in item.items():
            if key.endswith('_env') and (not isinstance(value,str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{2,80}',value)):
                raise ValueError('credential references must be environment variable names')
        if kind=='email' and item.get('enabled'):
            if item.get('tls','starttls') not in {'starttls','ssl'}:raise ValueError('email requires starttls or ssl')
            if not isinstance(item.get('recipients'),list) or not item['recipients']:raise ValueError('email requires explicit recipients')
            if type(item.get('port',587)) is not int:raise ValueError('invalid SMTP port')
        if kind=='ssh' and item.get('enabled'):
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,200}',item.get('host','')):raise ValueError('invalid SSH host')
            command=item.get('command')
            if not isinstance(command,list) or not command or any(not isinstance(a,str) or not a or '\x00' in a for a in command):raise ValueError('ssh.command must be an argument list')
    return cfg

def load(path=None):
    path=Path(path).expanduser().resolve() if path else default_config_path()
    cfg=copy.deepcopy(DEFAULTS)
    if path.exists():
        with path.open('rb') as handle:custom=tomllib.load(handle)
        for section,values in custom.items():
            if section not in cfg:raise ValueError('unknown config section: '+section)
            if section=='delivery':cfg[section]=values;continue
            if not isinstance(values,dict) or set(values)-set(cfg[section]):raise ValueError('unknown config field: '+section)
            cfg[section].update(values)
    return validate(cfg)

def activate(cfg,path=None):
    global CURRENT,ACTIVE,CONFIG_PATH
    CURRENT=validate(copy.deepcopy(cfg));ACTIVE=True
    CONFIG_PATH=Path(path).expanduser().resolve() if path else default_config_path()
    from . import mail_assistant as m,collector as c
    m.PRIVATE=data_path();m.TZ=ZoneInfo(cfg['app']['timezone']);m.MODEL=cfg['processing']['model']
    m.OLLAMA=cfg['processing']['ollama_url'].rstrip('/');m.LOCAL_MODELS=set(cfg['processing']['allowed_models'])
    c.BRIDGE=m.PRIVATE/'MailReadBridge.app/Contents/MacOS/MailReadBridge'
    c.SETTINGS=m.PRIVATE/'collector-settings.json'
    return cfg
