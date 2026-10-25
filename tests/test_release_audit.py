import importlib.util
from pathlib import Path
import unittest
import io
import tarfile
import tempfile

spec=importlib.util.spec_from_file_location('release_audit',Path(__file__).resolve().parents[1]/'scripts/audit_release.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)

class ReleaseAuditTests(unittest.TestCase):
    def test_values_are_never_echoed(self):
        value='123456:'+('A'*35)
        findings=audit.scan('source.py',value.encode())
        self.assertEqual(findings[0]['kind'],'telegram_token');self.assertNotIn(value,str(findings))
    def test_runtime_files_and_personal_paths_rejected(self):
        self.assertTrue(audit.scan('private/auth.json',b'{}'))
        personal='/'+'Users'+'/'+'private-person'+'/project'
        self.assertEqual(audit.scan('source.py',personal.encode())[0]['kind'],'personal_absolute_path')
    def test_reserved_example_addresses_are_allowed(self):
        self.assertFalse(audit.scan('example.txt',b'owner@example.test 127.0.0.1 192.0.2.1'))
    def test_archive_owner_metadata_is_detected_and_removed(self):
        normal_spec=importlib.util.spec_from_file_location('normalizer',Path(__file__).resolve().parents[1]/'scripts/normalize_archives.py')
        normal=importlib.util.module_from_spec(normal_spec);normal_spec.loader.exec_module(normal)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';root.mkdir();(root/'README.md').write_text('example')
            archive=Path(tmp)/'source.tar.gz'
            with tarfile.open(archive,'w:gz') as package:
                member=tarfile.TarInfo('README.md');member.uname='private-account';member.uid=123
                content=b'example';member.size=len(content);package.addfile(member,io.BytesIO(content))
            self.assertFalse(audit.inspect(root,[archive])['passed'])
            normal.normalize(archive);self.assertTrue(audit.inspect(root,[archive])['passed'])
