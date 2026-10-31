# Roadmap per arrivare a una vera alternativa workstation a VMware

**Non considerare nessuno di questi elementi già disponibile nella v0.1.0.**

## M1 — Hardening + UX
- Build frontend in CI con lockfile, test browser Playwright su VM Linux con libvirt nested.
- Wizard preflight per ogni guest (TPM, boot UEFI, pool, rete, requisiti distro, spazio).
- Boot order e gestione ISO CD-ROM (eject/change media).
- Clone VM offline con rigenerazione UUID/MAC, e snapshot completi multi-disco con qcow2 overlays libvirt.
- Export/import bundle VM offline con XML controllato + dischi + metadata e checksum.

## M2 — Desktop power-user
- SPICE, audio, USB redirection, clipboard bidirezionale, risoluzioni multiple.
- RDP/FreeRDP opzionale per gli ospiti che lo consentono.
- Condivisione file guest tramite qemu-guest-agent/virtiofs, controllo installazione agent.
- Configurazione CPU topology, HugePages, NUMA, CPU pinning e ballooning.

## M3 — Networking e storage
- Bridge/VLAN/port forwarding con preview e anti-lockout.
- Storage pools personalizzati, LVM, ZFS, btrfs e hotplug.
- Backup consistente con guest-agent + retention, esportazione incrementale e test restore.
- GPU SR-IOV/mdev quando il vendor/hardware supporta, guida IOMMU/vfio senza automatismi pericolosi.

## M4 — Cloud/cluster
- Controller multi-host, scheduling automatico, template versionati e cloud-init advanced.
- Migrazione live e failover solo con storage e rete correttamente predisposti.
- ACL granulari per progetto/utente, audit interhost, API e webhook.
- Benchmark host/guest, overcommit guard, capacity planning e alerting.
