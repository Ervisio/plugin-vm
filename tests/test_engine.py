import json
import os
import socket
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugin'))
import vm_engine as vm

class VirtualizationTests(unittest.TestCase):
    def test_nested_vps_without_kvm_gives_provider_hint(self):
        result = vm.virtualization_diagnostics(Path('/dev/not-present-kvm'), {'vmx', 'hypervisor'}, 'kvm')
        self.assertTrue(result['insideVM'])
        self.assertFalse(result['canAccelerate'])
        self.assertTrue(any('provider' in msg or 'VPS' in msg for msg in result['hints']))

    def test_bare_metal_missing_flags_gives_bios_hint(self):
        result = vm.virtualization_diagnostics(Path('/dev/not-present-kvm'), set(), 'none')
        self.assertEqual(result['cpuExtension'], 'none')
        self.assertFalse(result['canAccelerate'])
        self.assertTrue(any('BIOS' in msg for msg in result['hints']))

    def test_kvm_api_access_is_tested_not_only_exists(self):
        with tempfile.NamedTemporaryFile() as f, patch('vm_engine.os.open', return_value=123), \
                patch('vm_engine.os.close'), patch('fcntl.ioctl', return_value=12):
            result = vm.virtualization_diagnostics(Path(f.name), {'svm'}, 'kvm')
            self.assertEqual(result['cpuExtension'], 'svm')
            self.assertTrue(result['deviceAccessible'])
            self.assertTrue(result['canAccelerate'])

    def test_nested_for_guests_disabled_does_not_block_normal_vm(self):
        # A working KVM device starts normal VMs even when guest-of-guest nesting is disabled.
        orig_exists=Path.exists
        orig_read=Path.read_text
        def exists(path):
            if str(path)=='/sys/module/kvm_intel/parameters/nested': return True
            if str(path)=='/sys/module/kvm_amd/parameters/nested': return False
            return orig_exists(path)
        def read(path,*args,**kwargs):
            if str(path)=='/sys/module/kvm_intel/parameters/nested': return 'N\n'
            return orig_read(path,*args,**kwargs)
        with tempfile.NamedTemporaryFile() as f, patch('vm_engine.os.open', return_value=123), \
                patch('vm_engine.os.close'), patch('fcntl.ioctl',return_value=12), \
                patch.object(Path,'exists',exists), patch.object(Path,'read_text',read):
            result=vm.virtualization_diagnostics(Path(f.name),{'vmx'},'none')
            self.assertTrue(result['canAccelerate'])
            self.assertEqual(result['nestedForGuests'],'disabled')
            self.assertTrue(any('Normal VMs can still run' in msg for msg in result['hints']))

    def test_bad_kvm_api_rejected(self):
        with tempfile.NamedTemporaryFile() as f, patch('vm_engine.os.open', return_value=123), \
                patch('vm_engine.os.close'), patch('fcntl.ioctl', return_value=8):
            result = vm.virtualization_diagnostics(Path(f.name), {'vmx'}, 'none')
            self.assertFalse(result['canAccelerate'])
            self.assertIn('Unsupported KVM API', result['hints'][0])

class SecurityTests(unittest.TestCase):
    def test_name_cannot_be_command_injected(self):
        for value in ('', 'A-B', 'a/b', '../root', 'vm; rm -rf /', '--help', 'x'*70, None):
            with self.subTest(value=value), self.assertRaises(vm.VmError):
                vm.safe_name(value)
        self.assertEqual(vm.safe_name('win-11-test'), 'win-11-test')

    def test_bad_image_upload_paths_rejected(self):
        for name in ('../evil.iso', 'abc.sh', 'a/../x.qcow2', 'file.iso\n', '/tmp/test.iso'):
            with self.subTest(name=name), self.assertRaises(vm.VmError):
                vm.uploaded_images_name(name)
        self.assertEqual(vm.uploaded_images_name('arch.iso'), 'arch.iso')

    def test_download_url_allowlist(self):
        self.assertTrue(vm.hostname_allowed('https://releases.ubuntu.com/24.04/test.iso'))
        self.assertFalse(vm.hostname_allowed('https://user:pass@releases.ubuntu.com/secret'))
        self.assertFalse(vm.hostname_allowed('http://releases.ubuntu.com/iso'))
        self.assertFalse(vm.hostname_allowed('https://evil.example/iso'))
        self.assertFalse(vm.hostname_allowed('https://releases.ubuntu.com.evil.example/iso'))

    def test_windows_needs_iso_and_own_image(self):
        with tempfile.TemporaryDirectory() as d:
            img=Path(d)/'original.iso';img.write_bytes(b'fake iso header')
            cfg={'name':'win-test', 'osId':'windows-11','image':'original.iso','ramMiB':4096,'cpus':2,'diskGiB':64,'desktop':'none'}
            with patch.object(vm,'IMAGES',Path(d)), patch.object(vm,'vm_exists',return_value=False), \
                patch.object(vm,'state_file',return_value=Path(d)/'none'), patch.object(vm,'ensure_ready'), \
                patch.object(vm,'host_info',return_value={'memoryMiB':16384,'cpu':8}):
                val=vm.validate_config(cfg)
                self.assertEqual(val['image_type'],'iso')
                self.assertEqual(val['name'],'win-test')
                cfg['desktop']='gnome'
                with self.assertRaises(vm.VmError):vm.validate_config(cfg)

    def test_cloud_init_password_is_hashed_and_ssh_access_configured(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(vm,'run',return_value='$6$securehash') as runner:
            cfg={'name':'test-vm','osId':'ubuntu-2404','desktop':'none','username':'alice',
                 'password':'a-long-secret-password','sshKey':''}
            vm.cloud_init(cfg,Path(folder))
            raw=(Path(folder)/'user-data').read_text()
            self.assertNotIn('a-long-secret-password',raw)
            self.assertIn('$6$securehash',raw)
            self.assertIn('ALL=(ALL) ALL',raw)
            self.assertNotIn('NOPASSWD',raw)
            self.assertTrue(raw.startswith('#cloud-config\n'))
            self.assertEqual((Path(folder)/'user-data').stat().st_mode & 0o777,0o600)
            self.assertGreaterEqual(runner.call_count,2)

    def test_gpu_rejects_non_isolated_group(self):
        with patch.object(vm,'owned',return_value={'name':'testvm'}),patch.object(vm,'require_off'), \
                patch.object(vm,'gpus',return_value=[{'pci':'0000:01:00.0','ready':False}]):
            with self.assertRaisesRegex(vm.VmError,'not safe'):
                vm.attach_gpu('testvm','0000:01:00.0','PASSTHROUGH 0000:01:00.0')

    def test_physical_disk_requires_stable_symlink_and_confirmation(self):
        with patch.object(vm,'owned',return_value={'name':'testvm'}),patch.object(vm,'require_off'):
            with self.assertRaises(vm.VmError):
                vm.physical_device('testvm',{'path':'/dev/sda','confirm':'ATTACH PHYSICAL DEVICE'})
            with self.assertRaises(vm.VmError):
                vm.physical_device('testvm',{'path':'/dev/disk/by-id/ata-abc','confirm':'wrong'})

    def test_managed_uuid_must_match_current_libvirt_domain(self):
        with tempfile.TemporaryDirectory() as d, patch.object(vm,'state_file',return_value=Path(d)/'meta.json'), \
                patch.object(vm,'vm_exists',return_value=True), patch.object(vm,'virsh',return_value='another-uuid'):
            (Path(d)/'meta.json').write_text(json.dumps({'name':'testvm','uuid':'original-uuid'}))
            with self.assertRaisesRegex(vm.VmError,'identity changed'):
                vm.owned('testvm')

    def test_only_managed_vm_deletes(self):
        with patch.object(vm,'owned',side_effect=vm.VmError('not managed',403)):
            with self.assertRaises(vm.VmError):vm.delete_vm('someoneelse','someoneelse')

class CatalogTests(unittest.TestCase):
    def test_windows_is_manual(self):
        for key in ['windows-10','windows-11']:
            self.assertEqual(vm.CATALOG_BY_ID[key]['kind'],'manual')
            self.assertTrue(vm.CATALOG_BY_ID[key]['website'].startswith('https://www.microsoft.com'))

    def test_curated_images_have_checksum_and_fixed_host(self):
        for entry in vm.CATALOG:
            if entry['kind'] in ('iso','cloud'):
                self.assertTrue(vm.hostname_allowed(entry['url']))
                self.assertTrue(vm.hostname_allowed(entry['checksum']))
                self.assertIn(entry['verify'],('sha256','sha512'))

if __name__=='__main__':unittest.main()
