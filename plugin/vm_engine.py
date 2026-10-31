"""Ervisio VM runtime: fixed-operation libvirt orchestrator.

The web plugin talks only to a root-owned Unix socket (permission checked by
Ervisio's capability broker). There is intentionally no shell interpolation.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

STATE = Path('/var/lib/ervisio-vm')
DATA = Path('/var/lib/libvirt/images/ervisio-vm')
# Guest storage must be traversable by libvirt's unprivileged QEMU process.
# Directory listings remain root-only; libvirt handles disk file ownership.
IMAGES = DATA / '_library'
JOBS = STATE / 'jobs'
SOCKET = Path('/run/ervisio-vm/control.sock')
NAME_RE = re.compile(r'[a-z][a-z0-9-]{1,47}\Z')
SNAP_RE = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}\Z')
PCI_RE = re.compile(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]\Z')
ALLOWED_DISK_RE = re.compile(r'/dev/disk/by-(?:id|partuuid)/[a-zA-Z0-9_.:+-]{1,140}\Z')
GIB = 1024 ** 3
IMAGE_LIMIT = 14 * GIB

# The auto-download list is deliberately pinned to official HTTPS domains.
# Website-only entries must be imported as user-owned ISO files.
CATALOG = [
    dict(id='ubuntu-2404', name='Ubuntu 24.04 LTS', family='Linux', kind='cloud', format='qcow2',
         url='https://cloud-images.ubuntu.com/releases/noble/release/ubuntu-24.04-server-cloudimg-amd64.img',
         checksum='https://cloud-images.ubuntu.com/releases/noble/release/SHA256SUMS', verify='sha256',
         osVariant='ubuntu24.04', recommended=25, desktop=True, note='Cloud image, configurazione automatica con cloud-init'),
    dict(id='debian-13', name='Debian 13 Trixie', family='Linux', kind='cloud', format='qcow2',
         url='https://cloud.debian.org/images/cloud/trixie/latest/debian-13-generic-amd64.qcow2',
         checksum='https://cloud.debian.org/images/cloud/trixie/latest/SHA512SUMS', verify='sha512',
         osVariant='debian13', recommended=20, desktop=True, note='Cloud image ufficiale'),
    dict(id='arch', name='Arch Linux', family='Linux', kind='iso', format='iso',
         url='https://geo.mirror.pkgbuild.com/iso/latest/archlinux-x86_64.iso',
         checksum='https://geo.mirror.pkgbuild.com/iso/latest/sha256sums.txt', verify='sha256',
         osVariant='archlinux', recommended=35, desktop=False, note='Installer Arch; configurazione nel terminale/console'),
    dict(id='kali', name='Kali Linux 2026.3', family='Linux', kind='iso', format='iso',
         url='https://cdimage.kali.org/kali-2026.3/kali-linux-2026.3-installer-netinst-amd64.iso',
         checksum='https://cdimage.kali.org/kali-2026.3/SHA256SUMS', verify='sha256',
         osVariant='debian13', recommended=40, desktop=False, note='Installazione interattiva; netinstaller'),
    dict(id='ubuntu-desktop', name='Ubuntu Desktop 24.04 LTS', family='Linux', kind='iso', format='iso',
         url='https://releases.ubuntu.com/24.04/ubuntu-24.04.5.1-desktop-amd64.iso',
         checksum='https://releases.ubuntu.com/24.04/SHA256SUMS', verify='sha256',
         osVariant='ubuntu24.04', recommended=40, desktop=False, note='GNOME già incluso; installer grafico'),
    dict(id='fedora-44', name='Fedora 44 Workstation / Cloud', family='Linux', kind='manual',
         website='https://fedoraproject.org/workstation/download', recommended=35, desktop=False,
         note='Seleziona ISO ufficiale o immagine qcow2 e caricala'),
    dict(id='fedora-kde', name='Fedora 44 KDE Plasma', family='Linux', kind='manual',
         website='https://fedoraproject.org/kde/download', recommended=35, desktop=False,
         note='Desktop Plasma incluso nella ISO ufficiale'),
    dict(id='mint', name='Linux Mint', family='Linux', kind='manual',
         website='https://linuxmint.com/download.php', recommended=30, desktop=False,
         note='Cinnamon, MATE o Xfce; scegli la ISO'),
    dict(id='opensuse', name='openSUSE', family='Linux', kind='manual',
         website='https://get.opensuse.org/', recommended=35, desktop=False,
         note='Tumbleweed o Leap; ISO ufficiale'),
    dict(id='rocky', name='Rocky Linux', family='Linux', kind='manual',
         website='https://rockylinux.org/download', recommended=25, desktop=False, note='Installer ufficiale'),
    dict(id='alma', name='AlmaLinux', family='Linux', kind='manual',
         website='https://almalinux.org/get-almalinux/', recommended=25, desktop=False, note='Installer ufficiale'),
    dict(id='windows-11', name='Windows 11', family='Windows', kind='manual',
         website='https://www.microsoft.com/software-download/windows11', recommended=64, desktop=False,
         note='ISO Microsoft a cura dell’utente, attivazione e licenza necessarie; TPM 2.0/UEFI'),
    dict(id='windows-10', name='Windows 10', family='Windows', kind='manual',
         website='https://www.microsoft.com/software-download/windows10iso', recommended=64, desktop=False,
         note='Supporto standard terminato il 14 ottobre 2025; licenza necessaria'),
    dict(id='freebsd', name='FreeBSD', family='BSD', kind='manual',
         website='https://www.freebsd.org/where/', recommended=25, desktop=False,
         note='Installer ISO, senza cloud-init Ervisio'),
    dict(id='custom', name='Personalizzata', family='Custom', kind='manual',
         website='', recommended=25, desktop=False, note='Carica una ISO o una immagine qcow2 e installa il tuo OS'),
]
CATALOG_BY_ID = {x['id']: x for x in CATALOG}
ALLOWED_DOWNLOAD_HOSTS = {
    'cloud-images.ubuntu.com', 'cloud.debian.org', 'geo.mirror.pkgbuild.com',
    'cdimage.kali.org', 'kali.download', 'releases.ubuntu.com'
}

class VmError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def run(argv: list[str], timeout: int = 30, check: bool = True, input_data: str | None = None) -> str:
    p = subprocess.run(argv, input=input_data, text=True, capture_output=True, timeout=timeout,
                       env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
                            'DEBIAN_FRONTEND': 'noninteractive', 'HOME': '/root'})
    if check and p.returncode:
        raise VmError((p.stderr or p.stdout or ('Command failed: ' + argv[0])).strip()[-1200:], 502)
    return p.stdout.strip()


def safe_name(name: Any) -> str:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise VmError('Name: 2–48 lowercase letters/numbers/dashes, starting with a letter')
    return name


def safe_snapshot(name: Any) -> str:
    if not isinstance(name, str) or not SNAP_RE.fullmatch(name):
        raise VmError('Invalid snapshot name')
    return name


def num(value: Any, lo: int, hi: int, title: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise VmError(f'{title}: choose a value between {lo} and {hi}')
    return value


def state_file(name: str) -> Path:
    return DATA / safe_name(name) / 'vm.json'


def save_json(path: Path, obj: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.' + secrets.token_hex(5) + '.tmp')
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding='utf8')
    tmp.chmod(mode)
    tmp.replace(path)


def load_json(path: Path, fallback: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf8'))
    except (OSError, json.JSONDecodeError):
        return fallback


def owned(name: str) -> dict:
    record = load_json(state_file(name))
    if not isinstance(record, dict) or record.get('name') != name or not record.get('uuid'):
        raise VmError('VM not managed by Ervisio: deletion/storage changes unavailable', 403)
    if not vm_exists(name) or virsh('domuuid',name,check=False).strip()!=record['uuid']:
        raise VmError('VM identity changed since creation: destructive changes blocked',403)
    return record


def vm_exists(name: str) -> bool:
    if not shutil.which('virsh'):
        return False
    return subprocess.run(['virsh', '-c', 'qemu:///system', 'domuuid', name],
                          capture_output=True, timeout=10).returncode == 0


def virsh(*args: str, timeout: int = 30, check: bool = True) -> str:
    return run(['virsh', '-c', 'qemu:///system', *args], timeout=timeout, check=check)


def cpu_flags() -> set[str]:
    try:
        for line in Path('/proc/cpuinfo').read_text().splitlines():
            if line.startswith(('flags', 'Features')) and ':' in line:
                return set(line.split(':', 1)[1].split())
    except OSError:
        pass
    return set()


def virtualization_diagnostics(kvm_path: Path = Path('/dev/kvm'),
                               flags: set[str] | None = None,
                               hypervisor: str | None = None) -> dict:
    """Explain bare-metal versus guest KVM support without conflating host nesting.

    /sys/module/kvm_*/parameters/nested controls whether *our guests* may run KVM;
    it is not required for this host to start ordinary KVM guests.
    """
    if flags is None:
        flags = cpu_flags()
    if hypervisor is None:
        try:
            hypervisor = run(['systemd-detect-virt', '--vm'], check=False, timeout=3).strip()
        except (FileNotFoundError, OSError):
            hypervisor = ''
    hypervisor = hypervisor or 'none'
    running_in_vm = hypervisor not in ('none', 'unknown', '')
    cpu_extension = 'vmx' if 'vmx' in flags else ('svm' if 'svm' in flags else 'none')
    exists = kvm_path.exists()
    device_accessible = False
    device_error = ''
    if exists:
        try:
            import fcntl
            fd = os.open(str(kvm_path), os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0))
            try:
                api_version = fcntl.ioctl(fd, 0xAE00)  # KVM_GET_API_VERSION
                device_accessible = api_version == 12
                if not device_accessible:
                    device_error = f'Unsupported KVM API version: {api_version}'
            finally:
                os.close(fd)
        except (OSError, ValueError) as exc:
            device_error = str(exc)
    nested_parameter = 'unknown'
    nested_path = None
    for module in ('kvm_intel', 'kvm_amd'):
        candidate = Path('/sys/module') / module / 'parameters/nested'
        if candidate.exists():
            nested_path = str(candidate)
            try:
                text = candidate.read_text().strip().lower()
                nested_parameter = 'enabled' if text in ('1', 'y', 'yes') else 'disabled'
            except OSError:
                pass
            break
    hints = []
    if cpu_extension == 'none':
        hints.append(('CPU has no exposed VT-x/AMD-V flags: enable it in BIOS/UEFI; in a VPS ask your provider to expose nested virtualization.'
                      if running_in_vm else 'VT-x/AMD-V is not visible: check CPU support and enable virtualization in BIOS/UEFI.'))
    if not exists:
        hints.append(('No /dev/kvm inside the VPS: enable nested virtualization in the parent hypervisor or ask the hosting provider.'
                      if running_in_vm else 'No /dev/kvm: load the kvm_intel/kvm_amd kernel module and check BIOS/UEFI virtualization.'))
    elif not device_accessible:
        hints.append('KVM exists but cannot be opened: check kernel KVM support, device permissions and hypervisor restrictions. ' + device_error)
    if nested_parameter == 'disabled':
        hints.append('Nested virtualization for VMs created on this host is disabled. Normal VMs can still run; only VMs-within-VMs need this setting.')
    return {
        'hypervisor': hypervisor,
        'insideVM': running_in_vm,
        'cpuExtension': cpu_extension,
        'deviceExists': exists,
        'deviceAccessible': device_accessible,
        'nestedForGuests': nested_parameter,
        'nestedParameterPath': nested_path,
        'canAccelerate': exists and device_accessible and cpu_extension != 'none',
        'hints': hints,
    }


def host_info() -> dict:
    import platform
    os_release = {}
    try:
        for line in Path('/etc/os-release').read_text().splitlines():
            if '=' in line:
                k, v = line.split('=', 1)
                os_release[k] = v.strip('"')
    except OSError:
        pass
    cpu = os.cpu_count() or 1
    mem_kib = 0
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemTotal:'):
            try: mem_kib = int(line.split()[1])
            except (IndexError, ValueError): pass
            break
    tools = {x: bool(shutil.which(x)) for x in ('virsh', 'virt-install', 'qemu-img', 'cloud-localds', 'openssl', 'systemctl', 'swtpm')}
    architecture = platform.machine()
    virt = virtualization_diagnostics()
    supported = os.name == 'posix' and architecture in ('x86_64', 'amd64')
    libvirt_ok = False
    if tools['virsh']:
        try:
            libvirt_ok = subprocess.run(['virsh','-c','qemu:///system','uri'],capture_output=True,timeout=5).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass
    missing = [x for x in ('virsh','virt-install','qemu-img','cloud-localds','openssl') if not tools[x]]
    ready = bool(supported and virt['canAccelerate'] and not missing and libvirt_ok)
    notes = list(virt['hints'])
    if not supported:
        notes.insert(0, 'This release supports x86_64 Linux hosts. Windows hosts and ARM64 KVM are not yet supported.')
    if missing:
        notes.append('Install the VM runtime to provide: ' + ', '.join(missing))
    if tools['virsh'] and not libvirt_ok:
        notes.append('libvirt system connection unavailable. Start libvirtd.socket or virtqemud.socket.')
    free_bytes = 0
    try:free_bytes = shutil.disk_usage(DATA.parent if DATA.parent.exists() else '/var/lib').free
    except OSError:pass
    return dict(os=os_release.get('PRETTY_NAME', 'Linux'), arch=architecture, kvm=virt['deviceAccessible'],
                virtualization=virt, cpu=cpu, memoryMiB=mem_kib//1024, freeDiskBytes=free_bytes,
                tools=tools, libvirt=libvirt_ok, ready=ready, supported=supported,
                message=' '.join(notes), diagnostics=notes)


def virsh_info(name: str) -> dict:
    record = load_json(state_file(name), {})
    d = dict(name=name, managed=bool(isinstance(record,dict) and record.get('uuid')
                                     and record.get('uuid') == virsh('domuuid',name,check=False).strip()))
    try:
        xml = virsh('dumpxml', name)
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml)
        d['uuid'] = root.findtext('uuid', '')
        d['vcpus'] = int(root.findtext('vcpu', '0'))
        d['ramMiB'] = int(root.findtext('memory', '0')) // 1024
        d['disks'] = [{'target': x.find('target').get('dev', '') if x.find('target') is not None else '',
                       'source': (x.find('source').get('file') or x.find('source').get('dev') or '') if x.find('source') is not None else '',
                       'device': x.get('device', '')}
                      for x in root.findall('./devices/disk')]
        d['graphics'] = root.find('./devices/graphics[@type="vnc"]') is not None
        d['gpus'] = [a.attrib for a in root.findall('./devices/hostdev/source/address')]
    except Exception as exc:
        d['error'] = str(exc)[:100]
    _domstate = virsh('domstate', name, check=False).splitlines() if vm_exists(name) else []
    d['state'] = _domstate[0] if _domstate else 'unknown'
    d['autostart'] = 'enable' in virsh('dominfo', name, check=False).lower().split('autostart:')[-1][:32]
    if isinstance(record, dict):
        d['os'] = record.get('os', 'Imported VM')
        d['desktop'] = record.get('desktop', 'none')
        d['diskGiB'] = record.get('diskGiB')
        d['created'] = record.get('created')
        d['username'] = record.get('username')
    try:
        stat_out = virsh('domstats', name, '--cpu-total', '--balloon', '--block', '--raw', check=False)
        vals = {}
        for line in stat_out.splitlines():
            if '=' in line:
                k, v = line.strip().split('=', 1)
                vals[k] = v
        d['stats'] = {'cpuTimeNs': int(vals.get('cpu.time', '0')),
                      'balloonBytes': int(vals.get('balloon.current', '0')) * 1024,
                      'diskReadBytes': int(vals.get('block.0.rd.bytes', '0')),
                      'diskWriteBytes': int(vals.get('block.0.wr.bytes', '0'))}
    except (ValueError, OSError):
        d['stats'] = {}
    try:
        addr = virsh('domifaddr', name, '--source', 'lease', check=False)
        ips = re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}/\d+\b', addr)
        d['ip'] = ips[0].split('/')[0] if ips else None
    except Exception:
        d['ip'] = None
    return d


def list_vms() -> list[dict]:
    if not shutil.which('virsh'):
        return []
    names = [n.strip() for n in virsh('list', '--all', '--name', check=False).splitlines() if n.strip()]
    return [virsh_info(n) for n in names if NAME_RE.fullmatch(n)]


def networks() -> list[dict]:
    if not shutil.which('virsh'):
        return []
    result = []
    names = [n.strip() for n in virsh('net-list', '--all', '--name', check=False).splitlines() if n.strip()]
    for name in names:
        if re.fullmatch(r'[a-zA-Z0-9_.-]{1,40}', name):
            result.append({'name':name, 'active':'active' in virsh('net-info', name, check=False).lower().split('active:')[-1][:24]})
    return result


def list_images() -> list[dict]:
    result=[]
    if IMAGES.is_dir():
        for f in sorted(IMAGES.iterdir()):
            if f.is_file() and f.suffix.lower() in ('.qcow2', '.img', '.iso', '.raw'):
                result.append({'id':f.name, 'size':f.stat().st_size, 'path':str(f), 'type':'iso' if f.suffix=='.iso' else 'disk'})
    return result


def gpus() -> list[dict]:
    out=[]
    for dev in Path('/sys/bus/pci/devices').glob('*'):
        try:
            cls=int((dev/'class').read_text().strip(),16) >> 16
            if cls not in (0x0300,0x0302,0x0380):
                continue
            driver=(dev/'driver').resolve().name if (dev/'driver').exists() else 'none'
            group=(dev/'iommu_group').resolve() if (dev/'iommu_group').exists() else None
            members=sorted(x.name for x in (group/'devices').iterdir()) if group else []
            safe = bool(group and members and all((Path('/sys/bus/pci/devices')/m/'driver').exists() and
                (Path('/sys/bus/pci/devices')/m/'driver').resolve().name=='vfio-pci' for m in members))
            boot = (dev/'boot_vga').read_text().strip() == '1' if (dev/'boot_vga').exists() else False
            out.append({'pci':dev.name,'vendor':(dev/'vendor').read_text().strip(),
                        'device':(dev/'device').read_text().strip(), 'driver':driver,
                        'group':group.name if group else None, 'members':members,
                        'bootVga':boot, 'ready':safe and not boot})
        except (OSError, ValueError):
            continue
    return out


def disks() -> list[dict]:
    if not shutil.which('lsblk'):
        return []
    try:
        d=json.loads(run(['lsblk','-J','-b','-o','NAME,PATH,TYPE,SIZE,MOUNTPOINTS,FSTYPE,PKNAME,MODEL,RM,RO'],timeout=10))
        return d.get('blockdevices',[])
    except Exception:
        return []


def current_jobs() -> list[dict]:
    if not JOBS.is_dir():
        return []
    arr=[load_json(x,{}) for x in JOBS.glob('*.json')]
    return sorted((x for x in arr if isinstance(x,dict) and x.get('id')),key=lambda x:x.get('created',0),reverse=True)[:60]


def status() -> dict:
    return {'host':host_info(),'vms':list_vms(),'images':list_images(),'jobs':current_jobs(),
            'catalog':CATALOG,'networks':networks(),'gpus':gpus(), 'physicalDisks':disks()}


def manage_default_network(action: str) -> dict:
    """Only the libvirt built-in NAT network; no arbitrary bridge commands."""
    if action not in ('start','stop','autostart-on','autostart-off'):
        raise VmError('Unknown network action')
    if not any(n['name']=='default' for n in networks()):
        raise VmError('The libvirt default network does not exist. Install or define the distro-provided default NAT network.')
    commands={
        'start':('net-start','default'),
        'stop':('net-destroy','default'),
        'autostart-on':('net-autostart','default'),
        'autostart-off':('net-autostart','default','--disable')
    }
    virsh(*commands[action]);return {'ok':True,'action':action}


def ensure_ready() -> None:
    h=host_info()
    if not h['ready'] or not h['supported']:
        raise VmError('VM host not ready: ' + (h.get('message') or 'install the VM runtime and check hardware virtualization'))
    if not any(n['name']=='default' and n['active'] for n in networks()):
        raise VmError('The libvirt default NAT network is not active; enable it from the host before creating VMs')


def validate_config(x: dict) -> dict:
    ensure_ready()
    name=safe_name(x.get('name'))
    if vm_exists(name) or state_file(name).exists():
        raise VmError('A VM with this name already exists',409)
    ram=num(x.get('ramMiB'),512,131072,'Memory MiB')
    cpus=num(x.get('cpus'),1,64,'vCPUs')
    disk_gb=num(x.get('diskGiB'),10,2048,'Disk GiB')
    host=host_info()
    if ram > host['memoryMiB'] * 0.9:
        raise VmError('Reserve at least 10% host RAM for the hypervisor and Ervisio')
    if cpus > host['cpu'] * 2:
        raise VmError('Requested vCPU count is too high for this host')
    os_id=x.get('osId', 'custom')
    if os_id not in CATALOG_BY_ID:
        raise VmError('Unknown OS preset')
    p=CATALOG_BY_ID[os_id]
    image_name=x.get('image')
    if not isinstance(image_name,str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,120}', image_name):
        raise VmError('Select an image from the library first')
    image=IMAGES/image_name
    if not image.is_file() or image.is_symlink():
        raise VmError('Image not found in the verified local library',404)
    image_type=('iso' if image.suffix.lower()=='.iso' else
                'cloud' if os_id in ('ubuntu-2404','debian-13') else 'prebuilt')
    if image_type in ('cloud','prebuilt') and image.suffix.lower() not in ('.qcow2','.img','.raw'):
        raise VmError('Unsupported virtual disk format')
    if image_type in ('cloud','prebuilt'):
        info=json.loads(run(['qemu-img','info','--output=json',str(image)],timeout=15))
        if info.get('format') not in ('qcow2','raw') or info.get('backing-filename'):
            raise VmError('Disk image must be qcow2/raw with no external backing file')
    if p['family']=='Windows' and image_type!='iso':
        raise VmError('Windows requires an ISO installer provided by the user')
    desktop=x.get('desktop','none')
    if desktop not in ('none','gnome','kde','xfce','cinnamon'):
        raise VmError('Unsupported guest desktop profile')
    if desktop!='none' and (p.get('id') not in ('ubuntu-2404','debian-13') or image_type!='cloud'):
        raise VmError('Automatic desktop installation only supports official Ubuntu/Debian cloud images')
    if image_type=='cloud' and x.get('username') and not re.fullmatch(r'[a-z][a-z0-9_-]{1,30}',str(x['username'])):
        raise VmError('Invalid guest username')
    network=x.get('network','default')
    if network != 'default':
        raise VmError('Only the isolated libvirt default NAT network is enabled in this release')
    return {'name':name,'ramMiB':ram,'cpus':cpus,'diskGiB':disk_gb,'osId':os_id,
            'image':image_name,'image_type':image_type,'desktop':desktop,
            'username':x.get('username') or 'ervisio',
            'password':str(x.get('password') or ''),'sshKey':str(x.get('sshKey') or ''),
            'autostart':bool(x.get('autostart',False))}


def cloud_init(cfg: dict, folder: Path) -> Path:
    """Provision only official Ubuntu/Debian cloud images. Password stored as hash."""
    user=cfg['username']
    pwd=cfg['password']
    if len(pwd)<12 or len(pwd)>128:
        raise VmError('Provide a guest password of 12–128 characters (not saved in plaintext)')
    if '\n' in pwd or '\r' in pwd:
        raise VmError('Invalid password characters')
    pubkey=cfg['sshKey'].strip()
    if pubkey and (len(pubkey)>5000 or not re.fullmatch(r'(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256) [a-zA-Z0-9+/=]{40,5000}(?: [^\r\n]*)?',pubkey)):
        raise VmError('Enter a complete supported SSH public key or leave empty')
    hash_pass=run(['openssl','passwd','-6','-stdin'],input_data=pwd+'\n',timeout=10)
    # JSON is valid YAML; avoid injecting any shell strings into the cloud config.
    data={'users':['default',{'name':user,'gecos':'Ervisio VM user','groups':'sudo,adm,wheel',
                              'sudo':'ALL=(ALL) ALL','shell':'/bin/bash','lock_passwd':False,
                              'passwd':hash_pass,**({'ssh_authorized_keys':[pubkey]} if pubkey else {})}],
          'disable_root':True,'ssh_pwauth':not bool(pubkey),
          'package_update':True,
          'growpart':{'mode':'auto','devices':['/']},'resize_rootfs':True}
    profile=cfg['desktop']
    packages={
        'ubuntu-2404': {'gnome':['ubuntu-desktop-minimal'], 'kde':['kubuntu-desktop'],
                        'xfce':['xfce4','lightdm'],'cinnamon':['cinnamon-desktop-environment','lightdm']},
        'debian-13': {'gnome':['task-gnome-desktop'],'kde':['task-kde-desktop'],
                      'xfce':['task-xfce-desktop'],'cinnamon':['task-cinnamon-desktop']}}
    if profile!='none':
        data['packages']=packages[cfg['osId']][profile] + ['qemu-guest-agent']
        data['runcmd']=[['systemctl','set-default','graphical.target']]
    else:
        data['packages']=['qemu-guest-agent']
    # /dev/ttyS0 provides guest login in the serial console.
    data['write_files']=[{'path':'/etc/default/grub.d/90-ervisio-serial.cfg',
                          'content':'GRUB_CMDLINE_LINUX="$GRUB_CMDLINE_LINUX console=ttyS0,115200"\n'}]
    (folder/'user-data').write_text('#cloud-config\n'+json.dumps(data)+'\n')
    (folder/'meta-data').write_text('instance-id: ervisio-'+cfg['name']+'\nlocal-hostname: '+cfg['name']+'\n')
    os.chmod(folder/'user-data',0o600)
    seed=folder/'seed.iso'
    run(['cloud-localds',str(seed),str(folder/'user-data'),str(folder/'meta-data')],timeout=90)
    return seed


def virt_create(cfg: dict, report: Any) -> dict:
    """Create from a curated image; no untrusted arguments may become flags."""
    conf=validate_config(cfg)
    vm=conf['name']
    path=DATA/vm
    path.mkdir(parents=True,mode=0o711,exist_ok=False)
    image=IMAGES/conf['image']
    try:
        disk=path/'system.qcow2'
        if conf['image_type'] in ('cloud','prebuilt'):
            # qemu-img convert makes the VM independent of the shared template.
            report('Preparing a private, independent qcow2 disk')
            run(['qemu-img','convert','-p','-O','qcow2',str(image),str(disk)],timeout=900)
            run(['qemu-img','resize',str(disk),str(conf['diskGiB'])+'G'],timeout=120)
            seed=cloud_init(conf,path) if conf['image_type']=='cloud' else None
        else:
            report('Creating empty installation disk')
            run(['qemu-img','create','-f','qcow2',str(disk),str(conf['diskGiB'])+'G'],timeout=120)
            seed=None
        args=['virt-install','--connect','qemu:///system','--name',vm,'--memory',str(conf['ramMiB']),
              '--vcpus',str(conf['cpus']),'--machine','q35',
              '--disk',f'path={disk},format=qcow2,bus={"sata" if conf["osId"].startswith("windows") else "virtio"}',
              '--network','network=default,model='+('e1000e' if conf['osId'].startswith('windows') else 'virtio'),
              '--graphics','vnc,listen=127.0.0.1','--video',('vga' if conf['osId'].startswith('windows') else 'virtio'),
              '--serial','pty','--console','pty,target_type=serial',
              '--noautoconsole','--wait','0','--os-variant','generic']
        if conf['image_type']=='cloud':
            args+=['--import','--disk',f'path={seed},device=cdrom,readonly=on']
        elif conf['image_type']=='prebuilt':
            args+=['--import']
        else:
            args+=['--cdrom',str(image)]
        if conf['osId']=='windows-11':
            if not shutil.which('swtpm'):
                raise VmError('Windows 11 needs swtpm. Install the VM runtime first.')
            args+=['--boot','uefi','--tpm','backend.type=emulator,backend.version=2.0,model=tpm-tis']
        elif conf['osId'].startswith('windows'):
            args+=['--boot','uefi']
        report('Defining and starting the virtual machine')
        run(args,timeout=300)
        meta={'name':vm,'uuid':virsh('domuuid',vm),'os':conf['osId'],'desktop':conf['desktop'],'username':conf['username'],
              'diskGiB':conf['diskGiB'],'created':int(time.time()),'imageType':conf['image_type'],
              'source':conf['image'],'ownedDisks':['system.qcow2']}
        save_json(path/'vm.json',meta)
        if conf['autostart']:
            virsh('autostart',vm)
        report('VM defined. Use the graphical console; Linux desktop packages in a cloud guest may install after first boot.')
        return {'name':vm,'state':'created'}
    except Exception:
        if vm_exists(vm):
            try:
                virsh('destroy',vm,check=False)
                virsh('undefine',vm,'--nvram',check=False)
                if vm_exists(vm):
                    virsh('undefine',vm,check=False)
            except Exception:
                pass
        if not vm_exists(vm):
            shutil.rmtree(path,ignore_errors=True)
        raise


def power(name: str, action: str) -> dict:
    safe_name(name)
    if not vm_exists(name):
        raise VmError('VM not found',404)
    cmd={'start':['start'], 'shutdown':['shutdown'], 'force-stop':['destroy'],
         'reboot':['reboot'], 'reset':['reset'], 'suspend':['suspend'], 'resume':['resume'],
         'autostart-on':['autostart'], 'autostart-off':['autostart','--disable']}.get(action)
    if not cmd:
        raise VmError('Unsupported power action')
    virsh(*cmd,name)
    return {'ok':True,'action':action,'name':name}


def require_off(name: str) -> None:
    s=virsh('domstate',name).lower()
    if 'shut off' not in s:
        raise VmError('Shut down the VM before modifying disks or creating/restoring snapshots',409)


def update_resources(name: str, request: dict) -> dict:
    owned(name)
    require_off(name)
    h=host_info()
    ram=num(request.get('ramMiB'),512,131072,'Memory MiB')
    cpus=num(request.get('cpus'),1,64,'vCPUs')
    if ram>h['memoryMiB']*.9 or cpus>h['cpu']*2:
        raise VmError('Requested resources exceed safe host limits')
    # Changing the maximum CPU first is required by some libvirt versions.
    virsh('setmaxmem',name,str(ram*1024),'--config')
    virsh('setmem',name,str(ram*1024),'--config')
    virsh('setvcpus',name,str(cpus),'--maximum','--config')
    virsh('setvcpus',name,str(cpus),'--config')
    return {'ok':True}


def grow_disk(name: str, size: int) -> dict:
    rec=owned(name)
    require_off(name)
    size=num(size,rec.get('diskGiB',10)+1,4096,'New disk GiB')
    img=DATA/name/'system.qcow2'
    if not img.is_file() or img.is_symlink():
        raise VmError('Primary disk is missing')
    run(['qemu-img','resize',str(img),str(size)+'G'],timeout=120)
    rec['diskGiB']=size
    save_json(state_file(name),rec)
    return {'ok':True,'diskGiB':size,'note':'Expand the partition/filesystem inside the guest if needed.'}


def eject_install_media(name: str) -> dict:
    """Remove installation ISO from a managed, shut-down guest without deleting it."""
    owned(name)
    require_off(name)
    disks=virsh_info(name).get('disks',[])
    cd=next((d for d in disks if d.get('device')=='cdrom' and d.get('source')),None)
    if not cd or not re.fullmatch(r'[a-z]{2,4}[a-z]{1,2}',cd.get('target','')):
        raise VmError('No removable installation CD-ROM found',404)
    virsh('change-media',name,cd['target'],'--eject','--config')
    return {'ok':True,'note':'Installation ISO ejected. The original library file was preserved.'}


def extra_disk(name: str, size: int) -> dict:
    rec=owned(name)
    require_off(name)
    size=num(size,1,4096,'Additional disk GiB')
    disks=rec.get('ownedDisks',[])
    if len(disks)>12:
        raise VmError('Too many data disks')
    index=1
    while f'data-{index}.qcow2' in disks:
        index+=1
    filename=f'data-{index}.qcow2'
    path=DATA/name/filename
    target='vd'+chr(ord('b')+index-1)
    run(['qemu-img','create','-f','qcow2',str(path),str(size)+'G'])
    try:
        virsh('attach-disk',name,str(path),target,'--targetbus','virtio','--subdriver','qcow2','--config')
    except Exception:
        path.unlink(missing_ok=True)
        raise
    disks.append(filename)
    rec['ownedDisks']=disks
    save_json(state_file(name),rec)
    return {'ok':True,'target':target,'diskGiB':size}


def vm_snapshots(name: str) -> list[dict]:
    owned(name)
    img=DATA/name/'system.qcow2'
    result=[]
    if img.is_file():
        raw=run(['qemu-img','snapshot','-l',str(img)],timeout=15,check=False)
        for line in raw.splitlines():
            cols=line.split()
            if len(cols)>=2 and cols[0].isdigit():
                result.append({'name':cols[1], 'info':line.strip()})
    return result


def snapshot(name: str, operation: str, snap: str) -> dict:
    owned(name)
    require_off(name)
    snap=safe_snapshot(snap)
    img=DATA/name/'system.qcow2'
    if not img.is_file() or img.is_symlink():
        raise VmError('The managed primary disk is missing',404)
    if operation not in ('create','restore','delete'):
        raise VmError('Unsupported snapshot operation')
    args={'create':'-c','restore':'-a','delete':'-d'}[operation]
    run(['qemu-img','snapshot',args,snap,str(img)],timeout=120)
    return {'ok':True,'note':'Offline disk snapshot only; EFI variables and extra disks are NOT snapshotted.'}


def delete_vm(name: str, confirm: str, force: bool=False) -> dict:
    rec=owned(name)
    if confirm != name:
        raise VmError('Confirm the exact VM name')
    # Never delete unmanaged host disks, shared images or external devices.
    if vm_exists(name):
        if 'shut off' not in virsh('domstate',name).lower():
            if not force:
                raise VmError('Shut the VM down first, or explicitly request force deletion',409)
            virsh('destroy',name)
        # undefine should succeed before deleting our storage
        r=virsh('undefine',name,'--nvram','--tpm',check=False)
        if vm_exists(name):
            virsh('undefine',name)
        if vm_exists(name):
            raise VmError('Could not undefine the VM; disks were preserved')
    folder=DATA/name
    if not folder.is_symlink() and folder.is_dir() and folder.parent == DATA:
        for p in folder.iterdir():
            if p.is_symlink():
                p.unlink()
            elif p.is_file():
                p.unlink()
        folder.rmdir()
    return {'ok':True,'name':name, 'deleted':'managed VM files only'}


def physical_device(name: str, spec: dict) -> dict:
    owned(name)
    require_off(name)
    src=spec.get('path','')
    if not isinstance(src,str) or not ALLOWED_DISK_RE.fullmatch(src):
        raise VmError('Choose a stable /dev/disk/by-id or /dev/disk/by-partuuid symlink')
    if spec.get('confirm')!='ATTACH PHYSICAL DEVICE':
        raise VmError('Explicit physical device confirmation required')
    p=Path(src)
    if not p.is_symlink():
        raise VmError('Physical device must be identified through a stable system symlink')
    resolved=p.resolve(strict=True)
    if not str(resolved).startswith('/dev/') or not stat.S_ISBLK(resolved.stat().st_mode):
        raise VmError('Not a block device')
    # Refuse in-use partitions, parents, volumes, encrypted mappings and swaps.
    out=json.loads(run(['lsblk','-J','-o','PATH,MOUNTPOINTS,TYPE,PKNAME,FSTYPE,CHILDREN',str(resolved)],timeout=15))
    devices=out.get('blockdevices',[])
    def unsafe(d):
        if d.get('mountpoints') and any(m for m in d['mountpoints']): return True
        if d.get('fstype')=='swap' or d.get('type') in ('lvm','crypt','raid','rom'): return True
        return any(unsafe(x) for x in d.get('children') or [])
    if not devices or any(unsafe(d) for d in devices):
        raise VmError('Physical device is mounted, swap, encrypted or contains active child devices')
    swap=Path('/proc/swaps').read_text()
    if str(resolved) in swap:
        raise VmError('Device used as swap')
    # Refuse an entire host parent disk when its children may be live elsewhere.
    parent=run(['lsblk','-no','PKNAME',str(resolved)],check=False).strip()
    if parent and ' ' not in parent:
        parent_info=json.loads(run(['lsblk','-J','-o','PATH,MOUNTPOINTS,TYPE,PKNAME,FSTYPE,CHILDREN','/dev/'+parent]))
        # Refuse direct disk passthrough when the parent contains mounted sibling partitions.
        if any(unsafe(d) for d in parent_info.get('blockdevices',[])):
            raise VmError('Parent disk contains mounted or protected host partitions')
    # Never pass /dev/kvm or arbitrary path via user input to libvirt.
    existing={d.get('target') for d in virsh_info(name).get('disks',[])}
    target=next((f'vd{ch}' for ch in 'pqrstuvwxyz' if f'vd{ch}' not in existing),None)
    if target is None:
        raise VmError('No free virtio disk target slots available')
    argv=['attach-disk',name,src,target,'--targetbus','virtio','--config']
    if not spec.get('writable',False):
        argv+=['--mode','readonly']
    elif spec.get('writeConfirm') != 'I ACCEPT DATA LOSS':
        raise VmError('Write access requires a second explicit destructive-risk confirmation')
    virsh(*argv)
    return {'ok':True,'target':target,'warning':'Physical device was not formatted; guest writes may destroy existing data.'}


def attach_gpu(name: str, pci: str, confirm: str) -> dict:
    owned(name)
    require_off(name)
    if not isinstance(pci,str) or not PCI_RE.fullmatch(pci):
        raise VmError('Invalid PCI address')
    if confirm != f'PASSTHROUGH {pci}':
        raise VmError('Confirm the full PCI device address')
    gpu=next((x for x in gpus() if x['pci']==pci),None)
    if not gpu or not gpu['ready']:
        raise VmError('GPU not safe: enable IOMMU, bind whole IOMMU group to vfio-pci, keep host display GPU untouched')
    # libvirt hostdev devices generated only from sysfs-verified PCI addresses.
    import xml.etree.ElementTree as ET
    for address in gpu['members']:
        domain,bus,rest=address.split(':')
        slot,func=rest.split('.')
        root=ET.Element('hostdev',{'mode':'subsystem','type':'pci','managed':'yes'})
        source=ET.SubElement(root,'source')
        ET.SubElement(source,'address',{'domain':'0x'+domain,'bus':'0x'+bus,
                                         'slot':'0x'+slot,'function':'0x'+func})
        xml=ET.tostring(root,encoding='unicode')
        # Temporary per-operation XML is created in a private root-only directory.
        tmp=STATE/('pci-'+secrets.token_hex(10)+'.xml')
        tmp.write_text(xml)
        tmp.chmod(0o600)
        try:
            virsh('attach-device',name,str(tmp),'--config')
        finally:
            tmp.unlink(missing_ok=True)
    return {'ok':True,'devices':gpu['members'], 'note':'Reboot guest, install its GPU drivers and test the console.'}


def hostname_allowed(url: str) -> bool:
    u=urllib.parse.urlparse(url)
    return u.scheme == 'https' and u.hostname in ALLOWED_DOWNLOAD_HOSTS and not u.username and not u.password and (u.port in (None,443))

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not hostname_allowed(newurl):
            raise VmError('Download redirect outside the official allowlist')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def downloader(url: str):
    if not hostname_allowed(url):
        raise VmError('Unapproved image source')
    return urllib.request.build_opener(SafeRedirect).open(url,timeout=40)


def expected_digest(catalog: dict, filename: str) -> str | None:
    url=catalog.get('checksum')
    if not url: return None
    with downloader(url) as r:
        manifest=r.read(3*1024*1024).decode('utf8','replace')
    length=64 if catalog['verify']=='sha256' else 128
    for line in manifest.splitlines():
        cols=line.strip().split()
        if len(cols)>=2 and len(cols[0])==length and re.fullmatch(r'[a-fA-F0-9]+',cols[0]):
            if cols[1].lstrip('*').lstrip('./') == filename:
                return cols[0].lower()
    raise VmError('Official checksum manifest does not contain the expected image')


def download_image(os_id: str, report: Any, progress: Any) -> dict:
    image=CATALOG_BY_ID.get(os_id)
    if not image or image.get('kind') not in ('cloud','iso'):
        raise VmError('This OS requires a manually downloaded ISO')
    url=image['url']; name=os_id+('.iso' if image['kind']=='iso' else '.qcow2')
    dest=IMAGES/name
    if dest.is_file():
        return {'image':name,'alreadyDownloaded':True}
    report('Retrieving and verifying the official checksum manifest')
    expected=expected_digest(image,urllib.parse.urlparse(url).path.split('/')[-1])
    h=hashlib.new(image['verify'])
    IMAGES.mkdir(mode=0o711,parents=True,exist_ok=True)
    temp=IMAGES/(name+'.'+secrets.token_hex(8)+'.part')
    try:
        report('Downloading official image (large downloads can take time)')
        size=0
        with downloader(url) as source, open(temp,'xb') as sink:
            while True:
                chunk=source.read(1024*1024)
                if not chunk: break
                size+=len(chunk)
                if size>IMAGE_LIMIT:
                    raise VmError('Download exceeds 14 GiB safety limit')
                sink.write(chunk)
                h.update(chunk)
                progress(size)
        if h.hexdigest().lower()!=expected:
            raise VmError('Checksum mismatch: downloaded image rejected')
        report('Checksum verified: '+h.hexdigest()[:16]+'…')
        if image['kind']=='cloud':
            info=json.loads(run(['qemu-img','info','--output=json',str(temp)]))
            if info.get('format') not in ('qcow2','raw'):
                raise VmError('Unsupported cloud-image format')
            if info.get('backing-filename'):
                raise VmError('Cloud image unexpectedly references an external backing file')
        temp.chmod(0o644)
        temp.rename(dest)
        return {'image':name,'bytes':size,'checksum':h.hexdigest()}
    finally:
        temp.unlink(missing_ok=True)


@dataclass
class Task:
    id: str
    kind: str
    data: dict

class TaskRunner:
    def __init__(self):
        self.lock=threading.Lock()
        self.busy=set()
        JOBS.mkdir(mode=0o700,parents=True,exist_ok=True)
        for p in JOBS.glob('*.json'):
            item=load_json(p,{})
            if item.get('state') in ('queued','running'):
                item.update(state='interrupted',ended=time.time(),error='Runtime restarted; check VM state before retrying')
                save_json(p,item)

    def start(self, kind: str, data: dict) -> dict:
        if kind not in ('download','create'):
            raise VmError('Unknown background task')
        key=data.get('osId') if kind=='download' else safe_name(data.get('name'))
        marker=(kind,key)
        with self.lock:
            if marker in self.busy:
                raise VmError('An identical operation is already running',409)
            self.busy.add(marker)
        ident=secrets.token_hex(12)
        job={'id':ident,'kind':kind,'name':key,'state':'queued','created':time.time(),'log':[],
             'bytes':0,'error':None,'result':None}
        save_json(JOBS/(ident+'.json'),job)
        def worker():
            try:
                job['state']='running'; self.write(job)
                def report(line):
                    job['log']=(job['log']+[str(line)[:400]])[-100:]; self.write(job)
                def progress(bytes_done):
                    job['bytes']=bytes_done
                    if bytes_done // (8*1024*1024) != job.get('_last_chunk'):
                        job['_last_chunk']=bytes_done // (8*1024*1024)
                        self.write(job)
                if kind=='download':
                    result=download_image(data['osId'],report,progress)
                else:
                    result=virt_create(data,report)
                job['result']=result; job['state']='done'
            except Exception as exc:
                job['error']=str(exc)[:1000]; job['state']='failed'
            finally:
                job['ended']=time.time();job.pop('_last_chunk',None);self.write(job)
                with self.lock:
                    self.busy.discard(marker)
        threading.Thread(target=worker,daemon=True,name='vm-task-'+ident[:5]).start()
        return {'job':ident}

    def write(self, data:dict):
        save_json(JOBS/(data['id']+'.json'),data)


def uploaded_images_name(original: str) -> str:
    if not isinstance(original,str) or len(original)>130:
        raise VmError('Invalid image filename')
    clean=Path(original).name
    if clean!=original or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,120}\.(iso|qcow2|img|raw)',clean,re.I):
        raise VmError('Upload ISO or qcow2/img/raw file with a safe filename')
    return clean


def upload_destination(filename: str, size: int) -> Path:
    filename=uploaded_images_name(filename)
    num(size,1,IMAGE_LIMIT,'Upload size')
    IMAGES.mkdir(mode=0o711,parents=True,exist_ok=True)
    dest=IMAGES/filename
    if dest.exists():
        raise VmError('An image with that name already exists',409)
    return dest


def delete_image(filename: str) -> dict:
    filename=uploaded_images_name(filename)
    for vm in list_vms():
        rec=load_json(state_file(vm['name']),{})
        if rec.get('source')==filename:
            raise VmError('Image is referenced by a managed VM; remove its reference first',409)
    p=IMAGES/filename
    if not p.is_file() or p.is_symlink():
        raise VmError('Image not found',404)
    p.unlink()
    return {'ok':True}


if __name__ == '__main__':
    if len(__import__('sys').argv) == 2 and __import__('sys').argv[1] == 'host':
        print(json.dumps({'ok': True, 'host': host_info()}, ensure_ascii=False))
    else:
        raise SystemExit('usage: vm_engine.py host')
