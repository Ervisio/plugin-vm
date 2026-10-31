# Architettura di plugin-vm

```text
Browser Ervisio / iframe plugin
  React SDK v3 UI                    (nessun cookie o accesso DOM al core)
  | sdk.api.http / httpStream / upload
  v
Core Ervisio capability broker
  controlla signed manifest, paths, HTTP method, user admin unlock
  |
  v
Root bridge → Unix socket /run/ervisio-vm/control.sock (0600)
  |
  +--> vm_service.py (ThreadingUnixServer / JSON control API)
  |    |
  |    +--> vm_engine.py (subprocess argv fissi, virsh, virt-install, qemu-img)
  |    +--> background task runner (jobs JSON state, no credentials in logs)
  |    +--> virtual console sessions (ephemeral token)
  |
  +--> /v1/stream (octet-stream, VNC o seriale)
  +<-- /v1/input  (serialized binary reverse channel)
  |
QEMU VNC listener 127.0.0.1:59xx  OR  virsh console pseudo-TTY
```

## API

* `GET /v1/status`: requisiti host, VM, immagini, network, jobs, GPU PCI e dischi fisici.
* `GET /v1/health`: health check del runtime.
* `POST /v1/network`: solo azioni sul network libvirt `default` (start/stop/autostart).
* `POST /v1/vms`: valida e accoda il job di creazione.
* `POST /v1/images/download`: accoda il download dalla allowlist ufficiale con hash.
* `POST /v1/images/upload?name=`: upload binario streamed.
* `DELETE /v1/images/<filename>`: elimina un'immagine non utilizzata come origine.
* `POST /v1/vms/<name>/power`: start, shutdown, destroy, reboot, reset, suspend, resume, autostart.
* `POST /v1/vms/<name>/resources`: risorse offline (solo VM gestite).
* `POST /v1/vms/<name>/grow`, `/disk`, `/eject`: espande qcow2, aggiunge disco, espelle la ISO di installazione (VM spenta).
* `GET /v1/vms/<name>/snapshots`, `POST /v1/vms/<name>/snapshot`: qemu-img snapshot offline del primario.
* `POST /v1/vms/<name>/gpu`: attach GPU group pre-qualificato e confermato (VM spenta).
* `POST /v1/vms/<name>/physical`: attach block device stable-path pre-verificato (VM spenta).
* `POST /v1/vms/<name>/delete`: elimina solo definizione VM identificata tramite UUID e dischi root-owned.
* `POST /v1/console {name, kind}`: crea sessione `vnc` o `serial` temporanea, restituisce token.
* `GET /v1/stream`: stream binario raw; token in `X-VM-Token`.
* `POST /v1/input`: input binario ordinato, token in `X-VM-Token`.
* `DELETE /v1/console`: chiude la sessione.

Non c'è una route per eseguire comandi arbitrarî o specificare XML/flag libvirt non validati.

## Storage

* `/var/lib/libvirt/images/ervisio-vm/_library` — immagini scaricate o caricate; ISO readonly per libvirt.
* `/var/lib/libvirt/images/ervisio-vm/<name>/` — dischi qcow2 indipendenti, metadata `vm.json`, cloud-init seed.
* `/var/lib/ervisio-vm/jobs` — stato e log dei job, protetti da root.
* `/run/ervisio-vm/control.sock` — socket effimero del servizio.

Gli account libvirt/QEMU cambiano da distro a distro. Le directory dei file guest sono attraversabili (0711) e `libvirt` normalmente modifica ownership/label dei file per il processo QEMU. Verificare MAC policy/SELinux/AppArmor con un test reale; non abbassare indiscriminatamente la protezione dei dischi guest.

## VM template modes

1. **Cloud Ubuntu/Debian ufficiale**: verifica digest, `qemu-img convert`, resize, cloud-init seed con hash SHA512-crypt della password, import senza installer, guest qemu-agent e desktop opzionale.
2. **ISO ufficiale o custom**: crea qcow2 vuoto, CD-ROM ISO e VNC; l'installer è completamente interattivo.
3. **Immagine qcow2/raw preinstallata**: copia indipendente e `virt-install --import`, senza cloud-init automatico.

## Limitazioni architetturali

* Le API sono sincronizzate all'interno di un singolo servizio root; il runner usa thread e file JSON, non una coda transazionale multi-host.
* Un service restart interrompe job di download e create ma lascia dati; viene registrato `interrupted`.
* Il traffico console bidirezionale passa tramite canali HTTP autorizzati dal broker. La versione definitiva dovrebbe valutare WebRTC/SPICE e controllo qualità/adaptive streaming.
* Nessun cluster, live migration o backup consistente multi-disco in questa prima release.
