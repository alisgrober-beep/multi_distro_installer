#!/usr/bin/env python3
import os
import platform
import shutil
import subprocess
import sys
import getpass
import zipfile
from contextlib import contextmanager
from pathlib import Path

CACHE_DIR = Path("/var/cache/multiboot_installer")
MOK_DIR = CACHE_DIR / "mok"
MOUNT_ROOT = Path("/mnt/multiboot_target")
BTRFS_OPTS = "defaults,noatime,compress=zstd:1,space_cache=v2"
BTRFS_NOCOW_OPTS = "defaults,noatime,nodatacow"
VIRTIO_WIN_URL = "https://fedorapeople.org"
REFIND_VERSION = "0.14.2"
REFIND_ZIP_URL = f"https://sourceforge.net{REFIND_VERSION}/refind-bin-{REFIND_VERSION}.zip/download"

DISTROS = {
    "1": {
        "id": "fedora_kde",
        "name": "Fedora KDE",
        "type": "fedora",
        "desktop": "@kde-desktop-environment",
        "dm": "sddm",
        "dm_subvol": "@sddm",
        "dm_mount": "/var/lib/sddm",
        "swap_gb": 8,
    },
    "2": {
        "id": "fedora_gnome",
        "name": "Fedora GNOME",
        "type": "fedora",
        "desktop": "@workstation-product-environment",
        "dm": "gdm",
        "dm_subvol": "@gdm",
        "dm_mount": "/var/lib/gdm",
        "swap_gb": 8,
    },
    "3": {
        "id": "debian_gnome",
        "name": "Debian GNOME",
        "type": "debian",
        "desktop": "task-gnome-desktop",
        "dm": "gdm3",
        "dm_subvol": "@gdm3",
        "dm_mount": "/var/lib/gdm3",
        "swap_gb": 8,
    },
    "4": {
        "id": "debian_kde",
        "name": "Debian KDE",
        "type": "debian",
        "desktop": "task-kde-desktop",
        "dm": "sddm",
        "dm_subvol": "@sddm",
        "dm_mount": "/var/lib/sddm",
        "swap_gb": 8,
    },
    "5": {
        "id": "alpine_xfce",
        "name": "Alpine XFCE",
        "type": "alpine",
        "desktop": "xfce4 xfce4-terminal lightdm lightdm-gtk-greeter",
        "dm": "lightdm",
        "dm_subvol": None,
        "dm_mount": None,
        "swap_gb": 4,
    },
    "6": {
        "id": "alpine_sway",
        "name": "Alpine Sway",
        "type": "alpine",
        "desktop": "sway swaylock swayidle foot waybar wmenu seatd",
        "dm": None,
        "dm_subvol": None,
        "dm_mount": None,
        "swap_gb": 4,
    },
}


def run(cmd, check=True, input_text=None):
    print(f"[EXEC] {' '.join(map(str, cmd))}")
    return subprocess.run(cmd, check=check, text=True, input=input_text)


def locate_refind_assets() -> tuple[Path | None, Path | None]:
    """Finds refind_aa64.efi and btrfs_aa64.efi on the host live system."""
    refind_bin = None
    btrfs_drv = None
    search_roots = [
        Path("/usr/share/refind"),
        Path("/usr/lib/refind"),
        Path("/boot/efi/EFI/refind"),
    ]
    for root in search_roots:
        for candidate in [root / "refind_aa64.efi", root / "refind" / "refind_aa64.efi"]:
            if candidate.exists():
                refind_bin = candidate
                break
        for drv in [root / "drivers_aa64" / "btrfs_aa64.efi", root / "refind" / "drivers_aa64" / "btrfs_aa64.efi"]:
            if drv.exists():
                btrfs_drv = drv
                break
        if refind_bin:
            break
    return refind_bin, btrfs_drv


def stage_upstream_refind():
    """Downloads and extracts upstream refind-bin into /usr/share/refind if missing."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = CACHE_DIR / f"refind-bin-{REFIND_VERSION}.zip"
    extract_dir = CACHE_DIR / "refind_extracted"

    if not zip_path.exists():
        run(["curl", "-L", "--fail", "-o", str(zip_path), REFIND_ZIP_URL])

    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    src_root = next(extract_dir.glob("refind-bin-*")) / "refind"
    dest_root = Path("/usr/share/refind")
    if dest_root.exists():
        shutil.rmtree(dest_root)
    shutil.copytree(src_root, dest_root)

    install_script = next(extract_dir.glob("refind-bin-*")) / "refind-install"
    if install_script.exists():
        shutil.copy2(install_script, "/usr/local/sbin/refind-install")
        Path("/usr/local/sbin/refind-install").chmod(0o755)


def preflight_check():
    """Validates host architecture, UEFI state, and required CLI tools before touching any disk."""
    print("\n=== Running Preflight Environment & Dependency Checks ===")

    # 1. Verify ARM64 host architecture
    arch = platform.machine().lower()
    if arch not in ("aarch64", "arm64"):
        sys.exit(f"Error: Detected host architecture '{arch}'. This installer targets ARM64 (aarch64).")

    # 2. Verify live system is booted in UEFI mode
    if not Path("/sys/firmware/efi").is_dir():
        sys.exit("Error: System is not booted in UEFI mode (/sys/firmware/efi missing). Reboot in UEFI mode.")

    # 3. Map required CLI binaries to Fedora / Debian package names
    tool_packages = {
        "parted": ("parted", "parted"),
        "sgdisk": ("gdisk", "gdisk"),
        "wipefs": ("util-linux", "util-linux"),
        "blkid": ("util-linux", "util-linux"),
        "mkfs.fat": ("dosfstools", "dosfstools"),
        "mkfs.ext4": ("e2fsprogs", "e2fsprogs"),
        "mkfs.btrfs": ("btrfs-progs", "btrfs-progs"),
        "btrfs": ("btrfs-progs", "btrfs-progs"),
        "dnf": ("dnf5", "dnf"),
        "debootstrap": ("debootstrap", "debootstrap"),
        "curl": ("curl", "curl"),
        "tar": ("tar", "tar"),
        "openssl": ("openssl", "openssl"),
        "sbsign": ("sbsigntools", "sbsigntool"),
        "mokutil": ("mokutil", "mokutil"),
        "efibootmgr": ("efibootmgr", "efibootmgr"),
    }

    missing_tools = [tool for tool in tool_packages if shutil.which(tool) is None]
    if missing_tools:
        print(f"Missing required host tools: {', '.join(missing_tools)}")
        ans = input("Attempt to install missing packages automatically now? [Y/n]: ").strip().lower()
        if ans in ("", "y", "yes"):
            if shutil.which("dnf"):
                pkgs = sorted({tool_packages[t][0] for t in missing_tools})
                run(["dnf", "install", "-y", *pkgs])
            elif shutil.which("apt-get"):
                pkgs = sorted({tool_packages[t][1] for t in missing_tools})
                run(["apt-get", "update"])
                run(["apt-get", "install", "-y", *pkgs])
            else:
                sys.exit("No supported package manager (dnf/apt-get) found to auto-install dependencies.")
        else:
            sys.exit("Aborted due to missing host dependencies.")

    # Re-verify all binaries are present
    still_missing = [tool for tool in tool_packages if shutil.which(tool) is None]
    if still_missing:
        sys.exit(f"Error: The following required tools are still missing: {', '.join(still_missing)}")

    # 4. Verify btrfs-progs supports mkswapfile (btrfs-progs >= 6.1)
    mkswap_check = subprocess.run(
        ["btrfs", "filesystem", "mkswapfile", "--help"], capture_output=True, text=True
    )
    if mkswap_check.returncode != 0:
        sys.exit("Error: Installed btrfs-progs does not support 'btrfs filesystem mkswapfile' (requires >= 6.1).")

    # 5. Verify rEFInd aarch64 binary & Btrfs driver are staged on host
    refind_bin, _ = locate_refind_assets()
    if not refind_bin:
        print("rEFInd ARM64 binary (refind_aa64.efi) not found on host.")
        ans = input(f"Download and stage upstream rEFInd {REFIND_VERSION} now? [Y/n]: ").strip().lower()
        if ans in ("", "y", "yes"):
            stage_upstream_refind()
            refind_bin, _ = locate_refind_assets()
        if not refind_bin:
            sys.exit("Error: refind_aa64.efi is required for ARM64 Secure Boot setup.")

    print("[OK] Preflight checks passed: aarch64 UEFI, btrfs-progs, sbsigntools, debootstrap, dnf, and rEFInd ready.")


@contextmanager
def chroot_env(target: Path):
    binds = ["dev", "dev/pts", "proc", "sys", "run"]
    if Path("/sys/firmware/efi/efivars").is_dir():
        binds.append("sys/firmware/efi/efivars")

    mounted = []
    try:
        for b in binds:
            src = Path("/") / b
            dst = target / b
            dst.mkdir(parents=True, exist_ok=True)
            run(["mount", "--bind", str(src), str(dst)])
            mounted.append(dst)
        yield
    finally:
        for dst in reversed(mounted):
            run(["umount", "-l", str(dst)], check=False)


def chroot_run(target: Path, cmd: list, input_text=None, check=True):
    return run(["chroot", str(target), *map(str, cmd)], check=check, input_text=input_text)


def get_part_path(disk: str, part_num: int) -> str:
    if any(disk.endswith(c) for c in "0123456789"):
        return f"{disk}p{part_num}"
    return f"{disk}{part_num}"


def prompt_user_config(include_mok_note: bool = True) -> dict:
    note = " & MOK Enrollment Password" if include_mok_note else ""
    print(f"\n--- Configure Unified Developer User (UID 1000){note} ---")
    while True:
        username = input("Enter developer username (lowercase, e.g., dev): ").strip()
        if username and username.isalnum() and username.islower():
            break
        print("Invalid username. Use lowercase letters and numbers only.")

    while True:
        pw1 = getpass.getpass(f"Enter password for '{username}' (applied to user & root): ")
        pw2 = getpass.getpass("Confirm password: ")
        if pw1 and pw1 == pw2:
            break
        print("Passwords did not match or were empty. Try again.")

    return {"username": username, "password": pw1, "uid": 1000, "gid": 1000}


def generate_local_mok() -> dict:
    """Loads existing MOK from cache or running OS (/etc/secureboot-mok), or generates a new one."""
    MOK_DIR.mkdir(parents=True, exist_ok=True)
    mok_key = MOK_DIR / "MOK.key"
    mok_crt = MOK_DIR / "MOK.crt"
    mok_der = MOK_DIR / "MOK.der"

"""Use code with caution."""
live_mok = Path("/etc/secureboot-mok")
if not mok_key.exists() and (live_mok / "MOK.key").exists():
shutil.copy2(live_mok / "MOK.key", mok_key)
shutil.copy2(live_mok / "MOK.crt", mok_crt)
shutil.copy2(live_mok / "MOK.der", mok_der)
mok_key.chmod(0o600)
if not (mok_key.exists() and mok_crt.exists() and mok_der.exists()):
print("\n--- Generating Local Machine Owner Key (MOK) for ARM64 Secure Boot ---")
run([
"openssl", "req", "-newkey", "rsa:2048", "-nodes",
"-keyout", str(mok_key),
"-new", "-x509", "-sha256", "-days", "3650",
"-subj", "/CN=Local MultiBoot Developer MOK/",
"-out", str(mok_crt),
])
run(["openssl", "x509", "-outform", "DER", "-in", str(mok_crt), "-out", str(mok_der)])
mok_key.chmod(0o600)
return {"key": mok_key, "crt": mok_crt, "der": mok_der}
def check_and_wipe_disk(disk: str):
if not Path(disk).is_block_device():
sys.exit(f"Error: {disk} is not a valid block device.")
print(f"\n--- Checking existing data on {disk} ---")
run(["lsblk", "-o", "NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT", disk], check=False)
sig = subprocess.run(["wipefs", disk], capture_output=True, text=True)
if sig.stdout.strip():
print(f"\nExisting filesystem/partition signatures detected:\n{sig.stdout}")
confirm = input(f"\nWARNING: All data on {disk} will be DESTROYED. Type 'WIPE {disk}' to continue: ")
if confirm.strip() != f"WIPE {disk}":
sys.exit("Aborted by user.")
run(["wipefs", "--all", "--force", disk])
run(["sgdisk", "--zap-all", disk])
def create_partitions(disk: str):
print(f"\n--- Creating GPT & Partitions on {disk} ---")
run(["parted", "-s", disk, "mklabel", "gpt"])
run(["parted", "-s", disk, "mkpart", "EFI", "fat32", "1MiB", "513MiB"])
run(["parted", "-s", disk, "set", "1", "esp", "on"])
run(["parted", "-s", disk, "mkpart", "FEDORA_BOOT", "ext4", "513MiB", "4609MiB"])
run(["parted", "-s", disk, "mkpart", "MULTIBOOT_BTRFS", "btrfs", "4609MiB", "100%"])
run(["partprobe", disk])
p_efi = get_part_path(disk, 1)
p_boot = get_part_path(disk, 2)
p_btrfs = get_part_path(disk, 3)
run(["mkfs.fat", "-F32", "-n", "EFI", p_efi])
run(["mkfs.ext4", "-F", "-L", "FEDORA_BOOT", p_boot])
run(["mkfs.btrfs", "-f", "-L", "MULTIBOOT_POOL", p_btrfs])
return p_efi, p_boot, p_btrfs
def select_distros(single_mode: bool = False):
print("\n--- Select Target Systems (ARM64 / aarch64) ---")
for key, info in DISTROS.items():
print(f"  [{key}] {info['name']} ({info['id']})")
if single_mode:
choice = input("\nEnter single system number (e.g., 6): ").strip()
if choice not in DISTROS:
sys.exit("Invalid system selection.")
return [DISTROS[choice]]
choices = input("\nEnter comma-separated numbers (e.g., 1,3,6): ").strip().split(",")
selected = [DISTROS[c.strip()] for c in choices if c.strip() in DISTROS]
if not selected:
sys.exit("No valid systems selected. Exiting.")
return selected
def select_source_mode():
print("\n--- Select Installation Source ---")
print("  Fetch latest rootfs/packages from Internet (and cache locally)")
print(f"  Use local preseeded cache ({CACHE_DIR})")
mode = input("Choice [1/2]: ").strip()
CACHE_DIR.mkdir(parents=True, exist_ok=True)
return "cache" if mode == "2" else "internet"
def ensure_shared_subvolumes(top_mnt: Path):
shared_dir = top_mnt / "shared"
shared_dir.mkdir(parents=True, exist_ok=True)
for sv in ["@projects", "@containers", "@swap", "@libvirt"]:
sv_path = shared_dir / sv
if not sv_path.exists():
run(["btrfs", "subvolume", "create", str(sv_path)])
run(["chattr", "+C", str(shared_dir / "@swap")])
run(["chattr", "+C", str(shared_dir / "@libvirt")])
def create_single_os_subvolumes(top_mnt: Path, distro: dict):
os_dir = top_mnt / distro["id"]
os_dir.mkdir(parents=True, exist_ok=True)
distro_subvols = ["@", "@home", "@snapshots", "@var_log", "@var_cache", "@var_tmp"]
if distro["dm_subvol"]:
distro_subvols.append(distro["dm_subvol"])
for sv in distro_subvols:
sv_path = os_dir / sv
if not sv_path.exists():
run(["btrfs", "subvolume", "create", str(sv_path)])
def wipe_os_folder_subvolumes(top_mnt: Path, os_id: str):
os_dir = top_mnt / os_id
if os_dir.exists():
snap_dir = os_dir / "@snapshots"
if snap_dir.exists():
for sid_dir in sorted(snap_dir.iterdir()):
snap_sv = sid_dir / "snapshot"
if snap_sv.exists():
run(["btrfs", "subvolume", "delete", str(snap_sv)], check=False)
if sid_dir.is_dir():
shutil.rmtree(sid_dir, ignore_errors=True)
for sub in sorted(os_dir.iterdir()):
if sub.is_dir():
run(["btrfs", "subvolume", "delete", str(sub)], check=False)
os_dir.rmdir()
swapfile = top_mnt / "shared" / "@swap" / f"swapfile_{os_id}"
if swapfile.exists():
swapfile.unlink()
def create_btrfs_subvolumes(p_btrfs: str, selected_distros: list):
top_mnt = MOUNT_ROOT / "btrfs_top"
top_mnt.mkdir(parents=True, exist_ok=True)
run(["mount", "-o", "subvolid=5", p_btrfs, str(top_mnt)])
ensure_shared_subvolumes(top_mnt)
for d in selected_distros:
create_single_os_subvolumes(top_mnt, d)
run(["umount", str(top_mnt)])
def create_distro_swapfile(target_root: Path, distro_id: str, size_gb: int):
swap_file = target_root / "swap" / f"swapfile_{distro_id}"
if not swap_file.exists():
print(f"Creating {size_gb}G Btrfs swapfile for {distro_id}...")
run(["btrfs", "filesystem", "mkswapfile", "--size", f"{size_gb}g", str(swap_file)])
def provision_virtio_win_iso(target_root: Path, source_mode: str):
"""Stages virtio-win.iso into shared/@libvirt (/var/lib/libvirt/images) for Windows ARM64 VMs."""
CACHE_DIR.mkdir(parents=True, exist_ok=True)
cached_iso = CACHE_DIR / "virtio-win.iso"
libvirt_images = target_root / "var" / "lib" / "libvirt" / "images"
libvirt_images.mkdir(parents=True, exist_ok=True)
target_iso = libvirt_images / "virtio-win.iso"
if target_iso.exists():
return
if not cached_iso.exists() and source_mode == "internet":
print("--- Downloading virtio-win.iso for Windows ARM64 KVM Guest Drivers ---")
run(["curl", "-L", "--fail", "-o", str(cached_iso), VIRTIO_WIN_URL], check=False)
if cached_iso.exists():
shutil.copy2(cached_iso, target_iso)
target_iso.chmod(0o644)
def generate_fstab(target_root: Path, p_efi: str, p_boot: str, p_btrfs: str, distro: dict):
did = distro["id"]
uuid_btrfs = subprocess.check_output(["blkid", "-s", "UUID", "-o", "value", p_btrfs], text=True).strip()
uuid_efi = subprocess.check_output(["blkid", "-s", "UUID", "-o", "value", p_efi], text=True).strip()
uuid_boot = subprocess.check_output(["blkid", "-s", "UUID", "-o", "value", p_boot], text=True).strip()
lines = [
f"# /etc/fstab generated for {distro['name']} (Folder-isolated Btrfs + NVMe KVM layout)",
f"UUID={uuid_btrfs}  /                        btrfs  subvol={did}/@,{BTRFS_OPTS}  0 0",
f"UUID={uuid_btrfs}  /home                    btrfs  subvol={did}/@home,{BTRFS_OPTS}  0 0",
f"UUID={uuid_btrfs}  /.snapshots              btrfs  subvol={did}/@snapshots,{BTRFS_OPTS}  0 0",
f"UUID={uuid_btrfs}  /var/log                 btrfs  subvol={did}/@var_log,{BTRFS_OPTS}  0 0",
f"UUID={uuid_btrfs}  /var/cache               btrfs  subvol={did}/@var_cache,{BTRFS_OPTS}  0 0",
f"UUID={uuid_btrfs}  /var/tmp                 btrfs  subvol={did}/@var_tmp,{BTRFS_OPTS}  0 0",
f"UUID={uuid_btrfs}  /srv/projects            btrfs  subvol=shared/@projects,{BTRFS_OPTS}  0 0",
f"UUID={uuid_btrfs}  /var/lib/containers      btrfs  subvol=shared/@containers,{BTRFS_OPTS}  0 0",
f"UUID={uuid_btrfs}  /var/lib/libvirt/images  btrfs  subvol=shared/@libvirt,{BTRFS_NOCOW_OPTS}  0 0",
f"UUID={uuid_btrfs}  /swap                    btrfs  subvol=shared/@swap,{BTRFS_NOCOW_OPTS}  0 0",
f"/swap/swapfile_{did}  none                   swap   defaults,pri=10  0 0",
f"UUID={uuid_efi}    /boot/efi                vfat   umask=0077  0 2",
]
if distro["dm_subvol"]:
lines.append(
f"UUID={uuid_btrfs}  {distro['dm_mount']}  btrfs  subvol={did}/{distro['dm_subvol']},{BTRFS_OPTS}  0 0"
)
if distro["type"] == "fedora":
lines.append(f"UUID={uuid_boot}   /boot                    ext4   defaults  0 2")
fstab_path = target_root / "etc" / "fstab"
fstab_path.parent.mkdir(parents=True, exist_ok=True)
fstab_path.write_text("\n".join(lines) + "\n")
def configure_snapper_baseline(target: Path, p_btrfs: str, distro: dict, user_cfg: dict):
did = distro["id"]
dtype = distro["type"]
uname = user_cfg["username"]
snap_mnt = target / ".snapshots"
snap_subvol_path = f"{did}/@snapshots"
print(f"--- Configuring Snapper Baseline & Hooks for {distro['name']} ---")
run(["umount", str(snap_mnt)])
with chroot_env(target):
chroot_run(target, ["snapper", "--no-dbus", "-c", "root", "create-config", "/"])
chroot_run(target, ["btrfs", "subvolume", "delete", "/.snapshots"])
snap_mnt.mkdir(parents=True, exist_ok=True)
run(["mount", "-o", f"subvol={snap_subvol_path},{BTRFS_OPTS}", p_btrfs, str(snap_mnt)])
snap_mnt.chmod(0o750)
snap_cfg = target / "etc" / "snapper" / "configs" / "root"
if snap_cfg.exists():
cfg_text = snap_cfg.read_text()
replacements = {
'ALLOW_USERS=""': f'ALLOW_USERS="{uname}"',
'TIMELINE_LIMIT_HOURLY="10"': 'TIMELINE_LIMIT_HOURLY="5"',
'TIMELINE_LIMIT_DAILY="10"': 'TIMELINE_LIMIT_DAILY="7"',
'TIMELINE_LIMIT_WEEKLY="0"': 'TIMELINE_LIMIT_WEEKLY="2"',
'TIMELINE_LIMIT_MONTHLY="10"': 'TIMELINE_LIMIT_MONTHLY="1"',
'TIMELINE_LIMIT_YEARLY="10"': 'TIMELINE_LIMIT_YEARLY="0"',
}
for old, new in replacements.items():
cfg_text = cfg_text.replace(old, new)
snap_cfg.write_text(cfg_text)
with chroot_env(target):
if dtype == "fedora":
actions_dir = target / "etc" / "dnf" / "libdnf5-plugins" / "actions.d"
actions_dir.mkdir(parents=True, exist_ok=True)
(actions_dir / "snapper.actions").write_text(
"# Automatic Snapper pre/post snapshots for DNF5\n"
"pre_transaction::::/usr/bin/snapper create --type=pre --cleanup-algorithm=number "
"--print-number --userdata=important=yes --description=dnf5_${{pid}}\n"
"post_transaction::::/usr/bin/snapper create --type=post --cleanup-algorithm=number "
"--pre-number=${{pre_snap_id}} --userdata=important=yes --description=dnf5_${{pid}}\n"
)
chroot_run(target, ["systemctl", "enable", "snapper-timeline.timer", "snapper-cleanup.timer"], check=False)
elif dtype == "debian":
chroot_run(target, ["systemctl", "enable", "snapper-timeline.timer", "snapper-cleanup.timer"], check=False)
elif dtype == "alpine":
cron_daily = target / "etc" / "periodic" / "daily" / "snapper"
cron_daily.parent.mkdir(parents=True, exist_ok=True)
cron_daily.write_text(
"#!/bin/sh\n"
"/usr/bin/snapper --no-dbus -c root create --type=single --cleanup-algorithm=timeline "
'--description="daily-timeline"\n'
"/usr/bin/snapper --no-dbus -c root cleanup timeline\n"
"/usr/bin/snapper --no-dbus -c root cleanup number\n"
)
cron_daily.chmod(0o755)
chroot_run(target, ["rc-update", "add", "crond", "default"], check=False)
chroot_run(
target,
[
"snapper", "--no-dbus", "-c", "root", "create",
"--type", "single",
"--cleanup-algorithm", "number",
"--description", f"Baseline clean install ({distro['name']})",
],
)
def configure_secure_boot_module_signing(target: Path, distro: dict, mok: dict):
dtype = distro["type"]
print(f"--- Configuring Secure Boot Module & Kernel Signing for {distro['name']} ---")
sys_mok_dir = target / "etc" / "secureboot-mok"
sys_mok_dir.mkdir(parents=True, exist_ok=True)
shutil.copy2(mok["key"], sys_mok_dir / "MOK.key")
shutil.copy2(mok["crt"], sys_mok_dir / "MOK.crt")
shutil.copy2(mok["der"], sys_mok_dir / "MOK.der")
(sys_mok_dir / "MOK.key").chmod(0o600)
if dtype == "fedora":
akmods_priv = target / "etc" / "pki" / "akmods" / "private"
akmods_cert = target / "etc" / "pki" / "akmods" / "certs"
akmods_priv.mkdir(parents=True, exist_ok=True)
akmods_cert.mkdir(parents=True, exist_ok=True)
shutil.copy2(mok["key"], akmods_priv / "private_key.priv")
shutil.copy2(mok["der"], akmods_cert / "public_key.der")
(akmods_priv / "private_key.priv").chmod(0o600)
elif dtype == "debian":
dkms_mok_dir = target / "var" / "lib" / "shim-signed" / "mok"
dkms_mok_dir.mkdir(parents=True, exist_ok=True)
shutil.copy2(mok["key"], dkms_mok_dir / "MOK.priv")
shutil.copy2(mok["der"], dkms_mok_dir / "MOK.der")
(dkms_mok_dir / "MOK.priv").chmod(0o600)
dkms_conf = target / "etc" / "dkms" / "framework.conf.d" / "secureboot.conf"
dkms_conf.parent.mkdir(parents=True, exist_ok=True)
dkms_conf.write_text(
'mok_signing_key="/var/lib/shim-signed/mok/MOK.priv"\n'
'mok_certificate="/var/lib/shim-signed/mok/MOK.der"\n'
)
elif dtype == "alpine":
vmlinuz = target / "boot" / "vmlinuz-lts"
if vmlinuz.exists():
run([
"sbsign",
"--key", str(mok["key"]),
"--cert", str(mok["crt"]),
"--output", str(vmlinuz),
str(vmlinuz),
])
hook_script = target / "etc" / "kernel-hooks.d" / "50-sign-kernel-mok.hook"
hook_script.parent.mkdir(parents=True, exist_ok=True)
hook_script.write_text(
"#!/bin/sh\n"
"if [ -f /boot/vmlinuz-lts ] && [ -f /etc/secureboot-mok/MOK.key ]; then\n"
"  sbsign --key /etc/secureboot-mok/MOK.key --cert /etc/secureboot-mok/MOK.crt "
"--output /boot/vmlinuz-lts /boot/vmlinuz-lts\n"
"fi\n"
)
hook_script.chmod(0o755)
def configure_post_chroot(target: Path, p_btrfs: str, distro: dict, user_cfg: dict, mok: dict):
did = distro["id"]
dtype = distro["type"]
uname = user_cfg["username"]
passwd = user_cfg["password"]
uid = str(user_cfg["uid"])
gid = str(user_cfg["gid"])
hostname = f"dev-{did.replace('_', '-')}"
print(f"\n--- Configuring Post-Chroot User, KVM/Libvirt & Services for {distro['name']} ---")
(target / "etc" / "hostname").write_text(f"{hostname}\n")
with chroot_env(target):
if dtype in ("fedora", "debian"):
admin_group = "wheel" if dtype == "fedora" else "sudo"
chroot_run(target, ["groupadd", "-f", "-g", gid, uname])
chroot_run(target, ["groupadd", "-f", "libvirt"], check=False)
chroot_run(target, ["groupadd", "-f", "kvm"], check=False)
chroot_run(
target,
["useradd", "-m", "-u", uid, "-g", gid, "-G", f"{admin_group},libvirt,kvm", "-s", "/bin/bash", uname],
check=False,
)
chroot_run(target, ["chpasswd"], input_text=f"root:{passwd}\n{uname}:{passwd}\n")
sudoers_dropin = target / "etc" / "sudoers.d" / f"10-{admin_group}"
sudoers_dropin.parent.mkdir(parents=True, exist_ok=True)
sudoers_dropin.write_text(f"%{admin_group} ALL=(ALL:ALL) ALL\n")
sudoers_dropin.chmod(0o440)
if distro["dm"]:
chroot_run(target, ["systemctl", "enable", distro["dm"]], check=False)
chroot_run(target, ["systemctl", "enable", "libvirtd"], check=False)
if dtype == "fedora":
(target / ".autorelabel").touch()
elif dtype == "alpine":
chroot_run(
target,
["/sbin/apk", "add", "--no-cache", "shadow", "sudo", "doas", "bash", "sbsigntool"],
)
chroot_run(target, ["groupadd", "-f", "-g", gid, uname])
chroot_run(target, ["groupadd", "-f", "libvirt"], check=False)
chroot_run(target, ["groupadd", "-f", "kvm"], check=False)
chroot_run(
target,
[
"useradd", "-m", "-u", uid, "-g", gid,
"-G", "wheel,video,input,audio,libvirt,kvm", "-s", "/bin/bash", uname,
],
check=False,
)
chroot_run(target, ["chpasswd"], input_text=f"root:{passwd}\n{uname}:{passwd}\n")
sudoers_dropin = target / "etc" / "sudoers.d" / "10-wheel"
sudoers_dropin.parent.mkdir(parents=True, exist_ok=True)
sudoers_dropin.write_text("%wheel ALL=(ALL:ALL) ALL\n")
sudoers_dropin.chmod(0o440)
doas_conf = target / "etc" / "doas.d" / "doas.conf"
doas_conf.parent.mkdir(parents=True, exist_ok=True)
doas_conf.write_text("permit persist :wheel\n")
# Ensure tun kernel module loads at boot for libvirt NAT/bridged networking
modules_file = target / "etc" / "modules"
existing_mods = modules_file.read_text() if modules_file.exists() else ""
if "tun" not in existing_mods.splitlines():
modules_file.write_text(
existing_mods + ("tun\n" if existing_mods.endswith("\n") or not existing_mods else "\ntun\n")
)
if distro["dm"]:
chroot_run(target, ["rc-update", "add", distro["dm"], "default"], check=False)
chroot_run(target, ["rc-update", "add", "libvirtd", "default"], check=False)
if did == "alpine_sway":
chroot_run(target, ["usermod", "-aG", "seat", uname], check=False)
chroot_run(target, ["rc-update", "add", "seatd", "default"], check=False)
projects_dir = target / "srv" / "projects"
projects_dir.mkdir(parents=True, exist_ok=True)
os.chown(projects_dir, int(uid), int(gid))
libvirt_dir = target / "var" / "lib" / "libvirt" / "images"
libvirt_dir.mkdir(parents=True, exist_ok=True)
libvirt_dir.chmod(0o775)
user_proj_link = target / "home" / uname / "Projects"
if not user_proj_link.exists():
os.symlink("/srv/projects", user_proj_link)
os.lchown(user_proj_link, int(uid), int(gid))
configure_snapper_baseline(target, p_btrfs, distro, user_cfg)
configure_secure_boot_module_signing(target, distro, mok)
def bootstrap_distro(
p_efi: str, p_boot: str, p_btrfs: str, distro: dict, source_mode: str, user_cfg: dict, mok: dict
) -> dict:
did = distro["id"]
target = MOUNT_ROOT / did
target.mkdir(parents=True, exist_ok=True)
run(["mount", "-o", f"subvol={did}/@,{BTRFS_OPTS}", p_btrfs, str(target)])
for mnt, sv_path, opts in [
("home", f"{did}/@home", BTRFS_OPTS),
(".snapshots", f"{did}/@snapshots", BTRFS_OPTS),
("var/log", f"{did}/@var_log", BTRFS_OPTS),
("var/cache", f"{did}/@var_cache", BTRFS_OPTS),
("var/tmp", f"{did}/@var_tmp", BTRFS_OPTS),
("srv/projects", "shared/@projects", BTRFS_OPTS),
("var/lib/containers", "shared/@containers", BTRFS_OPTS),
("var/lib/libvirt/images", "shared/@libvirt", BTRFS_NOCOW_OPTS),
("swap", "shared/@swap", BTRFS_NOCOW_OPTS),
]:
(target / mnt).mkdir(parents=True, exist_ok=True)
run(["mount", "-o", f"subvol={sv_path},{opts}", p_btrfs, str(target / mnt)])
if distro["dm_subvol"]:
dm_path = target / distro["dm_mount"].lstrip("/")
dm_path.mkdir(parents=True, exist_ok=True)
run(["mount", "-o", f"subvol={did}/{distro['dm_subvol']},{BTRFS_OPTS}", p_btrfs, str(dm_path)])
if distro["type"] == "fedora":
(target / "boot").mkdir(parents=True, exist_ok=True)
run(["mount", p_boot, str(target / "boot")])
(target / "boot" / "efi").mkdir(parents=True, exist_ok=True)
run(["mount", p_efi, str(target / "boot" / "efi")])
create_distro_swapfile(target, did, distro["swap_gb"])
provision_virtio_win_iso(target, source_mode)
print(f"\n=== Bootstrapping {distro['name']} (aarch64 + ARM64 KVM/AAVMF/swtpm / {source_mode}) ===")
if distro["type"] == "fedora":
cache_arg = ["--setopt=keepcache=True", f"--setopt=cachedir={CACHE_DIR}/fedora"]
cmd = [
"dnf", "-y", f"--installroot={target}", "--forcearch=aarch64", "--releasever=41",
"install", "@core", "@virtualization", "edk2-aarch64", "swtpm", "swtpm-tools",
"kernel", "btrfs-progs", "snapper", "libdnf5-plugin-actions",
"sudo", "akmods", "kmodtool", "openssl", "mokutil",
"shim-aa64", "grub2-efi-aa64", distro["desktop"],
] + cache_arg
if source_mode == "cache":
cmd.append("--cacheonly")
run(cmd)
elif distro["type"] == "debian":
deb_cache = CACHE_DIR / "debian"
deb_cache.mkdir(parents=True, exist_ok=True)
run([
"debootstrap", "--arch=arm64", f"--cache-dir={deb_cache}",
"bookworm", str(target), "debian.org",
])
with chroot_env(target):
chroot_run(target, ["apt-get", "update"])
chroot_run(
target,
[
"env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y",
"linux-image-arm64", "btrfs-progs", "snapper", "sudo",
"qemu-system-arm", "libvirt-daemon-system", "libvirt-clients",
"qemu-efi-aarch64", "swtpm", "swtpm-tools", "virt-manager",
"dkms", "linux-headers-arm64", "shim-signed", "grub-efi-arm64-signed",
"mokutil", "sbsigntool", distro["desktop"],
],
)
elif distro["type"] == "alpine":
alpine_tar = CACHE_DIR / "alpine-minirootfs-aarch64.tar.gz"
if source_mode == "internet" or not alpine_tar.exists():
url = "alpinelinux.org"
run(["curl", "-L", "-o", str(alpine_tar), url])
run(["tar", "-xzf", str(alpine_tar), "-C", str(target)])
shutil.copy("/etc/resolv.conf", target / "etc" / "resolv.conf")
with chroot_env(target):
chroot_run(
target,
[
"/sbin/apk", "add", "--no-cache",
"alpine-base", "linux-lts", "btrfs-progs", "snapper", "openrc",
"qemu-system-aarch64", "qemu-img", "libvirt-daemon", "libvirt-client",
"edk2-aarch64", "swtpm", "virt-install",
*distro["desktop"].split(),
],
)
generate_fstab(target, p_efi, p_boot, p_btrfs, distro)
configure_post_chroot(target, p_btrfs, distro, user_cfg, mok)
boot_dir = target / "boot"
kernel_file = ""
initrd_file = ""
if distro["type"] == "fedora":
kernels = sorted(boot_dir.glob("vmlinuz-"))
initrds = sorted(boot_dir.glob("initramfs-.img"))
if kernels:
kernel_file = f"/{kernels[-1].name}"
if initrds:
initrd_file = f"/{initrds[-1].name}"
elif distro["type"] == "debian":
kernels = sorted(boot_dir.glob("vmlinuz-"))
initrds = sorted(boot_dir.glob("initrd.img-"))
if kernels:
kernel_file = f"/{did}/@/boot/{kernels[-1].name}"
if initrds:
initrd_file = f"/{did}/@/boot/{initrds[-1].name}"
elif distro["type"] == "alpine":
kernel_file = f"/{did}/@/boot/vmlinuz-lts"
initrd_file = f"/{did}/@/boot/initramfs-lts"
run(["umount", "-R", str(target)])
return {
"distro": distro,
"kernel": kernel_file,
"initrd": initrd_file,
}
def setup_refind_and_shim(
disk: str, p_efi: str, p_boot: str, p_btrfs: str, boot_entries: list, mok: dict, user_cfg: dict
):
print("\n=== Installing Official ARM64 Shim + MOK-Signed rEFInd Bootloader ===")
efi_mnt = MOUNT_ROOT / "efi_setup"
efi_mnt.mkdir(parents=True, exist_ok=True)
run(["mount", p_efi, str(efi_mnt)])
uuid_btrfs = subprocess.check_output(["blkid", "-s", "UUID", "-o", "value", p_btrfs], text=True).strip()
uuid_boot = subprocess.check_output(["blkid", "-s", "UUID", "-o", "value", p_boot], text=True).strip()
shim_src = None
mm_src = None
for candidate_dir in [
efi_mnt / "EFI" / "fedora",
efi_mnt / "EFI" / "debian",
efi_mnt / "EFI" / "ubuntu",
Path("/boot/efi/EFI/fedora"),
Path("/usr/share/shim"),
]:
if (candidate_dir / "shimaa64.efi").exists():
shim_src = candidate_dir / "shimaa64.efi"
mm_src = candidate_dir / "mmaa64.efi"
break
if not shim_src:
sys.exit("Error: Official Microsoft-CA signed shimaa64.efi not found on EFI partition.")
refind_dir = efi_mnt / "EFI" / "refind"
boot_fallback_dir = efi_mnt / "EFI" / "BOOT"
refind_dir.mkdir(parents=True, exist_ok=True)
boot_fallback_dir.mkdir(parents=True, exist_ok=True)
refind_bin, btrfs_drv_src = locate_refind_assets()
if not refind_bin:
sys.exit("Error: refind_aa64.efi binary not found.")
# Copy drivers and icons if staged in /usr/share/refind
src_refind_dir = refind_bin.parent
if (src_refind_dir / "icons").is_dir() and not (refind_dir / "icons").exists():
shutil.copytree(src_refind_dir / "icons", refind_dir / "icons")
drivers_dst = refind_dir / "drivers_aa64"
drivers_dst.mkdir(parents=True, exist_ok=True)
if btrfs_drv_src and btrfs_drv_src.exists():
shutil.copy2(btrfs_drv_src, drivers_dst / "btrfs_aa64.efi")
signed_refind = refind_dir / "grubaa64.efi"
run([
"sbsign",
"--key", str(mok["key"]),
"--cert", str(mok["crt"]),
"--output", str(signed_refind),
str(refind_bin),
])
shutil.copy2(shim_src, refind_dir / "shimaa64.efi")
shutil.copy2(shim_src, boot_fallback_dir / "bootaa64.efi")
shutil.copy2(signed_refind, boot_fallback_dir / "grubaa64.efi")
if mm_src and mm_src.exists():
shutil.copy2(mm_src, refind_dir / "mmaa64.efi")
shutil.copy2(mm_src, boot_fallback_dir / "mmaa64.efi")
shutil.copy2(mok["der"], efi_mnt / "MOK.der")
btrfs_drv = drivers_dst / "btrfs_aa64.efi"
if btrfs_drv.exists():
run([
"sbsign",
"--key", str(mok["key"]),
"--cert", str(mok["crt"]),
"--output", str(btrfs_drv),
str(btrfs_drv),
])
stanzas = [
"# Generated by Multi-Distro ARM64 Secure Boot Installer",
"timeout 10",
"use_nvram false",
"scanfor manual,external",
"",
]
for entry in boot_entries:
d = entry["distro"]
did = d["id"]
dtype = d["type"]
kernel = entry["kernel"]
initrd = entry["initrd"]
if dtype == "fedora":
vol_uuid = uuid_boot
options = (
f"root=UUID={uuid_btrfs} rootflags=subvol={did}/@ "
f"nvme_core.default_ps_max_latency_us=0 rw quiet rhgb"
)
elif dtype == "debian":
vol_uuid = uuid_btrfs
options = (
f"root=UUID={uuid_btrfs} rootflags=subvol={did}/@ "
f"nvme_core.default_ps_max_latency_us=0 rw quiet"
)
else:
vol_uuid = uuid_btrfs
options = (
f"root=UUID={uuid_btrfs} rootflags=subvol={did}/@ "
f"modules=sd-mod,usb-storage,btrfs rootfstype=btrfs rw"
)
stanzas.extend([
f'menuentry "{d["name"]} (Secure Boot)" {{',
f"    volume {vol_uuid}",
f"    loader {kernel}",
f"    initrd {initrd}",
f'    options "{options}"',
"}",
"",
])
(refind_dir / "refind.conf").write_text("\n".join(stanzas))
if Path("/sys/firmware/efi/efivars").is_dir():
print("\n--- Queuing Local MOK for UEFI MokManager Enrollment on First Reboot ---")
pw = user_cfg["password"]
run(["mokutil", "--import", str(mok["der"])], input_text=f"{pw}\n{pw}\n", check=False)
run([
"efibootmgr", "--create",
"--disk", disk,
"--part", "1",
"--label", "rEFInd Secure Boot (ARM64)",
"--loader", r"\EFI\refind\shimaa64.efi",
], check=False)
run(["umount", str(efi_mnt)])
def main():
if os.geteuid() != 0:
sys.exit("Run this installer as root.")
preflight_check()
disk = input("\nEnter target block device (e.g., /dev/nvme0n1 or /dev/sda): ").strip()
check_and_wipe_disk(disk)
selected_distros = select_distros(single_mode=False)
source_mode = select_source_mode()
user_cfg = prompt_user_config()
mok = generate_local_mok()
p_efi, p_boot, p_btrfs = create_partitions(disk)
create_btrfs_subvolumes(p_btrfs, selected_distros)
boot_entries = []
for distro in selected_distros:
entry = bootstrap_distro(p_efi, p_boot, p_btrfs, distro, source_mode, user_cfg, mok)
boot_entries.append(entry)
setup_refind_and_shim(disk, p_efi, p_boot, p_btrfs, boot_entries, mok, user_cfg)
print("\n=== Installation Complete ===")
print("Btrfs hierarchy ready: shared/@projects, shared/@containers, shared/@libvirt (nodatacow), shared/@swap")
if name == "main":
main()
