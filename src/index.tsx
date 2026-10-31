import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { getSdk, setSdk, type PluginSDK } from '@ervisio/plugin-sdk';
import { setReact } from '@ervisio/plugin-sdk/react';
import { Button, Icon, Badge, Tabs, ConfirmDialog, Select, Checkbox, Skeleton, toast } from './kit';
import { api, fmtBytes, errText, vmRunning, vmStopped, imageForOS,
  type VMStatus, type Host, type VM, type VMConfig, type Image, type CatalogOS, type Job, type GPU, type Snapshot } from './api';
import { VMConsole } from './console';
import styles from './style.css?inline';
import terminalStyles from '@xterm/xterm/css/xterm.css?inline';

function cls(...parts:Array<string|false|null|undefined>) {return parts.filter(Boolean).join(' ');}
function compactDate(timestamp:number|undefined):string {return timestamp?new Date(timestamp*1000).toLocaleString('it-IT'):'—';}
function niceState(vm:VM) {return vmRunning(vm.state)?'In esecuzione':vmStopped(vm.state)?'Spenta':vm.state;}
function Pill({good,children}:{good?:boolean;children:ReactNode}){
  return <span className={cls('vm-pill',good?'vm-pill-ok':'')}>{children}</span>;
}
function Head({title,subtitle,actions}:{title:string;subtitle?:string;actions?:ReactNode}){
  return <div className="vm-page-head"><div><h2>{title}</h2>{subtitle&&<p>{subtitle}</p>}</div><div className="vm-actions">{actions}</div></div>;
}
function NoWindows(){return <div className="vm-root"><div className="vm-unsupported"><span className="vm-brand"><Icon name="monitor" size={32}/></span><h1>Virtual Machines non è disponibile su Windows</h1><p>Il runtime usa KVM/QEMU, libvirt, systemd e socket Unix su host Linux x86_64. Un computer Windows può accedere al pannello Ervisio e usare le VM ospitate su Linux, ma non può ospitare questo runtime nativo.</p><Pill>Host Linux necessario</Pill></div></div>;}
function MissingRuntime({host,onInstall,busy,onRefresh,error}:{host:Host|null;onInstall():void;busy:boolean;onRefresh():void;error:string}){
  return <div className="vm-col"><div className="vm-cover">
      <span className="vm-cover-icon"><Icon name="server" size={28}/></span>
      <div><p className="vm-eyebrow">PRIMO AVVIO</p><h2>Prepara il tuo hypervisor</h2>
      <p>Ervisio installerà e configurerà KVM/QEMU, libvirt, rete locale e il gateway delle console. Il plugin non espone una porta VNC pubblica.</p>
      <div className="vm-actions"><Button variant="primary" disabled={!!host&&!host.virtualization.canAccelerate} loading={busy} onClick={onInstall}>Configura hypervisor</Button>
      <Button variant="secondary" onClick={onRefresh}>Riprova connessione</Button></div></div>
    </div>
    {host&&<HostChecks host={host} compact/>}
    {error&&<div className="vm-alert vm-alert-warn"><b>Gateway non raggiungibile</b><p>{error}</p></div>}
    <p className="vm-muted vm-small">Prerequisiti: host Linux x86_64, /dev/kvm, moduli KVM, privilegi amministratore e systemd. Su molti VPS serve che il provider abiliti la virtualizzazione nidificata.</p>
  </div>;
}
function HostChecks({host,compact=false}:{host:Host;compact?:boolean}){
  const v=host.virtualization;
  const items=[
    {title:'CPU e architettura',detail:`${host.arch} · ${host.cpu} CPU logiche · ${v.cpuExtension.toUpperCase()}`,ok:host.supported&&v.cpuExtension!=='none'},
    {title:'Accelerazione KVM',detail:v.deviceExists?(v.deviceAccessible?'Accesso /dev/kvm funzionante':'/dev/kvm presente ma non accessibile'):'/dev/kvm non trovato',ok:v.canAccelerate},
    {title:'Ambiente host',detail:v.insideVM?`VPS dentro ${v.hypervisor}`:'Sistema fisico o hypervisor non rilevato',ok:true},
    {title:'VM dentro le VM',detail:v.nestedForGuests==='enabled'?'Nested virtualization abilitata':v.nestedForGuests==='disabled'?'Nested virtualization disabilitata per gli ospiti':'Stato nested non determinabile',ok:v.nestedForGuests==='enabled',info:true},
    {title:'Libvirt e strumenti',detail:host.libvirt?'Connessione qemu:///system disponibile':'Runtime da configurare',ok:host.libvirt}
  ];
  return <div className="vm-card vm-checks"><div className="vm-section-title"><h3>Diagnostica virtualizzazione</h3><Pill good={host.ready}>{host.ready?'Host pronto':'Richiede intervento'}</Pill></div>
    <div className="vm-check-grid">{(compact?items.slice(0,4):items).map(item=><div className="vm-check" key={item.title}>
      <div className={cls('vm-check-icon',item.ok?'vm-check-ok':item.info?'vm-check-neutral':'vm-check-bad')}><Icon name={item.ok?'check':'alert'} size={16}/></div>
      <div><b>{item.title}</b><span>{item.detail}</span></div></div>)}</div>
    {!!host.diagnostics.length&&<div className="vm-alert vm-alert-warn"><b>Come risolvere</b>{host.diagnostics.map((d,i)=><p key={i}>{d}</p>)}</div>}
    <p className="vm-muted vm-small">Nota: il parametro KVM “nested” controlla se le VM create qui potranno eseguire altre VM; se è disattivato non impedisce di creare una VM normale quando /dev/kvm funziona.</p>
  </div>;
}
const FAMILIES=['Tutti','Linux','Windows','BSD','Custom'];
function OsTile({item,selected,onChoose,compact=false}:{item:CatalogOS;selected:boolean;onChoose():void;compact?:boolean}){
  const badge=item.kind==='cloud'?'Automatica':item.kind==='iso'?'ISO verificata':'Importa ISO';
  return <button type="button" className={cls('vm-os-tile',selected&&'vm-os-selected')} onClick={onChoose}>
    <div className="vm-os-icon">{item.family==='Windows'?<Icon name="monitor" size={22}/>:item.family==='BSD'?<Icon name="terminal" size={22}/>:<Icon name="server" size={22}/>}</div>
    <div><b>{item.name}</b><small>{compact?badge:item.note}</small></div>
    <span className="vm-os-type">{badge}</span></button>;
}
function ConsoleStat({label,value,unit}:{label:string;value:string|number;unit?:string}){return <div className="vm-stat"><span>{label}</span><b>{value}{unit&&<small> {unit}</small>}</b></div>;}
function Dashboard({data,onOpen,onCreate}:{data:VMStatus;onOpen(n:string):void;onCreate():void}){
  const [search,setSearch]=useState('');
  const host=data.host;
  const running=data.vms.filter(v=>vmRunning(v.state)).length;
  const managed=data.vms.filter(v=>v.managed);
  const filtered=data.vms.filter(v=>!search||`${v.name} ${v.os||''} ${v.ip||''}`.toLowerCase().includes(search.toLowerCase()));
  return <div className="vm-col">
    <div className="vm-cover"><span className="vm-cover-icon"><Icon name="server" size={30}/></span>
      <div><p className="vm-eyebrow">HYPERVISOR LOCALE</p><h2>Tutte le tue macchine. Un solo pannello.</h2><p>Creale, accendile, collegati allo schermo, gestisci storage, risorse e snapshot. Nessuna console VNC pubblica.</p></div>
      <div className="vm-cover-action"><Button variant="primary" size="lg" icon="plus" onClick={onCreate}>Nuova VM</Button></div>
    </div>
    <div className="vm-stats-grid">
      <ConsoleStat label="Macchine virtuali" value={data.vms.length}/><ConsoleStat label="VM in esecuzione" value={running}/>
      <ConsoleStat label="CPU logiche host" value={host.cpu}/><ConsoleStat label="RAM host" value={(host.memoryMiB/1024).toFixed(1)} unit="GiB"/>
    </div>
    <div className="vm-card vm-host-summary"><div><b>{host.os}</b><span>{host.arch} · {host.virtualization.hypervisor==='none'?'Bare metal/nessun hypervisor rilevato':host.virtualization.hypervisor} · {fmtBytes(host.freeDiskBytes)} liberi</span></div>
      <Pill good={host.ready}>{host.ready?'KVM disponibile':'Problema KVM'}</Pill></div>
    <Head title="Le tue macchine virtuali" subtitle={`${managed.length} gestite da Ervisio · ${data.vms.length-managed.length} rilevate da libvirt`}
      actions={<input className="vm-search" value={search} onChange={e=>setSearch(e.target.value)} placeholder="Cerca VM…"/>}/>
    {filtered.length?<div className="vm-machine-grid">{filtered.map(vm=><button type="button" className="vm-machine" key={vm.name} onClick={()=>onOpen(vm.name)}>
      <div className="vm-machine-top"><div className="vm-machine-icon"><Icon name={vm.os?.startsWith('windows')?'monitor':'server'} size={24}/></div>
      <Pill good={vmRunning(vm.state)}>{niceState(vm)}</Pill></div>
      <h3>{vm.name}</h3><p>{vm.os||'VM libvirt'} · {vm.managed?'Ervisio managed':'Esterna, sola lettura storage'}</p>
      <div className="vm-machine-metrics"><span><Icon name="cpu" size={14}/> {vm.vcpus||'?'} vCPU</span><span><Icon name="server" size={14}/> {vm.ramMiB?fmtBytes(vm.ramMiB*1024*1024):'—'}</span><span><Icon name="disk" size={14}/> {vm.diskGiB?`${vm.diskGiB} GiB`:'—'}</span></div>
      <div className="vm-machine-foot"><span>{vm.ip||'Console disponibile quando avviata'}</span><span className="vm-open">Apri ›</span></div>
    </button>)}</div>:<div className="vm-empty"><h3>{search?'Nessuna corrispondenza':'Nessuna VM ancora creata'}</h3><p>Apri il wizard per preparare un sistema operativo in pochi passaggi.</p><Button variant="primary" onClick={onCreate}>Crea la prima VM</Button></div>}
  </div>;
}

function VMCreate({data,onClose,onSuccess,onImageChange}:{data:VMStatus;onClose():void;onSuccess():void;onImageChange():void}){
  const [stage,setStage]=useState(0);
  const [osId,setOS]=useState('ubuntu-2404');
  const [osSearch,setOSSearch]=useState('');
  const [osFamily,setFamily]=useState('Tutti');
  const [image,setImage]=useState('');
  const [name,setName]=useState('my-vm');
  const [ramMiB,setRAM]=useState(4096);
  const [cpus,setCPU]=useState(2);
  const [diskGiB,setDisk]=useState(40);
  const [desktop,setDesktop]=useState('none');
  const [username,setUsername]=useState('ervisio');
  const [password,setPassword]=useState('');
  const [sshKey,setSSHKey]=useState('');
  const [autostart,setAutostart]=useState(false);
  const [busy,setBusy]=useState(false);
  const [uploading,setUploading]=useState(false);
  const [uploadPct,setUploadPct]=useState(0);
  const fileInput=useRef<HTMLInputElement>(null);
  const os=data.catalog.find(p=>p.id===osId)||data.catalog[0];
  const available=data.images;
  const autoImage=imageForOS(os,available);
  const imageChoice=(image&&available.some(i=>i.id===image))?image:autoImage;
  const isCloud=os.kind==='cloud'&&available.find(i=>i.id===imageChoice)?.type==='disk';
  const options=data.catalog.filter(x=> (osFamily==='Tutti'||x.family===osFamily)&&`${x.name} ${x.note}`.toLowerCase().includes(osSearch.toLowerCase()));
  const hasAuto=os.kind==='cloud'||os.kind==='iso';
  const isDownloading=data.jobs.some(j=>j.kind==='download'&&j.name===os.id&&['queued','running'].includes(j.state));
  const selectOS=(id:string)=>{
    const next=data.catalog.find(o=>o.id===id);if(!next)return;
    setOS(id);setImage('');setDesktop('none');setDisk(Math.max(25,next.recommended));
    setName(id.replace(/[^a-z0-9-]/g,'-').replace(/^-*/,''));
  };
  const download=async()=>{
    setBusy(true);
    try{await api.download(os.id);toast.ok('Download verificato avviato',os.name);onImageChange();}
    catch(e){toast.err('Download non avviato',errText(e));}finally{setBusy(false);}
  };
  const upload=async(file:File)=>{
    setUploading(true);setUploadPct(0);
    try{
      const reply=await api.uploadImage(file,(loaded,total)=>setUploadPct(Math.round(100*loaded/Math.max(total,1))));
      if(reply.status<200||reply.status>=300)throw new Error(reply.body||`HTTP ${reply.status}`);
      setImage(file.name);toast.ok('Immagine importata',file.name);await onImageChange();
    }catch(e){toast.err('Importazione fallita',errText(e));}
    finally{setUploading(false);}
  };
  const canContinue=stage===0||stage===1&&!!imageChoice||stage===2&&(/^[a-z][a-z0-9-]{1,47}$/.test(name)&&ramMiB>=512&&cpus>=1&&diskGiB>=10&&(!isCloud||password.length>=12));
  const submit=async()=>{
    if(!imageChoice)return;
    setBusy(true);
    try{
      const config:VMConfig={name,osId,image:imageChoice,ramMiB,cpus,diskGiB,desktop:isCloud?desktop:'none',username,password:isCloud?password:'',sshKey:isCloud?sshKey:'',network:'default',autostart};
      await api.create(config);toast.ok('Creazione avviata',`La VM ${name} apparirà dopo il completamento del job.`);onSuccess();
    }catch(e){toast.err('Creazione fallita',errText(e));}finally{setBusy(false);}
  };
  return <div className="vm-col">
    <Head title="Crea una macchina virtuale" subtitle="Dalla scelta del sistema operativo alla prima accensione, senza comandi manuali"
      actions={<Button variant="ghost" onClick={onClose}>Chiudi wizard</Button>}/>
    <div className="vm-steps">{['Sistema operativo','Immagine','Hardware','Riepilogo'].map((t,i)=><button key={t} className={cls('vm-step',i===stage&&'vm-step-active',i<stage&&'vm-step-done')} disabled={i>stage}
      onClick={()=>setStage(i)}><span>{i+1}</span>{t}</button>)}</div>
    {stage===0&&<div className="vm-col"><Head title="Quale sistema vuoi installare?" subtitle="Le immagini cloud sono configurabili automaticamente; le ISO usano l'installer grafico"/>
      <div className="vm-filter"><input value={osSearch} onChange={e=>setOSSearch(e.target.value)} placeholder="Cerca Ubuntu, Debian, Arch, Windows…"/>
      <div className="vm-chips">{FAMILIES.map(f=><button type="button" className={cls('vm-chip',f===osFamily&&'vm-chip-active')} key={f} onClick={()=>setFamily(f)}>{f}</button>)}</div></div>
      <div className="vm-os-grid">{options.map(item=><OsTile key={item.id} item={item} selected={item.id===osId} onChoose={()=>selectOS(item.id)}/>)}</div>
      {os.family==='Windows'&&<div className="vm-alert vm-alert-warn">Windows richiede una ISO originale e una licenza valida. Windows 10 ha terminato il supporto standard il 14 ottobre 2025. Windows 11 richiede UEFI e TPM 2.0 virtuale.</div>}
    </div>}
    {stage===1&&<div className="vm-col"><Head title="Prepara il disco di installazione" subtitle={`${os.name} · ${os.note}`}/>
      {hasAuto&&<div className="vm-card vm-image-hero"><div><h3>Scarica dai server ufficiali</h3><p>L'immagine viene scaricata sul server, verificata con checksum e salvata nella libreria. Poi puoi riutilizzarla per altre VM.</p>
          <p className="vm-muted vm-small">{os.url}</p></div>
        {autoImage?<Pill good>Già nella libreria</Pill>:<Button variant="primary" disabled={isDownloading} loading={busy} onClick={()=>void download()}>{isDownloading?'Download in corso…':'Scarica e verifica'}</Button>}</div>}
      {os.website&&<div className="vm-card vm-image-hero"><div><h3>Fonte ufficiale del sistema</h3><p>Scarica la ISO dal sito ufficiale, poi importala qui. Il plugin non scarica automaticamente immagini Microsoft né sostituisce la loro licenza.</p></div>
        <Button variant="secondary" onClick={()=>getSdk().openExternal(os.website!)}>Apri sito ufficiale</Button></div>}
      <div className="vm-card"><h3>Usa un'immagine già presente</h3>
        {available.length?<Select label="Libreria ISO / qcow2" value={imageChoice} onChange={setImage}
          options={[{value:'',label:'Scegli immagine…'},...available.map(x=>({value:x.id,label:`${x.id} · ${fmtBytes(x.size)}`}))]}/>:<p className="vm-muted">La libreria è vuota. Scarica un'immagine del catalogo oppure caricane una.</p>}
        <div className="vm-row vm-wrap"><input ref={fileInput} type="file" accept=".iso,.qcow2,.img,.raw" className="vm-hide" onChange={e=>{const f=e.target.files?.[0];if(f)void upload(f);e.target.value='';}}/>
          <Button variant="secondary" loading={uploading} onClick={()=>fileInput.current?.click()}>Importa ISO / qcow2</Button>
          {uploading&&<span className="vm-muted">Caricamento {uploadPct}%</span>}</div>
        <p className="vm-muted vm-small">Per immagini custom, importa soltanto ISO affidabili. Dischi qcow2/raw vengono copiati in uno storage privato della nuova VM. Arch e Kali richiedono l'installazione tramite console.</p>
      </div>
    </div>}
    {stage===2&&<div className="vm-col"><Head title="Assegna le risorse" subtitle="Imposta CPU, RAM, storage, accesso e avvio automatico"/>
      <div className="vm-grid-2">
        <div className="vm-card vm-form"><label>Nome VM (lettere minuscole, cifre e trattini)<input value={name} onChange={e=>setName(e.target.value)}/></label>
          <label>CPU virtuali<input type="number" min={1} max={64} value={cpus} onChange={e=>setCPU(Number(e.target.value))}/></label>
          <label>RAM in MiB<input type="number" min={512} max={131072} step={512} value={ramMiB} onChange={e=>setRAM(Number(e.target.value))}/></label>
          <label>Disco virtuale principale (GiB)<input type="number" min={10} max={2048} value={diskGiB} onChange={e=>setDisk(Number(e.target.value))}/></label>
          <label className="vm-inline-check"><Checkbox checked={autostart} onChange={setAutostart} label="Avvia automaticamente all'avvio dell'host"/></label>
        </div>
        <div className="vm-card vm-form"><h3>Installazione e accesso</h3>
          <p className="vm-muted">Rete: NAT libvirt isolata. Il sistema guest non viene pubblicato direttamente su Internet.</p>
          {isCloud?<><Select label="Desktop da installare nel guest (facoltativo)" value={desktop} onChange={setDesktop} options={[
            {value:'none',label:'Solo server / terminale'},{value:'gnome',label:'GNOME'},{value:'kde',label:'KDE Plasma'},
            {value:'xfce',label:'Xfce (leggero)'},{value:'cinnamon',label:'Cinnamon'}]}/>
            <label>Utente Linux<input value={username} onChange={e=>setUsername(e.target.value)}/></label>
            <label>Password Linux guest (min. 12 caratteri)<input type="password" value={password} onChange={e=>setPassword(e.target.value)} autoComplete="new-password"/></label>
            <label>Chiave pubblica SSH (facoltativa)<textarea rows={3} value={sshKey} onChange={e=>setSSHKey(e.target.value)} placeholder="ssh-ed25519 AAAA…"/></label>
            <p className="vm-muted vm-small">I dati cloud-init vengono preparati nel server e la password salvata come hash. I pacchetti desktop possono richiedere parecchio tempo dopo il primo avvio.</p>
          </>:<div className="vm-alert vm-alert-warn">Questa immagine usa un installer interattivo. Completerai lingua, disco, utente e password dallo schermo della VM. Nessuna password viene preconfigurata.</div>}
          <div className="vm-alert">GPU PCI passthrough e partizioni fisiche sono configurabili dopo la creazione in **Hardware avanzato**, solo se il dispositivo è idoneo e la VM è spenta.</div>
        </div>
      </div>
    </div>}
    {stage===3&&<div className="vm-col"><Head title="Tutto pronto?" subtitle="Controlla le scelte prima di iniziare"/>
      <div className="vm-card vm-summary"><div><span>Sistema</span><b>{os.name}</b></div><div><span>Nome</span><b>{name}</b></div>
        <div><span>Immagine</span><b>{imageChoice}</b></div><div><span>CPU</span><b>{cpus} vCPU</b></div>
        <div><span>RAM</span><b>{fmtBytes(ramMiB*1024*1024)}</b></div><div><span>Storage</span><b>{diskGiB} GiB qcow2</b></div>
        <div><span>Network</span><b>default (NAT)</b></div><div><span>Desktop automatico</span><b>{isCloud?desktop:'Installazione interattiva'}</b></div>
        <div><span>Autostart</span><b>{autostart?'Sì':'No'}</b></div></div>
      <div className="vm-alert">La creazione parte come job persistente, non come comando legato alla pagina. Seguirai log e stato in Attività. Evita di creare una VM se un'altra macchina omonima esiste già.</div>
    </div>}
    <footer className="vm-wizard-footer"><Button variant="ghost" onClick={onClose}>Annulla</Button>
      <div className="vm-actions">{stage>0&&<Button variant="secondary" onClick={()=>setStage(s=>s-1)}>Indietro</Button>}
      {stage<3?<Button variant="primary" disabled={!canContinue} onClick={()=>setStage(s=>s+1)}>Continua</Button>:
        <Button variant="primary" loading={busy} onClick={()=>void submit()}>Crea VM</Button>}</div></footer>
  </div>;
}

function VMDetail({vm,data,onBack,onRefresh}:{vm:VM;data:VMStatus;onBack():void;onRefresh():void}){
  const [tab,setTab]=useState('overview');
  const [busy,setBusy]=useState('');
  const [pending,setPending]=useState<{title:string;description:string;confirm:string;danger?:boolean;doIt():Promise<unknown>}|null>(null);
  const [memory,setMemory]=useState(vm.ramMiB||2048);
  const [cpus,setCpus]=useState(vm.vcpus||2);
  const [grow,setGrow]=useState((vm.diskGiB||30)+10);
  const [newDisk,setNewDisk]=useState(20);
  const [snapshots,setSnapshots]=useState<Snapshot[]>([]);
  const [snapshotName,setSnapshotName]=useState('snapshot-1');
  const [gpu,setGpu]=useState('');
  const [physical,setPhysical]=useState('');
  const [writable,setWritable]=useState(false);
  const [forceDelete,setForceDelete]=useState(false);
  const lastSample=useRef<{ns:number;time:number}|null>(null);
  const [cpuPct,setCpuPct]=useState<number|null>(null);
  useEffect(()=>{setMemory(vm.ramMiB||2048);setCpus(vm.vcpus||2);},[vm.name]);
  useEffect(()=>{
    const now=performance.now();const ns=vm.stats?.cpuTimeNs??0;
    const last=lastSample.current;
    if(last&&ns>=last.ns&&now>last.time&&vmRunning(vm.state)){
      const pct=100*(ns-last.ns)/((now-last.time)*1e6*Math.max(1,vm.vcpus||1));
      setCpuPct(Math.min(100,Math.max(0,pct)));
    }
    lastSample.current={ns,time:now};
  },[vm.stats?.cpuTimeNs,vm.state,vm.vcpus]);
  const reloadSnapshots=useCallback(async()=>{
    if(!vm.managed)return;
    try{const r=await api.snapshots(vm.name);setSnapshots(r.snapshots);}catch(e){toast.err('Snapshot',errText(e));}
  },[vm.name,vm.managed]);
  useEffect(()=>{if(tab==='snapshots')void reloadSnapshots();},[tab,reloadSnapshots]);
  const perform=async(id:string,fn:()=>Promise<unknown>,message:string)=>{
    setBusy(id);
    try{await fn();toast.ok(message,vm.name);onRefresh();if(tab==='snapshots')await reloadSnapshots();}
    catch(e){toast.err('Operazione non riuscita',errText(e));}
    finally{setBusy('');}
  };
  const confirm=(title:string,description:string,confirm:string,doIt:()=>Promise<unknown>,danger=true)=>setPending({title,description,confirm,doIt,danger});
  const runConfirm=()=>{
    if(!pending)return;
    const x=pending;setPending(null);
    void perform('confirm',x.doIt,x.title);
  };
  const doPower=(action:string)=>{
    const dangerous=['force-stop','reset','reboot'].includes(action);
    if(dangerous)confirm(`Conferma ${action}`,`Il sistema guest potrebbe perdere dati non salvati. Azione: ${action}`,vm.name,()=>api.power(vm.name,action));
    else void perform(action,()=>api.power(vm.name,action),`Comando ${action} inviato`);
  };
  const off=vmStopped(vm.state);const running=vmRunning(vm.state);
  const availableGpu=data.gpus.filter(g=>g.ready);
  const currentGpu=data.gpus.find(g=>g.pci===gpu);
  return <div className="vm-col">
    <div className="vm-detail-head"><Button variant="ghost" onClick={onBack}>← Tutte le macchine</Button></div>
    <div className="vm-cover vm-detail-cover"><span className="vm-cover-icon"><Icon name={vm.os?.startsWith('windows')?'monitor':'server'} size={30}/></span>
      <div><p className="vm-eyebrow">{vm.managed?'GESTITA DA ERVISIO':'IMPORTATA DA LIBVIRT'}</p><h2>{vm.name}</h2>
        <p>{vm.os||'Sistema non rilevato'} · {vm.uuid||''}</p></div>
      <div className="vm-cover-action"><Pill good={running}>{niceState(vm)}</Pill></div></div>
    <div className="vm-actions vm-wrap vm-powers">
      <Button variant="primary" disabled={running||!!busy} loading={busy==='start'} onClick={()=>doPower('start')}><Icon name="play" size={16}/> Avvia</Button>
      <Button variant="secondary" disabled={!running||!!busy} onClick={()=>doPower('shutdown')}>Arresta normalmente</Button>
      <Button variant="secondary" disabled={!running||!!busy} onClick={()=>doPower('reboot')}>Riavvia</Button>
      <Button variant="secondary" disabled={!running||!!busy} onClick={()=>doPower('suspend')}>Sospendi</Button>
      <Button variant="secondary" disabled={!vm.state.toLowerCase().includes('paused')||!!busy} onClick={()=>doPower('resume')}>Riprendi</Button>
      <Button variant="danger" disabled={!running||!!busy} onClick={()=>doPower('force-stop')}>Forza stop</Button>
    </div>
    <Tabs value={tab} onChange={setTab} items={[{id:'overview',label:'Panoramica',icon:'server'},
      {id:'console',label:'Schermo e terminale',icon:'monitor'},
      {id:'storage',label:'CPU / RAM / dischi',icon:'disk'},
      {id:'snapshots',label:'Snapshot',icon:'clock'},
      {id:'hardware',label:'GPU e partizioni',icon:'cpu'}]}/>
    {tab==='overview'&&<div className="vm-col"><div className="vm-stats-grid">
      <ConsoleStat label="CPU utilizzata" value={cpuPct===null?'—':cpuPct.toFixed(1)+'%'}/>
      <ConsoleStat label="vCPU" value={vm.vcpus||'—'}/>
      <ConsoleStat label="RAM configurata" value={fmtBytes((vm.ramMiB||0)*1024*1024)}/>
      <ConsoleStat label="Disco principale" value={vm.diskGiB?`${vm.diskGiB} GiB`:'—'}/></div>
      <div className="vm-grid-2"><div className="vm-card"><h3>Dati della macchina</h3>
        <div className="vm-kv"><span>Indirizzo IP</span><b>{vm.ip||'Non rilevato (agent guest / DHCP)'}</b></div>
        <div className="vm-kv"><span>Sistema</span><b>{vm.os||'Esterno'}</b></div>
        <div className="vm-kv"><span>Ambiente grafico</span><b>{vm.desktop||'Manuale'}</b></div>
        <div className="vm-kv"><span>Creata</span><b>{compactDate(vm.created)}</b></div>
        <div className="vm-kv"><span>Avvio automatico</span><b>{vm.autostart?'Attivo':'Disattivo'}</b></div>
        <div className="vm-actions"><Button variant="secondary" disabled={!!busy} onClick={()=>void perform('autostart',()=>api.power(vm.name,vm.autostart?'autostart-off':'autostart-on'),'Avvio automatico aggiornato')}>{vm.autostart?'Disabilita autostart':'Abilita autostart'}</Button></div>
      </div><div className="vm-card"><h3>Traffico disco & stato</h3>
        <div className="vm-kv"><span>Bytes letti</span><b>{fmtBytes(vm.stats?.diskReadBytes)}</b></div>
        <div className="vm-kv"><span>Bytes scritti</span><b>{fmtBytes(vm.stats?.diskWriteBytes)}</b></div>
        <div className="vm-kv"><span>Memoria assegnata (balloon)</span><b>{fmtBytes(vm.stats?.balloonBytes)}</b></div>
        <div className="vm-kv"><span>Console video</span><b>{vm.graphics?'QEMU VNC locale':'Non disponibile'}</b></div>
        <p className="vm-muted vm-small">Uso CPU calcolato dalle variazioni di cpu.time. Il valore balloon non equivale alla RAM effettivamente utilizzata all'interno del sistema guest.</p>
      </div></div>
      <div className="vm-card vm-card-danger"><h3>Zona pericolosa</h3><p>Elimina definitivamente la definizione della VM e soltanto i dischi che Ervisio ha creato. Dischi fisici o esterni non vengono eliminati.</p>
        <label className="vm-inline-check"><Checkbox checked={forceDelete} onChange={setForceDelete} label="Forza l'arresto prima di eliminare se necessario"/></label>
        <Button variant="danger" disabled={!vm.managed||!!busy} onClick={()=>confirm('Elimina macchina virtuale',`Eliminazione di ${vm.name} e dei suoi dischi gestiti. Operazione irreversibile.`,vm.name,()=>api.deleteVM(vm.name,forceDelete))}>Elimina VM definitivamente</Button>
        {!vm.managed&&<p className="vm-muted vm-small">VM esterna: protezione attiva, non può essere eliminata da questo pannello.</p>}</div>
    </div>}
    {tab==='console'&&<VMConsole vm={vm}/>}
    {tab==='storage'&&<div className="vm-grid-2"><div className="vm-card vm-form"><h3>CPU e memoria</h3>
      <label>vCPU<input type="number" min={1} max={64} value={cpus} onChange={e=>setCpus(Number(e.target.value))}/></label>
      <label>RAM (MiB)<input type="number" min={512} step={512} value={memory} onChange={e=>setMemory(Number(e.target.value))}/></label>
      <p className="vm-muted vm-small">Le modifiche richiedono una VM spenta. Mantieni risorse libere per il sistema host.</p>
      <Button variant="primary" disabled={!off||!vm.managed||!!busy} onClick={()=>void perform('resources',()=>api.resources(vm.name,memory,cpus),'Risorse aggiornate')}>Applica risorse</Button>
      </div><div className="vm-card vm-form"><h3>Storage</h3>
      <label>Nuova dimensione disco principale (GiB)<input type="number" min={(vm.diskGiB||10)+1} value={grow} onChange={e=>setGrow(Number(e.target.value))}/></label>
      <Button variant="secondary" disabled={!off||!vm.managed||!!busy} onClick={()=>confirm('Espandi il disco',`Espandi il disco a ${grow} GiB. Il filesystem interno potrebbe richiedere un'espansione separata.`,vm.name,()=>api.grow(vm.name,grow),false)}>Espandi disco</Button>
      <label>Nuovo disco separato (GiB)<input type="number" min={1} value={newDisk} onChange={e=>setNewDisk(Number(e.target.value))}/></label>
      <Button variant="secondary" disabled={!off||!vm.managed||!!busy} onClick={()=>void perform('disk',()=>api.addDisk(vm.name,newDisk),'Disco aggiunto')}>Aggiungi disco qcow2</Button>
      <h4>Dischi collegati</h4>{(vm.disks||[]).map(d=><div className="vm-kv" key={d.target}><span>{d.target} · {d.device}</span><code title={d.source}>{d.source.split('/').slice(-2).join('/')}</code>{d.device==='cdrom'&&d.source&&<Button size="sm" variant="secondary" disabled={!off||!vm.managed||!!busy} onClick={()=>confirm('Espelli ISO',`Scollega il supporto installer ${d.source} senza cancellare il file originale.`,vm.name,()=>api.eject(vm.name),false)}>Espelli ISO</Button>}</div>)}
      </div></div>}
    {tab==='snapshots'&&<div className="vm-col"><div className="vm-alert vm-alert-warn"><b>Snapshot offline del disco principale</b><p>La macchina deve essere spenta. Non include RAM, dispositivi, variabili EFI o altri dischi collegati: non è un backup dell'intera VM.</p></div>
      <div className="vm-card vm-form"><h3>Crea un punto di ripristino</h3><div className="vm-row vm-wrap"><input aria-label="Nome snapshot" value={snapshotName} onChange={e=>setSnapshotName(e.target.value)} placeholder="prima-del-test"/>
        <Button variant="primary" disabled={!off||!vm.managed||!!busy} onClick={()=>void perform('snapshot',()=>api.snapshot(vm.name,'create',snapshotName),'Snapshot creato')}>Crea snapshot</Button>
        <Button variant="ghost" onClick={()=>void reloadSnapshots()}>Aggiorna</Button></div></div>
      {snapshots.length? snapshots.map(s=><div className="vm-card vm-snapshot" key={s.name}><div><b>{s.name}</b><p className="vm-muted vm-small">{s.info}</p></div><div className="vm-actions">
        <Button variant="secondary" disabled={!off||!vm.managed||!!busy} onClick={()=>confirm('Ripristina snapshot',`Perdi le modifiche successive al punto '${s.name}'.`,s.name,()=>api.snapshot(vm.name,'restore',s.name))}>Ripristina</Button>
        <Button variant="danger" disabled={!off||!vm.managed||!!busy} onClick={()=>confirm('Elimina snapshot',`Elimina il punto '${s.name}' senza modificare la VM corrente.`,s.name,()=>api.snapshot(vm.name,'delete',s.name))}>Elimina</Button></div></div>):<div className="vm-empty"><h3>Nessuno snapshot</h3><p>Crea un punto di ripristino offline dopo aver arrestato la VM.</p></div>}
    </div>}
    {tab==='hardware'&&<div className="vm-col"><div className="vm-grid-2">
      <div className="vm-card vm-form"><h3>GPU PCI passthrough</h3><p>Solo GPU con IOMMU attivo, GPU host non primaria e intero gruppo già associato a vfio-pci.</p>
        {availableGpu.length?<Select label="GPU disponibile" value={gpu} onChange={setGpu} options={[{value:'',label:'Scegli GPU…'},...availableGpu.map(g=>({value:g.pci,label:`${g.pci} · ${g.vendor}/${g.device} (${g.group})`}))]}/>:
          <div className="vm-alert vm-alert-warn">Nessuna GPU è pronta per un passthrough sicuro. Controlla IOMMU, vfio-pci e i gruppi PCI nella pagina Hardware host.</div>}
        {currentGpu&&<p className="vm-muted vm-small">Gruppo IOMMU completo: {currentGpu.members.join(', ')}</p>}
        <Button variant="primary" disabled={!off||!vm.managed||!gpu||!!busy} onClick={()=>confirm('Assegna GPU',`Collega alla VM l'intero gruppo IOMMU della GPU ${gpu}. Il dispositivo deve rimanere isolato dal sistema host.`, `PASSTHROUGH ${gpu}`,()=>api.gpu(vm.name,gpu))}>Collega GPU alla VM</Button>
      </div><div className="vm-card vm-form"><h3>Partizione o disco fisico</h3>
        <p>Collega un dispositivo tramite percorso stabile <code>/dev/disk/by-id/…</code> o <code>/dev/disk/by-partuuid/…</code>. Il runtime rifiuta volumi montati, swap, volumi attivi e dispositivi potenzialmente usati dall'host.</p>
        <label>Percorso fisico<input value={physical} onChange={e=>setPhysical(e.target.value)} placeholder="/dev/disk/by-id/…"/></label>
        <label className="vm-inline-check"><Checkbox checked={writable} onChange={setWritable} label="Accesso in scrittura (rischio perdita dati)"/></label>
        <Button variant="danger" disabled={!off||!vm.managed||!physical||!!busy} onClick={()=>confirm('Collega partizione fisica',writable?'Operazione ad alto rischio: una VM può sovrascrivere dati del dispositivo.':'Il dispositivo verrà collegato in sola lettura. Assicurati di aver scelto quello corretto.',writable?'I ACCEPT DATA LOSS':'ATTACH PHYSICAL DEVICE',()=>api.physical(vm.name,physical,writable))}>Collega dispositivo {writable?'(scrittura)':'(sola lettura)'}</Button>
      </div></div>
      <div className="vm-alert">Le GPU e i dischi fisici non vengono automaticamente formattati, partizionati o rimossi da Ervisio. È necessario arrestare la macchina prima di modificarli.</div>
    </div>}
    <ConfirmDialog open={!!pending} onClose={()=>setPending(null)} onConfirm={runConfirm}
      title={pending?.title||'Conferma'} description={pending?.description||''} confirmLabel="Conferma operazione" confirmText={pending?.confirm}
      danger={pending?.danger} icon="alert"/>
  </div>;
}

function Images({data,onRefresh}:{data:VMStatus;onRefresh():void}){
  const file=useRef<HTMLInputElement>(null);
  const [uploading,setUploading]=useState(false);
  const [pct,setPct]=useState(0);
  const [pending,setPending]=useState<Image|null>(null);
  const [busy,setBusy]=useState(false);
  const upload=async(f:File)=>{
    setUploading(true);setPct(0);
    try{const r=await api.uploadImage(f,(loaded,total)=>setPct(Math.round(100*loaded/Math.max(1,total))));
      if(r.status<200||r.status>=300)throw new Error(r.body||'Upload failed');toast.ok('Importazione completata',f.name);onRefresh();}
    catch(e){toast.err('Importazione fallita',errText(e));}finally{setUploading(false);}
  };
  const remove=async()=>{
    if(!pending)return;setBusy(true);
    try{await api.deleteImage(pending.id);toast.ok('Immagine eliminata',pending.id);onRefresh();}
    catch(e){toast.err('Eliminazione immagine fallita',errText(e));}finally{setBusy(false);setPending(null);}
  };
  return <div className="vm-col"><Head title="Libreria delle immagini" subtitle="ISO, immagini cloud e dischi personalizzati, riutilizzabili per più VM" actions={<>
    <input ref={file} type="file" className="vm-hide" accept=".iso,.qcow2,.raw,.img" onChange={e=>{const f=e.target.files?.[0];if(f)void upload(f);e.target.value='';}}/>
    <Button variant="primary" loading={uploading} onClick={()=>file.current?.click()}>Importa immagine</Button></>}/>
    {uploading&&<div className="vm-card"><b>Caricamento in corso: {pct}%</b><div className="vm-progress"><div style={{width:`${pct}%`}}/></div></div>}
    {data.images.length?<div className="vm-col">{data.images.map(im=><div className="vm-card vm-listitem" key={im.id}><span className="vm-fileicon"><Icon name="disk" size={20}/></span>
      <div className="vm-grow"><b>{im.id}</b><p>{im.type==='iso'?'Installazione ISO':'Disco virtuale'} · {fmtBytes(im.size)}</p></div>
      <Button variant="danger" disabled={busy} onClick={()=>setPending(im)}>Elimina</Button></div>)}</div>:
      <div className="vm-empty"><h3>La libreria è vuota</h3><p>Apri il wizard per scaricare Ubuntu, Debian, Arch o Kali verificati; oppure importa una ISO o qcow2.</p></div>}
    <div className="vm-alert">Le ISO caricate dall'utente non vengono verificate con una firma del distributore. Esegui solo immagini di cui conosci l'origine.</div>
    <ConfirmDialog open={!!pending} onClose={()=>setPending(null)} onConfirm={()=>void remove()} title="Elimina immagine dalla libreria?"
      description={`Stai eliminando ${pending?.id||''}. I dischi privati delle VM esistenti non verranno cancellati.`} confirmLabel="Elimina immagine"
      confirmText={pending?.id} danger icon="trash"/>
  </div>;
}
function Hardware({data,onRefresh}:{data:VMStatus;onRefresh():void}){
  const [showDisks,setShowDisks]=useState(false);
  const [networkBusy,setNetworkBusy]=useState(false);
  const defaultNet=data.networks.find(n=>n.name==='default');
  const startNetwork=async()=>{setNetworkBusy(true);try{await api.network('start');toast.ok('Rete NAT avviata');onRefresh();}catch(e){toast.err('Errore rete',errText(e));}finally{setNetworkBusy(false);}};
  return <div className="vm-col"><Head title="Hardware host" subtitle="Controlli reali per virtualizzazione, GPU, rete e dischi fisici"/>
    <HostChecks host={data.host}/>
    <div className="vm-card"><div className="vm-section-title"><h3>GPU PCI / IOMMU</h3><Pill good={data.gpus.some(g=>g.ready)}>{data.gpus.filter(g=>g.ready).length} pronte</Pill></div>
      {data.gpus.length?data.gpus.map(g=><div className="vm-listitem vm-device" key={g.pci}><span className="vm-fileicon"><Icon name="cpu" size={20}/></span>
        <div className="vm-grow"><b>{g.pci} · {g.vendor}/{g.device}</b><p>Driver {g.driver} · gruppo {g.group||'assente'} · componenti {g.members.join(', ')||'n/d'}</p></div>
        <Pill good={g.ready}>{g.ready?'Idonea':'Non idonea'}</Pill></div>):<p className="vm-muted">Non sono state rilevate GPU PCI dal runtime.</p>}
      <p className="vm-muted vm-small">Il pannello non forza lo scollegamento della GPU usata dal sistema host. Per il passthrough il gruppo IOMMU deve essere isolato e assegnato a vfio-pci in precedenza.</p>
    </div>
    <div className="vm-card"><div className="vm-section-title"><h3>Reti libvirt</h3></div>
      {data.networks.map(n=><div className="vm-kv" key={n.name}><span>{n.name}</span><Pill good={n.active}>{n.active?'Attiva':'Spenta'}</Pill></div>)}
      {defaultNet&&!defaultNet.active&&<Button variant="primary" loading={networkBusy} onClick={()=>void startNetwork()}>Attiva rete NAT default</Button>}
      <p className="vm-muted vm-small">Il wizard utilizza la rete virtuale default NAT, senza esporre le porte dei sistemi guest su Internet.</p></div>
    <div className="vm-card"><div className="vm-section-title"><h3>Storage fisico dell'host</h3><Button variant="ghost" onClick={()=>setShowDisks(!showDisks)}>{showDisks?'Nascondi':'Mostra dispositivi'}</Button></div>
      {showDisks&&data.physicalDisks.map(d=><div className="vm-listitem" key={d.path}><span className="vm-fileicon"><Icon name="disk" size={18}/></span>
        <div className="vm-grow"><b>{d.path} · {d.type}</b><p>{fmtBytes(d.size)} · {d.fstype||'filesystem non specificato'} · {(d.mountpoints||[]).filter(Boolean).join(', ')||'non montato'}</p></div></div>)}
      <p className="vm-muted vm-small">I dispositivi non vengono collegati automaticamente. Usa il dettaglio della singola VM per scegliere un percorso /dev/disk/by-id o by-partuuid, dopo aver verificato il dispositivo.</p>
    </div></div>;
}
function Activity({jobs}:{jobs:Job[]}){
  return <div className="vm-col"><Head title="Operazioni in background" subtitle="Creazione VM e download continuano anche se chiudi la pagina Ervisio"/>
    {jobs.length?jobs.map(j=><div className="vm-card" key={j.id}><div className="vm-section-title"><div><h3>{j.kind==='create'?'Crea VM':'Scarica immagine'} · {j.name}</h3><p className="vm-muted vm-small">{compactDate(j.created)} · Job {j.id}</p></div>
      <Pill good={j.state==='done'}>{j.state==='running'?'In corso':j.state==='queued'?'In coda':j.state==='done'?'Completato':j.state==='failed'?'Errore':'Interrotto'}</Pill></div>
      {j.kind==='download'&&j.state==='running'&&<div className="vm-muted">Scaricati: {fmtBytes(j.bytes)}</div>}
      {j.error&&<div className="vm-alert vm-alert-warn">{j.error}</div>}
      {j.log?.length>0&&<div className="vm-log">{j.log.map((line,i)=><div key={i}>{line}</div>)}</div>}
    </div>):<div className="vm-empty"><h3>Nessun job</h3><p>I download delle immagini e le installazioni delle VM compariranno qui.</p></div>}
  </div>;
}
function VMWidget(){
  const [state,setState]=useState<VMStatus|null>(null);
  useEffect(()=>{let cancelled=false;void api.status().then(x=>{if(!cancelled)setState(x)}).catch(()=>{});return()=>{cancelled=true;};},[]);
  return <div className="vm-widget"><b>Virtual Machines</b><p>{state?`${state.vms.filter(v=>vmRunning(v.state)).length} attive / ${state.vms.length} totali`:'Verifica hypervisor…'}</p></div>;
}
function App(){
  const [page,setPage]=useState('machines');
  const [selected,setSelected]=useState('');
  const [status,setStatus]=useState<VMStatus|null>(null);
  const [hostFallback,setHostFallback]=useState<Host|null>(null);
  const [loading,setLoading]=useState(true);
  const [installBusy,setInstallBusy]=useState(false);
  const [error,setError]=useState('');
  const lastFetched=useRef(0);
  const refresh=useCallback(async(silent=false)=>{
    if(!silent)setLoading(true);
    try{
      const s=await api.status();setStatus(s);setError('');setHostFallback(null);
    }catch(e){
      if(!silent){setError(errText(e));setStatus(null);
        try{const h=await api.hostFallback();setHostFallback(h);}catch{/* show install UI with basic guidance */}}
    }finally{lastFetched.current=Date.now();if(!silent)setLoading(false);}
  },[]);
  useEffect(()=>{void refresh();const t=setInterval(()=>{if(Date.now()-lastFetched.current>=4500&&status)void refresh(true);},5000);return()=>clearInterval(t);},[refresh,status]);
  const install=async()=>{
    setInstallBusy(true);
    try{const out=await api.install();toast.ok('Runtime installato',out.slice(-220));await refresh();}
    catch(e){toast.err('Installazione runtime fallita',errText(e));await refresh();}
    finally{setInstallBusy(false);}
  };
  const selectedVM=useMemo(()=>status?.vms.find(v=>v.name===selected),[status,selected]);
  if(getSdk().platform==='windows')return <NoWindows/>;
  return <div className="vm-root hue-term">
    <header className="vm-shell-head"><span className="vm-brand"><Icon name="server" size={24}/></span>
      <div><h1>Virtual Machines</h1><p>Il tuo hypervisor KVM, direttamente da Ervisio</p></div>
      <div className="vm-grow"/><span className="vm-shell-badge">QEMU / libvirt</span>
      <Button variant="secondary" icon="refresh" loading={loading} onClick={()=>void refresh()}>Aggiorna</Button>
    </header>
    {!status&&loading?<Skeleton lines={6}/>:
      !status?<MissingRuntime host={hostFallback} onInstall={()=>void install()} busy={installBusy} onRefresh={()=>void refresh()} error={error}/>:
      <>
        <div className="vm-nav">{[{id:'machines',name:'Macchine',icon:'server'},{id:'images',name:'Immagini',icon:'disk'},
          {id:'hardware',name:'Hardware host',icon:'cpu'},{id:'activity',name:'Attività',icon:'clock'}].map(i=><button key={i.id} type="button"
            className={cls('vm-nav-item',i.id===page&&'vm-nav-active')} onClick={()=>{setSelected('');setPage(i.id)}}><Icon name={i.icon} size={16}/>{i.name}</button>)}
          <div className="vm-nav-spacer"/><Pill good={status.host.ready}>{status.host.ready?'Host pronto':'KVM non pronto'}</Pill></div>
        {status.host.ready||page==='hardware'?
          page==='machines'?(selectedVM?<VMDetail vm={selectedVM} data={status} onRefresh={()=>void refresh(true)} onBack={()=>setSelected('')}/>:
          <Dashboard data={status} onOpen={setSelected} onCreate={()=>setPage('create')}/>):
          page==='create'?<VMCreate data={status} onClose={()=>setPage('machines')} onSuccess={()=>{setPage('activity');void refresh(true)}} onImageChange={()=>void refresh(true)}/>:
          page==='images'?<Images data={status} onRefresh={()=>void refresh(true)}/>:
          page==='hardware'?<Hardware data={status} onRefresh={()=>void refresh(true)}/>:<Activity jobs={status.jobs}/>:
          <div className="vm-col"><HostChecks host={status.host}/><Button variant="secondary" onClick={()=>setPage('hardware')}>Dettagli hardware</Button></div>}
      </>}
  </div>;
}
export default function activate(sdk:PluginSDK):void {
  setSdk(sdk);setReact(sdk.react);
  const css=document.createElement('style');css.id='ervisio-vm-style';css.textContent=styles+'\n'+terminalStyles;
  document.head.appendChild(css);
  sdk.registerPage('vm',sdk.version>=3?App:()=> <div className="vm-root">Aggiorna Ervisio: è necessario SDK v3.</div>);
  sdk.registerWidget({id:'vm-overview',render:VMWidget});
}
