"""Build a private, per-install native App with its own macOS consent identity."""
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
from . import config,mail_assistant as m

def build():
    if sys.platform!='darwin':raise ValueError('Apple Mail requires macOS; use EML import on other systems')
    if not shutil.which('clang'):raise ValueError('Install Xcode Command Line Tools first')
    m.setup();bundle=m.PRIVATE/'MailReadBridge.app';contents=bundle/'Contents'
    binary=contents/'MacOS/MailReadBridge';binary.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    info={'CFBundleIdentifier':'org.localmailagent.bridge','CFBundleName':'Local Mail Agent',
        'CFBundleDisplayName':'Local Mail Agent','CFBundleExecutable':'MailReadBridge',
        'CFBundlePackageType':'APPL','CFBundleVersion':'1','LSUIElement':True,
        'NSAppleEventsUsageDescription':'读取已启用邮箱的收件箱，在本机分类脱敏。只有配置开启时，才在成功处理并核对身份后设为已读；不发送、删除或移动原邮件。',
        'LMAPythonExecutable':sys.executable,'LMAConfigPath':str(config.CONFIG_PATH or config.default_config_path())}
    manifest=contents/'Info.plist';manifest.write_bytes(plistlib.dumps(info))
    command=['clang','-fobjc-arc','-Wno-deprecated-declarations','-framework','Cocoa','-framework','ScriptingBridge',
        '-framework','ApplicationServices',str(Path(__file__).parent/'native/mail_bridge.m'),'-o',str(binary),
        '-Wl,-sectcreate,__TEXT,__info_plist,'+str(manifest)]
    result=subprocess.run(command,capture_output=True)
    if result.returncode:raise ValueError('Native build failed; check Xcode Command Line Tools')
    subprocess.run(['codesign','--force','--sign','-','--identifier','org.localmailagent.bridge',str(bundle)],check=True,capture_output=True)
    binary.chmod(0o700)
    return bundle

def start():
    bundle=m.PRIVATE/'MailReadBridge.app'
    expected=str(config.CONFIG_PATH or config.default_config_path())
    try:info=plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
    except (OSError,ValueError):info={}
    if info.get('LMAConfigPath')!=expected or info.get('LMAPythonExecutable')!=sys.executable:build()
    subprocess.run(['open',str(bundle)],check=True)
    return bundle
