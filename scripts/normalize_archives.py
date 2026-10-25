#!/usr/bin/env python3
"""Remove host ownership and extended metadata from source tarballs before release."""
import argparse
import copy
import gzip
from pathlib import Path
import tarfile

def normalize(path):
    path=Path(path);temporary=path.with_name(path.name+'.normalized')
    try:
        with tarfile.open(path,'r:*') as source,temporary.open('wb') as raw:
            with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as compressed:
                with tarfile.open(fileobj=compressed,mode='w',format=tarfile.PAX_FORMAT) as target:
                    for entry in source:
                        if not (entry.isfile() or entry.isdir()):raise ValueError('Archive links are not supported')
                        normalized=copy.copy(entry);normalized.uid=0;normalized.gid=0
                        normalized.uname='';normalized.gname='';normalized.pax_headers={};normalized.mtime=0
                        if entry.isfile():
                            with source.extractfile(entry) as handle:target.addfile(normalized,handle)
                        else:target.addfile(normalized)
        temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('archives',nargs='+',type=Path);args=p.parse_args()
    for path in args.archives:normalize(path);print('Normalized '+path.name)
