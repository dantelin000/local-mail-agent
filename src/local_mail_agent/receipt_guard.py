"""Local receipt proof: immutable identity/content headers plus the complete MIME body."""
import hashlib
import re

IDENTITY_HEADERS = ('message-id', 'from', 'to', 'cc', 'bcc', 'subject', 'date', 'reply-to',
                    'mime-version', 'content-type', 'content-transfer-encoding', 'content-disposition')

def make_guard(raw):
    source = bytes(raw).decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
    head, separator, body = source.partition('\n\n')
    if not separator:
        raise ValueError('source_malformed')
    values = {}
    last = None
    for line in head.split('\n'):
        if line.startswith((' ', '\t')) and last:
            values[last][-1] += ' ' + line.strip(' \t\n\r')
            continue
        key, colon, value = line.partition(':')
        if not colon or not re.fullmatch(r'[A-Za-z0-9-]+', key):
            raise ValueError('source_malformed')
        last = key.lower()
        values.setdefault(last, []).append(value.strip(' \t\n\r'))
    digest = lambda text: hashlib.sha256(text.encode('utf-8')).hexdigest()
    return {'version': 2, 'body_hash': digest(body), 'headers': {
        key: digest('\n'.join(values[key])) for key in IDENTITY_HEADERS if key in values}}
