import { useEffect, useRef, useState } from 'react';
import RFB from '@novnc/novnc';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { getSdk } from '@ervisio/plugin-sdk';
import { api, errText, type VM } from './api';
import { Button, toast } from './kit';

/** No public VNC port and no guest IP required. The Ervisio broker forwards an
 * admin-authorized HTTP stream over a root-owned Unix socket to local QEMU VNC.
 * Messages in the reverse direction are serialized HTTP requests.
 */
export class VmConsoleChannel {
  readonly protocol = '';
  binaryType = 'arraybuffer';
  readyState = 0;
  onmessage:((e:{data:ArrayBuffer})=>void)|null=null;
  onopen:(()=>void)|null=null;
  onerror:((error:Error)=>void)|null=null;
  onclose:((e:{wasClean:boolean})=>void)|null=null;
  private stream:{close():void}|null=null;
  private writes:Uint8Array[]=[];
  private flushing=false;
  private totalQueued=0;
  private readonly MAX_QUEUE=2*1024*1024;

  constructor(private readonly token:string) {
    this.stream=getSdk().api.httpStream('vm-control',
      {method:'GET',path:'/v1/stream',headers:{'X-VM-Token':token}}, {
        onStart:status=>{
          if(status!==200){this.fail(new Error(`Console stream: HTTP ${status}`));return;}
          this.readyState=1;this.onopen?.();
        },
        onData:chunk=>{
          if(this.readyState!==1)return;
          const copy=new Uint8Array(chunk.length);copy.set(chunk);
          this.onmessage?.({data:copy.buffer});
        },
        onEnd:()=>this.close(),
        onError:e=>this.fail(e)
      });
  }
  send(value:Uint8Array|ArrayBuffer|string):void {
    if(this.readyState!==1)return;
    const data=typeof value==='string'?new TextEncoder().encode(value):
      value instanceof Uint8Array?value.slice():new Uint8Array(value.slice(0));
    if(data.length===0)return;
    if(this.totalQueued+data.length>this.MAX_QUEUE){this.fail(new Error('Console input queue overflow'));return;}
    this.writes.push(data);this.totalQueued+=data.length;
    if(!this.flushing)void this.flush();
  }
  private async flush():Promise<void> {
    this.flushing=true;
    try {
      while(this.readyState===1&&this.writes.length) {
        let total=0;const list:Uint8Array[]=[];
        while(this.writes.length&&total+this.writes[0].length<=32*1024){
          const item=this.writes.shift()!;list.push(item);total+=item.length;
        }
        // A single large clipboard packet may be split into multiple requests.
        if(!list.length){
          const big=this.writes.shift()!;
          list.push(big.subarray(0,32*1024));total=32*1024;
          if(big.length>total)this.writes.unshift(big.subarray(total));
        }
        this.totalQueued-=total;
        const joined=new Uint8Array(total);let offset=0;
        for(const b of list){joined.set(b,offset);offset+=b.length;}
        const result=await getSdk().api.http('vm-control',{
          method:'POST',path:'/v1/input',headers:{'X-VM-Token':this.token},body:joined
        });
        if(result.status!==200)throw new Error(`Console input rejected: HTTP ${result.status}`);
      }
    }catch(e){this.fail(e instanceof Error?e:new Error(String(e)));}
    finally{this.flushing=false;}
  }
  private fail(e:Error):void{if(this.readyState>=2)return;this.onerror?.(e);this.close();}
  close():void {
    if(this.readyState===3)return;
    this.readyState=3;
    this.writes=[];this.totalQueued=0;
    this.stream?.close();this.stream=null;
    this.onclose?.({wasClean:true});
  }
}

type RfbClient = RFB & {viewOnly:boolean;scaleViewport:boolean;resizeSession:boolean;
  qualityLevel:number;compressionLevel:number;clipboardPasteFrom(s:string):void;
  sendCtrlAltDel():void;disconnect():void};

function VncViewer({token,onEnd}:{token:string;onEnd():void}){
  const target=useRef<HTMLDivElement>(null);
  const wrapper=useRef<HTMLDivElement>(null);
  const instance=useRef<RfbClient|null>(null);
  const [connected,setConnected]=useState(false);
  const [viewOnly,setViewOnly]=useState(false);
  const [clipboard,setClipboard]=useState('');
  const [resize,setResize]=useState(false);
  useEffect(()=>{
    if(!target.current)return;
    const channel=new VmConsoleChannel(token);
    const rfb=new RFB(target.current,channel as unknown as WebSocket,{shared:true}) as RfbClient;
    instance.current=rfb;
    rfb.scaleViewport=true;rfb.resizeSession=false;rfb.qualityLevel=7;rfb.compressionLevel=6;
    const conn=()=>setConnected(true);
    const disc=()=>{setConnected(false);onEnd();};
    rfb.addEventListener('connect',conn);
    rfb.addEventListener('disconnect',disc);
    return()=>{rfb.removeEventListener('disconnect',disc);rfb.disconnect();channel.close();instance.current=null;};
  },[token]);
  return <div className="vm-console-block">
    <div className="vm-console-toolbar">
      <span className={'vm-pill '+(connected?'vm-pill-ok':'')}>{connected?'Desktop remoto connesso':'Connessione QEMU VNC…'}</span>
      <label><input type="checkbox" checked={viewOnly} onChange={e=>{setViewOnly(e.target.checked);if(instance.current)instance.current.viewOnly=e.target.checked;}}/> Solo visualizza</label>
      <label><input type="checkbox" checked={resize} onChange={e=>{setResize(e.target.checked);if(instance.current)instance.current.resizeSession=e.target.checked;}}/> Ridimensionamento</label>
      <Button size="sm" variant="secondary" disabled={!connected} onClick={()=>instance.current?.sendCtrlAltDel()}>Ctrl+Alt+Canc</Button>
      <Button size="sm" variant="secondary" onClick={()=>wrapper.current?.requestFullscreen().catch(()=>toast.err('Fullscreen','Il browser lo ha bloccato'))}>Schermo intero</Button>
    </div>
    <div className="vm-console-viewport" ref={wrapper}><div className="vm-rfb" ref={target} tabIndex={0} aria-label="Console grafica QEMU interattiva"/></div>
    <div className="vm-console-toolbar vm-clipboard"><input aria-label="Testo da incollare nella VM" value={clipboard} onChange={e=>setClipboard(e.target.value)} placeholder="Testo da inviare agli appunti della VM"/>
      <Button size="sm" disabled={!connected||!clipboard} onClick={()=>instance.current?.clipboardPasteFrom(clipboard)}>Invia testo</Button>
    </div>
    <p className="vm-muted vm-small">Questa console mostra lo schermo della VM fin dall'avvio, anche durante l'installazione del sistema operativo. Il copia-incolla dipende dal supporto del guest.</p>
  </div>;
}

function SerialViewer({token,onEnd}:{token:string;onEnd():void}){
  const el=useRef<HTMLDivElement>(null);
  const terminal=useRef<Terminal|null>(null);
  const [connected,setConnected]=useState(false);
  useEffect(()=>{
    if(!el.current)return;
    const term=new Terminal({convertEol:true,cursorBlink:true,scrollback:3500,fontSize:13,
      theme:{background:'#10151c',foreground:'#e8f0f6',cursor:'#9fb4ef'}});
    const fit=new FitAddon();term.loadAddon(fit);term.open(el.current);fit.fit();terminal.current=term;
    const channel=new VmConsoleChannel(token);
    channel.onopen=()=>{setConnected(true);term.focus();term.write('\r\n[Connessione seriale attiva]\r\n');};
    channel.onmessage=e=>term.write(new Uint8Array(e.data));
    channel.onerror=e=>term.write(`\r\n[Errore: ${e.message}]\r\n`);
    channel.onclose=()=>{setConnected(false);onEnd();};
    const disposable=term.onData(data=>channel.send(data));
    const ro=new ResizeObserver(()=>{fit.fit();});ro.observe(el.current);
    return()=>{ro.disconnect();disposable.dispose();channel.close();term.dispose();terminal.current=null;};
  },[token]);
  return <div className="vm-console-block"><div className="vm-console-toolbar"><span className={'vm-pill '+(connected?'vm-pill-ok':'')}>{connected?'Seriale connessa':'Connessione console seriale…'}</span>
    <Button size="sm" onClick={()=>terminal.current?.focus()}>Focus tastiera</Button>
  </div><div className="vm-xterm" ref={el} tabIndex={0}/>
  <p className="vm-muted vm-small">La console seriale funziona quando il guest ha una ttyS0/serial getty configurata. Se non compare il login, usa la console grafica.</p></div>;
}

export function VMConsole({vm}:{vm:VM}){
  const [kind,setKind]=useState<'vnc'|'serial'>('vnc');
  const [token,setToken]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const start=async(k:'vnc'|'serial')=>{
    setToken('');setKind(k);setBusy(true);setError('');
    try{const r=await api.openConsole(vm.name,k);setToken(r.token);}
    catch(e){setError(errText(e));}
    finally{setBusy(false);}
  };
  useEffect(()=>{setToken('');setError('');},[vm.name]);
  if(!vm.state.toLowerCase().startsWith('running'))return <div className="vm-empty"><h3>Avvia la VM per aprire la console</h3><p>La console video e quella seriale sono disponibili mentre QEMU è in esecuzione.</p></div>;
  return <div className="vm-col">
    <div className="vm-row vm-wrap"><Button variant={kind==='vnc'?'primary':'secondary'} loading={busy&&kind==='vnc'} onClick={()=>void start('vnc')}>Schermo interattivo</Button>
      <Button variant={kind==='serial'?'primary':'secondary'} loading={busy&&kind==='serial'} onClick={()=>void start('serial')}>Terminale seriale</Button>
      {token&&<Button variant="ghost" onClick={()=>setToken('')}>Disconnetti</Button>}</div>
    {error&&<div className="vm-alert vm-alert-warn">{error}</div>}
    {token?(kind==='vnc'?<VncViewer token={token} onEnd={()=>setToken('')}/>:<SerialViewer token={token} onEnd={()=>setToken('')}/>):
      <div className="vm-empty"><h3>Scegli come collegarti</h3><p>Video con mouse e tastiera per Windows, GNOME e desktop Linux; console seriale per terminali Linux headless.</p></div>}
  </div>;
}
