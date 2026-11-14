# Multi-Distro ARM64 Btrfs Developer Workstation & Secure Boot Bootstrapper

An automated Python installation and maintenance toolkit for building a **multi-distro ARM64 (`aarch64`) developer workstation** on a single Btrfs storage pool.

Designed for developers who frequently hop across Linux distributions (**Fedora**, **Debian**, **Alpine**) and standard libraries (**`glibc`** ↔ **`musl`**) while keeping source code, container images, and KVM virtual machine disks completely isolated from OS rollbacks and reinstalls.

---

## Key Features

* **Automated Preflight & Self-Bootstrapping (`preflight_check`):** Validates `aarch64` UEFI mode, verifies `btrfs-progs >= 6.1`, auto-installs missing host CLI tools (`debootstrap`, `sbsigntools`, `mokutil`, `efibootmgr`) via `dnf`/`apt-get`, and downloads/stages upstream `rEFInd` ARM64 binaries and Btrfs EFI drivers automatically.
* **Folder-Isolated Btrfs Subvolume Hierarchy:** Every OS lives in its own top-level folder (`<os_id>/@*`) inside `subvolid=5`, eliminating cross-distro file collisions while sharing `shared/@projects`, `shared/@containers`, `shared/@libvirt`, and `shared/@swap`.
* **End-to-End ARM64 UEFI Secure Boot:** Chains Microsoft-CA-signed `shimaa64.efi` → locally MOK-signed **rEFInd** (`grubaa64.efi`) + MOK-signed `btrfs_aa64.efi` → signed kernels and automatic out-of-tree module signing (`akmods` on Fedora, `DKMS` on Debian, `kernel-hooks.d` on Alpine).
* **Automated Snapper Baseline & Rollbacks:** Initializes Snapper (`root` config) mapped to `<os_id>/@snapshots`, configures automatic `dnf5`/`apt` pre/post transaction hooks and OpenRC cron jobs, and creates an immediate `Snapshot #1` clean baseline.
* **NVMe & Windows 11 ARM64 KVM Ready:** Pre-provisions `shared/@libvirt` (`nodatacow`), installs QEMU/libvirt + `edk2-aarch64` (`AAVMF`) + `swtpm` (TPM 2.0), stages `virtio-win.iso`, and tunes NVMe APST latency (`nvme_core.default_ps_max_latency_us=0`).
* **Non-Destructive Single-OS Lifecycle Management:** Add, wipe, roll back, or reinstall any individual OS folder without touching shared developer data or requiring re-enrollment of your UEFI Machine Owner Key (MOK).

---

## Supported Target Operating Systems (`aarch64`)

| ID | Distribution | Desktop / Compositor | Init / libc | Display Manager Subvolume |
| :--- | :--- | :--- | :--- | :--- |
| `fedora_kde` | Fedora KDE | KDE Plasma | `systemd` / `glibc` | `fedora_kde/@sddm` |
| `fedora_gnome` | Fedora GNOME | GNOME Workstation | `systemd` / `glibc` | `fedora_gnome/@gdm` |
| `debian_gnome` | Debian GNOME | GNOME | `systemd` / `glibc` | `debian_gnome/@gdm3` |
| `debian_kde` | Debian KDE | KDE Plasma | `systemd` / `glibc` | `debian_kde/@sddm` |
| `alpine_xfce` | Alpine Linux | XFCE4 | `OpenRC` / `musl` | *(Root-contained)* |
| `alpine_sway` | Alpine Linux | Sway (Wayland + `seatd`) | `OpenRC` / `musl` | *(TTY / `seatd`)* |

---

## Disk Partition & Btrfs Subvolume Architecture

### GPT Partition Table

| Partition | Size | Filesystem | Label | Mount Point / Purpose |
| :--- | :--- | :--- | :--- | :--- |
| **Part 1** | `512 MiB` | `FAT32` (ESP) | `EFI` | `/boot/efi` — Shared UEFI System Partition (`shim`, `rEFInd`, `MOK.der`) |
| **Part 2** | `4 GiB` | `ext4` | `FEDORA_BOOT` | `/boot` — Dedicated boot partition for Fedora |
| **Part 3** | `Remainder` | `Btrfs` | `MULTIBOOT_POOL` | Main Btrfs pool (`subvolid=5`) hosting `shared/` and `<os_id>/` folders |

### Btrfs Pool Layout (`subvolid=5`)

```text
MULTIBOOT_POOL (subvolid=5)
├── shared/
│   ├── @projects              -> /srv/projects (symlinked to ~/Projects, UID/GID 1000)
│   ├── @containers            -> /var/lib/containers (OCI images shared across distros)
│   ├── @libvirt               -> /var/lib/libvirt/images (nodatacow, VM disks + virtio-win.iso)
│   └── @swap                  -> /swap (nodatacow, holds per-distro swapfile_<os_id>)
├── fedora_kde/
│   ├── @                      -> /
│   ├── @home                  -> /home (isolated glibc/desktop dotfiles)
│   ├── @snapshots             -> /.snapshots
│   ├── @var_log               -> /var/log
│   ├── @var_cache             -> /var/cache
│   ├── @var_tmp               -> /var/tmp
│   └── @sddm                  -> /var/lib/sddm
├── debian_gnome/
│   └── (@, @home, @snapshots, @var_log, @var_cache, @var_tmp, @gdm3)
└── alpine_sway/
    └── (@, @home, @snapshots, @var_log, @var_cache, @var_tmp)
```

> **Why isolate `@home` per distro?**
> Sharing `/home` between `glibc` distros (Fedora, Debian) and `musl` distros (Alpine) causes compiled language caches (`~/.cargo`, `~/.npm`, Python `venv`) to fail with binary incompatibility errors. Isolating `<os_id>/@home` while mounting `shared/@projects` inside each OS gives you clean builds everywhere.

---

## Prerequisites

* **Recommended Live Environment:** Boot from a **Fedora Workstation or KDE ARM64 (`aarch64`) Live ISO** in UEFI mode (works with UEFI Secure Boot enabled out of the box and includes native `dnf5` + `btrfs-progs >= 6.1`).
* **Automatic Dependency Resolution:** `preflight_check()` automatically detects and offers to install any missing host packages (`debootstrap`, `sbsigntools`, `mokutil`, `efibootmgr`, `gdisk`, `parted`, `dosfstools`, `e2fsprogs`) and stages upstream `rEFInd` (`0.14.2` `aarch64`) before touching any disk.

---

## Installation & Usage

Place both scripts in the same directory (e.g., `/usr/local/sbin/`):

```bash
chmod +x multi_distro_installer.py multiboot_maint.py
```

### 1. Fresh Multi-Distro Disk Installation

```bash
sudo ./multi_distro_installer.py
```

1. **Preflight Check:** Confirms `aarch64` UEFI environment and installs/stages any missing host dependencies.
2. **Target Block Device:** Enter the target drive (e.g., `/dev/nvme0n1`) and type `WIPE <device>` to confirm.
3. **Target OS Selection:** Enter comma-separated numbers of the distros to deploy.
4. **Installation Source:** Fetch from Internet (with local caching under `/var/cache/multiboot_installer`) or install offline from a preseeded cache.
5. **Unified Developer User:** Enforces `UID 1000` / `GID 1000` across all distros and sets user, root, and MOK enrollment passwords.

### 2. First-Boot Secure Boot MOK Enrollment

1. Reboot into your target drive.
2. `shimaa64.efi` will automatically launch **MokManager** (blue UEFI screen).
3. Select **Enroll MOK** → **Continue** → **Yes**, enter the password configured during installation, and reboot.
4. **rEFInd** will boot under full UEFI Secure Boot with menu entries for all installed systems.

---

## Maintenance, Rollbacks & Single-OS Management

Run `multiboot_maint.py` from **any booted distro** on the machine or from a **Live USB**:

```bash
sudo ./multiboot_maint.py
```

### Available Operations

1. **Inspect Btrfs pool & Snapper snapshots:** Displays all `shared/` and `<os_id>/` subvolumes, highlights the currently booted OS, and lists available Snapper snapshot IDs.
2. **Atomic Snapper rollback (`<os_id>/@`):** Moves the active `<os_id>/@` aside to `<os_id>/@_pre_rollback_<timestamp>` and creates a read-write Btrfs snapshot from your chosen Snapper snapshot ID.
3. **Wipe a single OS:** Deletes all snapshots, subvolumes, swapfile, and rEFInd menu entry for one OS folder while leaving `shared/@projects`, `shared/@containers`, `shared/@libvirt`, and other OSes intact.
4. **Sync rEFInd kernel/initrd entries:** Re-scans `/boot` and `<os_id>/@/boot` after kernel updates or rollbacks and regenerates `refind.conf`.
5. **Add / Reinstall a single OS:** Deploys a new distro folder (or cleanly replaces an existing one) into `MULTIBOOT_POOL`, reusing your existing enrolled MOK keys automatically.

---

## Repository Structure

```text
.
├── .github/workflows/ci.yml    # GitHub Actions CI (Ruff lint/format, Mypy typecheck, import smoke test)
├── .gitignore                  # Protects MOK private keys, caches, ISOs, and Python bytecode
├── multi_distro_installer.py   # Preflight check, partitioning, subvolumes, chroot bootstrap & Secure Boot
├── multiboot_maint.py          # Interactive pool inspector, Snapper rollback, rEFInd sync & single-OS installer
└── README.md                   # Architecture & usage documentation
```

---

## License

MIT License. Use at your own risk—always verify target block device paths before confirming disk wipe operations.
