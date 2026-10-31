# Ervisio Virtual Machines (`plugin-vm`)

Un vero pannello KVM/QEMU e libvirt, integrato nell'interfaccia **Ervisio**.
L'obiettivo è rendere la creazione e l'uso quotidiano delle VM più semplici di una configurazione manuale, con schermo interattivo nel browser e diagnostica preventiva dei requisiti.

> **Stato: anteprima tecnica / v0.1.0.** Il backend Python e il trasporto locale della console hanno test automatici; la UI è implementata ma non è stata compilata in questo ambiente perché `npm` non riesce a raggiungere le dipendenze. **Non è stato provato su un host KVM reale** né certificato su tutte le distribuzioni. Usa un host di test, non una macchina di produzione.

## Funzioni

| Sezione | Funzionalità |
|---|---|
| Diagnostica | Riconosce architettura, flag CPU VT-x/AMD-V, VM dentro VM, hypervisor, `/dev/kvm`, controllo API KVM, KVM nested per guest, strumenti e connessione libvirt |
| Primo avvio | Installazione guidata runtime KVM/QEMU, libvirt, OVMF, swtpm, cloud-utils e servizio locale; blocca host senza accelerazione KVM prima di installare pacchetti |
| Creazione VM | Wizard per distro, immagini, CPU, RAM, dischi qcow2, rete NAT, boot automatico e desktop guest opzionale |
| Catalogo | Ubuntu 24.04 cloud + Desktop ISO, Debian 13 cloud, Arch Linux ISO e Kali ISO (download e checksum ufficiali); altre distro con sito ufficiale e import guidato |
| Windows guest | Windows 10/11 da ISO dell'utente, boot UEFI, TPM 2.0 per Windows 11. Nessun aggiramento di licenze/attivazione |
| Console grafica | QEMU VNC su loopback, trasportato via HTTP stream sull'Unix socket autorizzato Ervisio; noVNC dentro la pagina con mouse e tastiera |
| Terminale | Console seriale `virsh console` usando xterm.js, quando la seriale del guest è configurata |
| Macchine | Elenco VM libvirt anche esterne; start, shutdown, reboot, force-stop, suspend/resume, autostart, IP lease e metriche |
| Storage | Aggiunta dischi qcow2, espansione del primario offline, espulsione della ISO di installazione, lettura dei dischi, import ISO/qcow2/raw/img e rimozione dalla libreria |
| Snapshot | Snapshot **offline del solo disco principale**: creazione, elenco, restore e cancellazione. Non sono backup completi |
| GPU | Elenca GPU PCI, driver, boot VGA e gruppo IOMMU; assegna soltanto gruppi interamente su vfio-pci alla VM spenta, dopo conferma digitata |
| Storage fisico | Collegamento conservativo di device identificati da `/dev/disk/by-id` o `/dev/disk/by-partuuid`, con blocco mount/swap/volumi attivi e doppia conferma per scrittura |
| Job | Download immagini e creazione VM come thread persistenti rispetto alla pagina (continuano se chiudi la UI); log e stato memorizzati localmente |
| Windows host | Rilevato subito: **non supportato**. Sono supportati guest Windows su host Linux |

## Distinzioni importanti

**L'host è già una VM?** Non è un problema di per sé: se il provider espone VT-x/AMD-V e un `/dev/kvm` utilizzabile, `plugin-vm` può usare KVM. Se il provider non li espone, il plugin mostra una diagnosi. Non tenta di “abilitare” l'accelerazione bypassando l'hypervisor.

**Le VM create qui potranno eseguire altre VM?** Dipende dalla configurazione `nested` del modulo KVM dell'host e dalla possibilità di esporre i flag CPU agli ospiti. **Non serve nested KVM per avviare normali guest.** Il pannello non attiva in automatico `host-passthrough` e quindi non garantisce nested a un guest anche se il modulo host è abilitato.

**Desktop guest e schermo remoto non sono uguali.** Lo schermo QEMU VNC è disponibile per ISO installer e sistemi già avviati, inclusi Windows, GNOME e KDE. In un'immagine cloud Ubuntu/Debian puoi richiedere a cloud-init GNOME, KDE, Xfce o Cinnamon; il guest installa poi pacchetti al primo avvio e può impiegare diverso tempo. Non viene automaticamente creato un servizio RDP dentro il guest: non serve per la console QEMU VNC.

**Disco fisico non significa filesystem montato.** Il plugin può collegare un dispositivo a una VM, ma non lo formatta o partiziona. L'opzione è avanzata, offline e pensata solo per dispositivi dedicati o scollegati dall'host.

## Requisiti dell'host

- Ervisio **0.6.2+** con plugin SDK v3 e possibilità di sbloccare i privilegi admin.
- **Linux x86_64** e systemd. Windows host non supportato; ARM64 KVM non implementato in questa versione.
- CPU con VT-x/AMD-V, modulo KVM caricato e `/dev/kvm` disponibile. Su VPS serve esporre la virtualizzazione al guest.
- `python3`, `virsh`, `virt-install`, `qemu-img`, `cloud-localds`, `openssl`, `libvirt` e rete NAT `default` attiva.
- Per Windows 11: OVMF (UEFI) e `swtpm`. Per GPU: IOMMU, gruppo PCI completo e vfio-pci prerequisiti manuali.
- Spazio disco sufficiente (le ISO e le immagini cloud possono occupare parecchi GiB).

### Compatibilità host prevista

Il comando di installazione del runtime riconosce `apt-get`, `dnf`, `pacman` e `zypper`. I nomi dei pacchetti sono scelti per famiglie di distribuzioni, **ma i percorsi di installazione non sono stati testati su ogni release**. Controlla le transazioni dei pacchetti prima di usare un host importante.

## Come compilarlo

```bash
# Richiede Node.js 22+, Python 3.10+ e Git (SDK in dipendenza GitHub)
npm install
npm run test
npm run build
npm run pack
```

`npm run build` produce `dist/vm/` contenente `index.js`, `manifest.json`, `logo.svg`, `vm_engine.py` e `vm_service.py`.
`npm run pack` genera un archivio da firmare/pubblicare tramite le pipeline del marketplace.

### Prova in developer mode

1. Compila il plugin.
2. Nella console Ervisio attiva **Plugins → Developer**.
3. Carica `dist/vm/`. In modalità dev il plugin può funzionare unsigned solo nella modalità consentita dal core.
4. Per il runtime Linux, il percorso dichiarato nel manifest è `/var/lib/ervisio/plugins/vm/`. Se stai usando la dev folder, collega o copia **solo su una VM di test** i file `.py` nel percorso runtime previsto dal manifest, proteggendoli con permessi root, oppure installa il plugin nel percorso ufficiale tramite una release firmata.
5. Premi **Configura hypervisor**. Per host non idonei l'interfaccia spiega cosa manca.
6. Scarica Ubuntu/Debian o importa una ISO, poi esegui il wizard.
7. Dopo la creazione apri la VM e premi **Schermo interattivo** (o **Terminale seriale** se il guest lo supporta).

Per verificare i requisiti senza alcuna modifica all'host:

```bash
python3 -B plugin/vm_engine.py host
```

Per test automatici del backend, senza KVM:

```bash
python3 -B -m unittest discover -s tests -p 'test_*.py' -v
```

## Layout del repository

```text
plugin-vm/
  src/
    index.tsx             Dashboard, wizard, gestione, hardware e snapshot
    console.tsx           Viewer noVNC e terminale xterm attraverso l'SDK
    api.ts                Tipi e client API con messaggi di errore
    kit.tsx               Wrapper UI del design system Ervisio
    style.css             Tema responsive compatibile con Ervisio
  plugin/
    manifest.json         Capabilities SDK v3, limitazioni platform e permessi
    vm_engine.py          Modello dati, KVM/libvirt, OS catalog e job
    vm_service.py         HTTP root-only su Unix socket, console e setup
    logo.svg
  tests/
    test_engine.py        Diagnostica, validazioni e azioni distruttive
    test_gateway.py       HTTP Unix socket e trasporto VNC bidirezionale
  docs/
    SECURITY.md
    ARCHITECTURE.md
    NEXT.md
```

## Sicurezza

Il runtime gira come servizio **root** e ascolta esclusivamente su `/run/ervisio-vm/control.sock` (`0600`); l'SDK dichiara questa API con privilegi admin. La VNC di ogni VM creata dal plugin viene associata solo a `127.0.0.1`, non a tutte le interfacce. Il browser non riceve una connessione VNC TCP diretta: riceve un flusso tramite il broker Ervisio.

La cancellazione e le modifiche dei dischi richiedono il file metadata root-owned della VM e un confronto con il suo UUID libvirt. Le VM esterne sono rilevate ma protette da rimozione dei file. I comandi sono eseguiti con `subprocess.run([...])`, senza interpolazione shell. Il catalogo automatico usa host HTTPS in allowlist e verifica l'hash restituito dagli indici ufficiali; gli upload dell'utente non vengono verificati crittograficamente.

Per i limiti e la threat model leggi `docs/SECURITY.md`.

## Limiti noti della v0.1.0

- L'obiettivo è una **UX migliore**, non dichiarare già una maggiore affidabilità, prestazione o completezza di VMware Workstation/ESXi.
- Host solo Linux x86_64 e accelerazione KVM; **nessuna emulazione lenta di fallback** in questa versione.
- GPU passthrough richiede configurazione VFIO/IOMMU fuori dal plugin; non c'è automaticamente SR-IOV, vGPU, migrazione live o detach GPU.
- Storage host root predefinito; non supporta ancora la creazione in pool personalizzati né live-resize.
- Snapshot `qemu-img snapshot` del disco principale a VM spenta, senza altri dischi o variabili EFI, non equivalenti a backup/disaster recovery.
- Networking limitato alla rete NAT libvirt `default`; niente bridge avanzati, VLAN o port mapping automatizzato.
- Per Windows il guest potrebbe richiedere driver VirtIO per prestazioni elevate; la prima release privilegia dispositivi SATA/e1000 compatibili con l'installer.
- La console QEMU VNC non trasporta audio, USB o monitor multipli. Clipboard e resizing dipendono dal guest; serve supporto separato per audio/SPICE/RDP avanzato.
- La console seriale funziona soltanto se il guest la configura.
- La creazione delle VM e il download sono persistenti rispetto al browser, ma **non** sopravvivono automaticamente al riavvio del servizio VM: i job interrotti sono marcati `interrupted` e richiedono verifica dello stato.
- Non è ancora stato eseguito il build frontend con dipendenze reali né un test integrato su Linux con KVM in questa sessione.

## Licenze e marchi

Il codice originale `plugin-vm` è MIT (vedi `LICENSE`). Il client noVNC è MPL-2.0, xterm.js è MIT e QEMU/libvirt sono componenti di terze parti installati separatamente. I sistemi operativi dei guest restano soggetti alle rispettive licenze. I marchi Windows, Ubuntu, VMware ecc. appartengono ai rispettivi titolari.
