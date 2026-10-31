import { getSdk } from '@ervisio/plugin-sdk';

export type Host = {
  os:string;arch:string;kvm:boolean;cpu:number;memoryMiB:number;freeDiskBytes:number;
  tools:Record<string,boolean>;ready:boolean;supported:boolean;libvirt:boolean;message:string;diagnostics:string[];
  virtualization:{hypervisor:string;insideVM:boolean;cpuExtension:string;deviceExists:boolean;deviceAccessible:boolean;
    nestedForGuests:'enabled'|'disabled'|'unknown';nestedParameterPath:string|null;canAccelerate:boolean;hints:string[]};
};
export type VMStats={cpuTimeNs:number;balloonBytes:number;diskReadBytes:number;diskWriteBytes:number};
export type VM={name:string;uuid?:string;managed:boolean;state:string;autostart:boolean;os?:string;desktop?:string;
  diskGiB?:number;vcpus?:number;ramMiB?:number;ip?:string|null;created?:number;username?:string;graphics?:boolean;
  disks?:{target:string;source:string;device:string}[];gpus?:Record<string,string>[];stats?:VMStats;error?:string};
export type Image={id:string;type:'iso'|'disk';size:number;path:string};
export type Job={id:string;kind:string;name:string;state:'queued'|'running'|'done'|'failed'|'interrupted';
  created:number;ended?:number;log:string[];bytes:number;error?:string|null;result?:{image?:string;name?:string;state?:string}|null};
export type CatalogOS={id:string;name:string;family:string;kind:'cloud'|'iso'|'manual';recommended:number;
  desktop:boolean;note:string;website?:string;url?:string;format?:string};
export type GPU={pci:string;vendor:string;device:string;driver:string;group:string|null;members:string[];bootVga:boolean;ready:boolean};
export type PhysicalDisk={name:string;path:string;type:string;size:number;mountpoints?:string[];fstype?:string;children?:PhysicalDisk[];model?:string};
export type VMStatus={ok:boolean;host:Host;vms:VM[];images:Image[];jobs:Job[];catalog:CatalogOS[];
  networks:{name:string;active:boolean}[];gpus:GPU[];physicalDisks:PhysicalDisk[]};
export type VMConfig={name:string;osId:string;image:string;ramMiB:number;cpus:number;diskGiB:number;
  desktop:string;username:string;password:string;sshKey:string;network:'default';autostart:boolean};
export type Snapshot={name:string;info:string};

export function fmtBytes(n:number|undefined|null):string {
  if(n==null||!Number.isFinite(n))return '—';
  if(n<1024)return `${n} B`;
  const units=['KiB','MiB','GiB','TiB'];let value=n/1024;let i=0;
  while(value>=1024&&i<units.length-1){value/=1024;i++;}
  return `${value>=10?value.toFixed(1):value.toFixed(2)} ${units[i]}`;
}
export function errText(e:unknown):string{return e instanceof Error?e.message:String(e);}
export function vmRunning(s:string):boolean{return s.toLowerCase().startsWith('running');}
export function vmStopped(s:string):boolean{return s.toLowerCase().includes('shut off');}
export function imageForOS(os:CatalogOS,images:Image[]):string {
  const name=os.id+(os.kind==='iso'?'.iso':'.qcow2');
  return images.some(i=>i.id===name)?name:'';
}
async function request<T>(method:string,path:string,body?:object):Promise<T> {
  const r=await getSdk().api.http('vm-control',{method,path,...(body?{body}:{})});
  let json:unknown;
  try{json=r.json();}catch{throw new Error(`VM Runtime: risposta non valida (HTTP ${r.status})`)}
  const obj=json as {ok?:boolean;error?:string};
  if(r.status<200||r.status>=300||obj.ok===false)throw new Error(obj.error||`VM Runtime HTTP ${r.status}`);
  return json as T;
}
export const api={
  status:()=>request<VMStatus>('GET','/v1/status'),
  network:(action:'start'|'stop'|'autostart-on'|'autostart-off')=>request<{ok:true}>('POST','/v1/network',{action}),
  hostFallback:async():Promise<Host>=>{
    const r=await getSdk().api.exec('vm-host-check');
    const json=JSON.parse(r.stdout||'{}') as {ok:boolean;host?:Host;error?:string};
    if(r.exitCode!==0||!json.host)throw new Error(json.error||r.stderr||'Host check failed');
    return json.host;
  },
  install:async():Promise<string>=>{
    const r=await getSdk().api.exec('vm-runtime-install');
    if(r.exitCode!==0)throw new Error(r.stderr||r.stdout||'Runtime installation failed');
    return r.stdout;
  },
  download:(osId:string)=>request<{ok:true;job:string}>('POST','/v1/images/download',{osId}),
  create:(config:VMConfig)=>request<{ok:true;job:string}>('POST','/v1/vms',config),
  power:(name:string,action:string)=>request<{ok:true}>('POST',`/v1/vms/${name}/power`,{action}),
  deleteVM:(name:string,force:boolean)=>request<{ok:true}>('POST',`/v1/vms/${name}/delete`,{confirm:name,force}),
  resources:(name:string,ramMiB:number,cpus:number)=>request<{ok:true}>('POST',`/v1/vms/${name}/resources`,{ramMiB,cpus}),
  eject:(name:string)=>request<{ok:true}>('POST',`/v1/vms/${name}/eject`,{}),
  grow:(name:string,sizeGiB:number)=>request<{ok:true}>('POST',`/v1/vms/${name}/grow`,{sizeGiB}),
  addDisk:(name:string,sizeGiB:number)=>request<{ok:true}>('POST',`/v1/vms/${name}/disk`,{sizeGiB}),
  snapshots:(name:string)=>request<{ok:boolean;snapshots:Snapshot[]}>('GET',`/v1/vms/${name}/snapshots`),
  snapshot:(name:string,operation:string,snapshotName:string)=>request<{ok:true;note:string}>('POST',`/v1/vms/${name}/snapshot`,{operation,name:snapshotName}),
  gpu:(name:string,pci:string)=>request<{ok:true;devices:string[]}>('POST',`/v1/vms/${name}/gpu`,{pci,confirm:`PASSTHROUGH ${pci}`}),
  physical:(name:string,path:string,writable:boolean)=>request<{ok:true;target:string}>('POST',`/v1/vms/${name}/physical`,{
    path,confirm:'ATTACH PHYSICAL DEVICE',writable,writeConfirm:writable?'I ACCEPT DATA LOSS':''}),
  deleteImage:(name:string)=>request<{ok:true}>('DELETE',`/v1/images/${encodeURIComponent(name)}`),
  uploadImage:(file:File,onProgress:(loaded:number,total:number)=>void)=>getSdk().api.upload('vm-control',
    {method:'POST',path:'/v1/images/upload',query:{name:file.name}},file,{onProgress}),
  openConsole:(name:string,kind:'vnc'|'serial')=>request<{ok:true;token:string}>('POST','/v1/console',{name,kind})
};
