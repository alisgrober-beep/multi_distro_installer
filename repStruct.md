
Repository Structure

# text . ├── .github/workflows/ci.yml    
# Dual-arch CI matrix (Ruff lint/format, Mypy typecheck, contract tests) ├── .gitignore                  
# Protects MOK private keys, caches, ISOs, VM images, and bytecode ├── multi_distro_installer.py   
# Preflight, GPT/Btrfs setup, chroot bootstrap, Snapper & Secure Boot ├── multiboot_maint.py          
# Interactive pool inspector, Snapper rollback, rEFInd sync & single-OS add ├── win11_vm.py                
# Dual-arch Windows 11 KVM guest provisioner & cross-distro VM registrar └── README.md                   
# Architecture, usage & CI documentation 
