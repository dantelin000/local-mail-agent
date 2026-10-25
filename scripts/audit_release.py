#!/usr/bin/env python3
"""Audit tracked/unignored source, Git history and actual distribution contents.

Only paths, line numbers and finding categories are printed; matched values are never printed.
This is a release check, not a general-purpose secret detector.
"""
import argparse
import io
import json
from pathlib import Path,PurePosixPath
import re
import subprocess
import tarfile
import zipfile

PATTERNS={
    'personal_absolute_path':re.compile(r'''/(?:Users|home)/[^/\s"'<>]+'''),
    'private_key':re.compile(r'-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----'),
    'telegram_token':re.compile(r'\b\d{6,15}:[A-Za-z0-9_-]{25,}\b'),
    'api_key':re.compile(r'\b(?:sk-[A-Za-z0-9_-]{20,}|gh[opusr]_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b'),
    'inline_secret':re.compile(r'''(?im)\b(?:api_key|api_hash|bot_token|password|client_secret|access_token|refresh_token)\s*=\s*["'][^"'\n]{16,}["']'''),
    'production_bot_reference':re.compile(r'hermes_[a-z0-9]{12,}_bot'),
    'production_host_alias':re.compile(r'oracle-ubuntu-[0-9]+'),
    'email_address':re.compile(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'),
    'ipv4_address':re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])'),
}
FORBIDDEN_SUFFIXES=('.eml','.mbox','.sqlite','.sqlite3','.db','.session','.pem','.key','.log','.pyc')
FORBIDDEN_NAMES={'auth.json','credentials.json','token.json','tokens.json','oauth.json','config.toml','.env'}
FORBIDDEN_DIRS={'private','archive','data','sessions','.venv','__pycache__'}

def scan(name,raw):
    findings=[];path=PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts:findings.append({'path':name,'kind':'unsafe_archive_path'})
    if path.name in FORBIDDEN_NAMES or 'credentials' in path.name.casefold() or path.name.endswith(FORBIDDEN_SUFFIXES) or re.search(r'\.(?:sqlite3?|db|session)(?:[-.].*)?$',path.name) or set(path.parts)&FORBIDDEN_DIRS:
        findings.append({'path':name,'kind':'runtime_or_private_file'})
    try:text=raw.decode('utf-8')
    except UnicodeDecodeError:return findings+[{'path':name,'kind':'unexpected_binary'}]
    if '\x00' in text:return findings+[{'path':name,'kind':'unexpected_binary'}]
    for kind,pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            value=match.group()
            if kind=='email_address' and (value.endswith(('.test','.invalid','@example.com','@example.org','@example.net'))):continue
            if kind=='ipv4_address' and (value.startswith(('127.','192.0.2.','198.51.100.','203.0.113.')) or value=='0.0.0.0'):continue
            findings.append({'path':name,'line':text[:match.start()].count('\n')+1,'kind':kind})
    return findings

def tree_files(root):
    # Prefer exactly what could be committed; ignored private runtime files are excluded.
    if (root/'.git').exists():
        output=subprocess.run(['git','ls-files','-z','--cached','--others','--exclude-standard'],cwd=root,check=True,capture_output=True).stdout
        return [root/Path(name.decode()) for name in output.split(b'\0') if name]
    return [p for p in root.rglob('*') if p.is_file() and not set(p.relative_to(root).parts)&{'.git','.venv','__pycache__','dist','build'} and not any(x.endswith('.egg-info') for x in p.parts)]

def inspect(root,archives=(),history=False):
    findings=[];count=0;commits=0;members=0;tags=0
    for path in tree_files(root):
        if path.is_symlink():findings.append({'path':str(path.relative_to(root)),'kind':'symlink'});continue
        if path.is_file():findings+=scan(str(path.relative_to(root)),path.read_bytes());count+=1
    if history:
        result=subprocess.run(['git','rev-list','--all'],cwd=root,capture_output=True,text=True,check=True)
        for commit in result.stdout.splitlines():
            commits+=1
            meta=subprocess.run(['git','show','-s','--format=%an <%ae>%n%cn <%ce>%n%B',commit],cwd=root,capture_output=True,check=True).stdout
            findings+=scan('git-metadata/'+commit[:12],meta)
            names=subprocess.run(['git','ls-tree','-r','--name-only','-z',commit],cwd=root,capture_output=True,check=True).stdout
            for raw_name in names.split(b'\0'):
                if not raw_name:continue
                name=raw_name.decode();content=subprocess.run(['git','show',commit+':'+name],cwd=root,capture_output=True,check=True).stdout
                findings+=scan('git-history/'+commit[:12]+'/'+name,content)
        refs=subprocess.run(['git','for-each-ref','--format=%(objecttype) %(objectname)','refs/tags'],cwd=root,capture_output=True,text=True,check=True)
        for line in refs.stdout.splitlines():
            kind,oid=line.split()
            if kind=='tag':
                tags+=1;content=subprocess.run(['git','cat-file','tag',oid],cwd=root,capture_output=True,check=True).stdout
                findings+=scan('git-tag/'+oid[:12],content)
    for archive in archives:
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as package:
                for entry in package.infolist():
                    if entry.is_dir():continue
                    extra=entry.extra
                    while len(extra)>=4:
                        kind=int.from_bytes(extra[:2],'little');size=int.from_bytes(extra[2:4],'little')
                        if kind in {0x7875,0x7855,0x000d}:findings.append({'path':archive.name+'/'+entry.filename,'kind':'archive_owner_metadata'})
                        extra=extra[4+size:]
                    members+=1;findings+=scan(archive.name+'/'+entry.filename,package.read(entry))
        else:
            with tarfile.open(archive,'r:*') as package:
                for entry in package:
                    if entry.uid or entry.gid or entry.uname or entry.gname:
                        findings.append({'path':archive.name+'/'+entry.name,'kind':'archive_owner_metadata'})
                    if set(entry.pax_headers)-{'mtime'}:
                        findings.append({'path':archive.name+'/'+entry.name,'kind':'archive_extended_metadata'})
                    if entry.isdir():continue
                    if not entry.isfile():findings.append({'path':archive.name+'/'+entry.name,'kind':'archive_link'});continue
                    members+=1
                    with package.extractfile(entry) as handle:findings+=scan(archive.name+'/'+entry.name,handle.read())
    return {'source_files':count,'git_commits':commits,'git_tags':tags,'archive_members':members,'findings':findings,'passed':not findings}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--history',action='store_true')
    p.add_argument('archives',nargs='*',type=Path);args=p.parse_args()
    result=inspect(args.root.resolve(),args.archives,args.history);print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
