"""Environment-first credentials, optionally backed by the OS keychain."""
import os
import re

SERVICE='local-mail-agent'

def read(name,required=True):
    if not isinstance(name,str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{2,80}',name):raise ValueError('invalid_credential_reference')
    value=os.environ.get(name)
    if not value:
        try:
            import keyring
            value=keyring.get_password(SERVICE,name)
        except Exception:value=None
    if required and not value:raise ValueError('credential_not_configured:'+name)
    return value

def store(name,value):
    if not re.fullmatch(r'[A-Z][A-Z0-9_]{2,80}',name):raise ValueError('invalid_credential_reference')
    try:import keyring
    except ImportError:raise ValueError('install_the_keychain_extra') from None
    keyring.set_password(SERVICE,name,value)
