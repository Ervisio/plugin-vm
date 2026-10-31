# Threat model e procedure di sicurezza

La gestione VM è ad alto rischio: libvirt/QEMU, GPU e storage fisico interagiscono con l'hypervisor host. `plugin-vm` non deve essere installato su host importanti senza test e audit indipendenti.

## Controlli implementati

- Bridge RPC Ervisio autenticato; HTTP capability su socket root-only, con `admin: true`, paths e metodi limitati.
- Niente TCP accessibile sul controllo; QEMU VNC per VM del plugin su loopback.
- Tokens console generati random da 192 bit, durata/limitazione sessioni, non scritti nei log.
- Ogni percorso sensibile derivato da ID rigorosamente validati; no input utente direttamente interpolato in una shell.
- Solo immagini HTTPS da domain allowlist, hash SHA-256/SHA-512 confrontati con indici ufficiali; nessuna redirezione fuori dalla allowlist.
- Per immagini importate da utente: limiti di dimensione, suffix allowlist, qemu-img JSON info senza backing filename, nessuna sovrascrittura di file esistenti.
- Cancellazione/resize/snapshot/GPU/physical consentiti solo a VM con `vm.json` root-owned e UUID corrispondente alla definizione libvirt corrente.
- QEMU guest storage sotto una directory fissa dedicata; l'eliminazione rimuove solo file dentro quella directory, mai path provenienti dall'XML di VM esterne.
- GPU: `boot_vga=1` esclusa, IOMMU group completo, ogni membro deve essere già vfio-pci. Il plugin **non** fa il rebinding del gruppo host.
- Physical disk: solo symlink stable-path e device block reali; controlli mount, child block, swap, LVM, dm-crypt, RAID; read-only predefinito; conferma addizionale per la scrittura.
- Job output troncato e senza password dell'utente; la password cloud-init entra nella generazione locale e viene hashata.

## Rischi non ancora risolti

- Root runtime e libvirt sono una superficie di attacco importante. Un servizio Python privilegiato non equivale a isolamento formale.
- Le immagini ISO custom sono codice non fidato eseguito in guest QEMU: aggiornare QEMU e host, non utilizzare host di produzione come sandbox generica.
- La verifica del checksum via manifest ufficiale HTTPS non equivale a una verifica della firma GPG del distributore.
- I dispositivi PCI, il passthrough GPU e l'accesso in scrittura a dischi fisici possono compromettere dati e disponibilità dell'host anche quando i controlli di superficie passano.
- La console QEMU VNC su loopback non ha crittografia/autenticazione propria: la sicurezza dipende dall'isolamento loopback, dalle policy libvirt e dal broker admin di Ervisio.
- Il protocollo browser→Unix socket non è ancora sottoposto a fuzzing completo o pentest.
- Snapshot solo del primario senza EFI/extra dischi non sono meccanismi di disaster recovery.
- Il comportamento di package managers/libvirt/systemd varia per distribuzione; effettuare test in VM usa-e-getta.

## Checklist prima del deploy

1. Verificare la firma del plugin marketplace e i manifest capabilities.
2. Controllare il runtime con `python -m unittest` e `npm run build`.
3. Provare create/stop/delete su una VM di test con dati sacrificabili.
4. Assicurarsi che QEMU VNC non ascolti su `0.0.0.0`.
5. Verificare ownership/SELinux/AppArmor dei dischi qcow2 e dei seed ISO.
6. Fare backup **separati e verificati** dell'host e dei guest prima di operazioni GPU/partizioni.
7. Limitare gli admin Ervisio che possono usare il plugin.
