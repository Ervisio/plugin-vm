#!/usr/bin/env python3
"""Root-only local Ervisio VM API and streaming console multiplexer.

HTTP over /run/ervisio-vm/control.sock. Never publish on TCP. All UI requests
pass Ervisio's signed-plugin capability broker (admin unlock required).
"""
from __future__ import annotations
import base64
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import pty
import re
import secrets
import select
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
from typing import Any

sys.path.insert(0,str(Path(__file__).resolve().parent))
import vm_engine as core

MAX_JSON=64*1024
SESSIONS={}
SESSIONS_LOCK=threading.Lock()
RUNNER=None

class Console:
    def __init__(self, vm, kind):
        self.vm=core.safe_name(vm)
        self.kind=kind
        self.token=secrets.token_hex(24)
        self.created=time.monotonic()
        self.last=self.created
        self.peer=None
        self.proc=None
        self.fd=None
        self.connected=False
        self.closed=False
        self.lock=threading.RLock()
        if kind=='serial':
            if not core.vm_exists(vm):
                raise core.VmError('VM not found',404)
            master,slave=pty.openpty()
            # virsh console is safe: argv never runs a shell.
            self.proc=subprocess.Popen(['virsh','-c','qemu:///system','console',vm],
                                       stdin=slave,stdout=slave,stderr=slave,close_fds=True,
                                       start_new_session=True)
            os.close(slave)
            self.fd=master
        elif kind=='vnc':
            uri=core.virsh('domdisplay',vm,'--type','vnc')
            u=urllib.parse.urlparse(uri)
            if u.scheme!='vnc' or u.hostname not in ('127.0.0.1','localhost','::1'):
                raise core.VmError('VM VNC display is not bound to loopback; refusing to connect')
            if not u.port or not 5900<=u.port<=65535:
                raise core.VmError('VM does not expose an active local VNC console')
            self.port=u.port
        else:
            raise core.VmError('Unsupported console type')

    def start(self):
        with self.lock:
            if self.connected or self.closed:
                raise core.VmError('Console token already used',409)
            self.connected=True
            if self.kind=='vnc':
                self.peer=socket.create_connection(('127.0.0.1',self.port),timeout=8)
                self.peer.settimeout(1)

    def recv(self):
        if self.kind=='vnc':
            try:
                return self.peer.recv(131072)
            except socket.timeout:
                return None
        r,_,_=select.select([self.fd],[],[],1)
        if not r:
            return None
        try:
            return os.read(self.fd,65536)
        except OSError:
            return b''

    def write(self,data):
        with self.lock:
            if not self.connected or self.closed:
                raise core.VmError('Console not connected',409)
            self.last=time.monotonic()
            if len(data)>256*1024:
                raise core.VmError('Console input too large')
            if self.kind=='vnc':
                assert self.peer
                self.peer.sendall(data)
            else:
                assert self.fd is not None
                os.write(self.fd,data)

    def close(self):
        with self.lock:
            if self.closed: return
            self.closed=True
            if self.peer:
                try:self.peer.close()
                except OSError:pass
            if self.fd is not None:
                try:os.close(self.fd)
                except OSError:pass
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:self.proc.kill()


def new_console(vm,kind):
    with SESSIONS_LOCK:
        # A fresh console token for each viewer; don't grow unbounded.
        for k,v in list(SESSIONS.items()):
            if v.closed or (not v.connected and time.monotonic()-v.created>90):
                v.close();SESSIONS.pop(k,None)
        if len(SESSIONS)>20:
            raise core.VmError('Too many open consoles',429)
        c=Console(vm,kind)
        SESSIONS[c.token]=c
    return {'token':c.token,'kind':kind,'vm':vm}


def get_console(token):
    if not isinstance(token,str) or not re.fullmatch(r'[a-f0-9]{48}',token):
        raise core.VmError('Invalid console token',403)
    with SESSIONS_LOCK:
        c=SESSIONS.get(token)
    if not c or c.closed or time.monotonic()-c.created>60*60*6:
        raise core.VmError('Console token expired',403)
    return c

class ThreadingUnixServer(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
    daemon_threads=True
    request_queue_size=64
    allow_reuse_address=True

class ApiHandler(BaseHTTPRequestHandler):
    server_version='ErvisioVM/0.1'
    protocol_version='HTTP/1.1'
    def log_message(self,fmt,*args):
        # Never log credentials, image names or console tokens.
        return

    def reply(self,status,payload):
        data=json.dumps(payload,ensure_ascii=False).encode('utf8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        self.wfile.write(data)

    def fail(self,err):
        if isinstance(err,core.VmError):
            self.reply(err.status,{'ok':False,'error':str(err)})
        elif isinstance(err,(ValueError,KeyError,TypeError)):
            self.reply(400,{'ok':False,'error':'Invalid request: '+str(err)[:400]})
        else:
            self.reply(500,{'ok':False,'error':'Operation failed: '+str(err)[:700]})

    def json_body(self):
        try:size=int(self.headers.get('Content-Length','0'))
        except ValueError:raise core.VmError('Missing Content-Length')
        if size<2 or size>MAX_JSON:
            raise core.VmError('Invalid JSON body size')
        data=json.loads(self.rfile.read(size))
        if not isinstance(data,dict):
            raise core.VmError('Expected JSON object')
        return data

    def route(self):
        return urllib.parse.urlsplit(self.path).path

    def do_GET(self):
        try:
            p=self.route()
            if p=='/v1/status':
                return self.reply(200,{'ok':True,**core.status()})
            if p=='/v1/stream':
                return self.stream_console()
            m=re.fullmatch(r'/v1/vms/([a-z][a-z0-9-]{1,47})/snapshots',p)
            if m:
                return self.reply(200,{'ok':True,'snapshots':core.vm_snapshots(m[1])})
            if p=='/v1/health':
                return self.reply(200,{'ok':True,'version':'0.1.0'})
            raise core.VmError('Not found',404)
        except Exception as err:
            self.fail(err)

    def do_DELETE(self):
        try:
            p=self.route()
            if p=='/v1/console':
                c=get_console(self.headers.get('X-VM-Token',''))
                c.close()
                with SESSIONS_LOCK:SESSIONS.pop(c.token,None)
                return self.reply(200,{'ok':True})
            m=re.fullmatch(r'/v1/images/([A-Za-z0-9_.-]{1,120})',p)
            if m:
                return self.reply(200,core.delete_image(m[1]))
            raise core.VmError('Not found',404)
        except Exception as err:self.fail(err)

    def do_POST(self):
        try:
            p=self.route()
            if p=='/v1/input':
                return self.input_console()
            if p=='/v1/images/upload':
                return self.upload()
            req=self.json_body()
            if p=='/v1/network':
                return self.reply(200,core.manage_default_network(req.get('action','')))
            if p=='/v1/images/download':
                return self.reply(202,{'ok':True,**RUNNER.start('download',{'osId':req.get('osId')})})
            if p=='/v1/vms':
                # Prevalidation so the UI gets immediate useful errors.
                core.validate_config(req)
                return self.reply(202,{'ok':True,**RUNNER.start('create',req)})
            if p=='/v1/console':
                name=core.safe_name(req.get('name'))
                if not core.vm_exists(name):
                    raise core.VmError('VM not found',404)
                return self.reply(200,{'ok':True,**new_console(name,req.get('kind','vnc'))})
            m=re.fullmatch(r'/v1/vms/([a-z][a-z0-9-]{1,47})/(power|resources|grow|disk|snapshot|delete|gpu|physical|eject)',p)
            if m:
                name,action=m.groups()
                if action=='power': result=core.power(name,req.get('action',''))
                elif action=='resources':result=core.update_resources(name,req)
                elif action=='grow':result=core.grow_disk(name,req.get('sizeGiB'))
                elif action=='eject':result=core.eject_install_media(name)
                elif action=='disk':result=core.extra_disk(name,req.get('sizeGiB'))
                elif action=='snapshot':result=core.snapshot(name,req.get('operation',''),req.get('name',''))
                elif action=='delete':result=core.delete_vm(name,req.get('confirm',''),bool(req.get('force',False)))
                elif action=='gpu':result=core.attach_gpu(name,req.get('pci',''),req.get('confirm',''))
                else:result=core.physical_device(name,req)
                return self.reply(200,result)
            raise core.VmError('Not found',404)
        except Exception as err:self.fail(err)

    def stream_console(self):
        c=get_console(self.headers.get('X-VM-Token',''))
        c.start()
        self.send_response(200)
        self.send_header('Content-Type','application/octet-stream')
        self.send_header('Cache-Control','no-store')
        self.send_header('Connection','close')
        self.end_headers()
        self.close_connection=True
        try:
            while not c.closed:
                chunk=c.recv()
                if chunk is None:continue
                if not chunk:break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (OSError,BrokenPipeError,ConnectionResetError):
            pass
        finally:
            c.close()
            with SESSIONS_LOCK:SESSIONS.pop(c.token,None)

    def input_console(self):
        c=get_console(self.headers.get('X-VM-Token',''))
        try: length=int(self.headers.get('Content-Length','0'))
        except ValueError:raise core.VmError('Invalid Content-Length')
        if length<=0 or length>256*1024:
            raise core.VmError('Invalid input length')
        c.write(self.rfile.read(length))
        self.reply(200,{'ok':True})

    def upload(self):
        query=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        name=query.get('name',[''])[0]
        try: size=int(self.headers.get('Content-Length','0'))
        except ValueError:raise core.VmError('Missing Content-Length')
        dst=core.upload_destination(name,size)
        temp=dst.parent/('.upload-'+secrets.token_hex(12)+'.part')
        # Only allow ordinary file creation in our root-owned image directory.
        bytes_read=0
        try:
            with temp.open('xb') as f:
                while bytes_read<size:
                    chunk=self.rfile.read(min(1024*1024,size-bytes_read))
                    if not chunk:raise core.VmError('Incomplete upload')
                    bytes_read+=len(chunk);f.write(chunk)
                f.flush();os.fsync(f.fileno())
            if dst.suffix.lower()!='.iso':
                info=json.loads(core.run(['qemu-img','info','--output=json',str(temp)],timeout=30))
                if info.get('format') not in ('qcow2','raw') or info.get('backing-filename'):
                    raise core.VmError('Unsafe or unsupported disk image')
            temp.chmod(0o644)
            # No overwrite if another request raced this upload.
            if dst.exists():raise core.VmError('Image already exists',409)
            temp.rename(dst)
            self.reply(201,{'ok':True,'image':dst.name,'size':size,
                             'note':'Custom upload is not cryptographically verified; trust the original ISO.'})
        finally:temp.unlink(missing_ok=True)


def service():
    global RUNNER
    if os.geteuid()!=0:raise RuntimeError('VM service must run as root')
    for d in (core.STATE,core.IMAGES,core.DATA,core.JOBS):
        d.mkdir(mode=0o711 if d in (core.DATA,core.IMAGES) else 0o700,parents=True,exist_ok=True)
        if d in (core.DATA,core.IMAGES): d.chmod(0o711)
    core.SOCKET.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
    core.SOCKET.unlink(missing_ok=True)
    RUNNER=core.TaskRunner()
    server=ThreadingUnixServer(str(core.SOCKET),ApiHandler)
    core.SOCKET.chmod(0o600)
    try:server.serve_forever(poll_interval=.5)
    finally:
        server.server_close()
        core.SOCKET.unlink(missing_ok=True)


def install():
    if sys.platform!='linux' or os.geteuid()!=0:
        raise RuntimeError('Linux and administrator rights required')
    if not Path('/run/systemd/system').exists():
        raise RuntimeError('systemd is required by this runtime')
    osinfo=core.host_info()
    if osinfo['arch'] not in ('x86_64','amd64'):
        raise RuntimeError('This release supports x86_64 hosts only')
    if not osinfo['virtualization']['canAccelerate']:
        raise RuntimeError('KVM acceleration unavailable: ' + osinfo['message'])
    print('Preparing KVM/QEMU, libvirt and console service...',flush=True)
    apt=shutil.which('apt-get')
    dnf=shutil.which('dnf')
    pacman=shutil.which('pacman')
    zypper=shutil.which('zypper')
    # Deliberately no arbitrary package names or shell. Command list is fixed.
    if apt:
        core.run(['apt-get','update'],timeout=300)
        core.run(['apt-get','install','-y','qemu-system-x86','qemu-utils','libvirt-daemon-system',
                  'libvirt-clients','virtinst','cloud-image-utils','ovmf','swtpm','openssl'],timeout=900)
    elif dnf:
        core.run(['dnf','install','-y','qemu-kvm','qemu-img','libvirt','virt-install',
                  'cloud-utils','edk2-ovmf','swtpm','openssl'],timeout=900)
    elif pacman:
        core.run(['pacman','-Sy','--needed','--noconfirm','qemu-full','libvirt','virt-install',
                  'cloud-image-utils','edk2-ovmf','swtpm','openssl'],timeout=900)
    elif zypper:
        core.run(['zypper','--non-interactive','install','qemu','libvirt','virt-install',
                  'cloud-utils','ovmf','swtpm','openssl'],timeout=900)
    else:raise RuntimeError('Package manager not supported. Install KVM/libvirt/virt-install manually.')
    if not Path('/dev/kvm').exists():
        raise RuntimeError('KVM device /dev/kvm is missing. Enable virtualization in BIOS/UEFI or nested virtualization.')
    # Supports either traditional monolithic daemon or modern modular libvirt.
    for service_name in ('libvirtd.socket','virtqemud.socket'):
        r=subprocess.run(['systemctl','enable','--now',service_name],capture_output=True,text=True)
        if r.returncode==0:break
    else:
        raise RuntimeError('No libvirt management socket could be started')
    try:
        if 'default' not in core.virsh('net-list','--all','--name'):
            candidate=Path('/usr/share/libvirt/networks/default.xml')
            if candidate.is_file(): core.virsh('net-define',str(candidate))
        if 'default' in core.virsh('net-list','--all','--name'):
            core.virsh('net-start','default',check=False)
            core.virsh('net-autostart','default',check=False)
    except Exception:pass
    unit=Path('/etc/systemd/system/ervisio-vm.service')
    unit.write_text('''[Unit]\nDescription=Ervisio Virtual Machines local control\nAfter=network.target libvirtd.socket virtqemud.socket\n\n[Service]\nType=simple\nExecStart=/usr/bin/python3 -B /var/lib/ervisio/plugins/vm/vm_service.py serve\nUser=root\nGroup=root\nRestart=on-failure\nRestartSec=2\nUMask=0077\nNoNewPrivileges=yes\nPrivateTmp=yes\nProtectHome=read-only\nProtectSystem=full\nReadWritePaths=/var/lib/ervisio-vm /var/lib/libvirt/images/ervisio-vm /run/ervisio-vm\nLimitNOFILE=8192\n\n[Install]\nWantedBy=multi-user.target\n''')
    core.run(['systemctl','daemon-reload'])
    core.run(['systemctl','enable','--now','ervisio-vm.service'],timeout=60)
    print(json.dumps({'ok':True,'message':'VM runtime installed; hardware virtualization ready.'}))

if __name__=='__main__':
    try:
        if len(sys.argv)!=2 or sys.argv[1] not in ('serve','install'):
            raise RuntimeError('Use: vm_service.py serve|install')
        {'serve':service,'install':install}[sys.argv[1]]()
    except Exception as error:
        print(json.dumps({'ok':False,'error':str(error)}))
        sys.exit(1)
