#!/usr/bin/env bash

# One-time VM image build; downloads Ubuntu and installs the app
# Output: standalone VM disk image and SSH key

set -euo pipefail

# Run commands from this directory
cd "$(dirname "$0")"

# Store build files here
mkdir -p build

# Stop if KVM isn't accessible
[ -r /dev/kvm ] && [ -w /dev/kvm ] || { echo "/dev/kvm not usable. Fix this first (see walkthrough: 'KVM check')."; exit 1; }

# Check that required QEMU and ISO tools are installed
command -v qemu-system-x86_64 >/dev/null || { echo "install: sudo apt install qemu-system-x86 qemu-utils genisoimage"; exit 1; }
command -v genisoimage >/dev/null || { echo "install: sudo apt install genisoimage"; exit 1; }

# Ubuntu 24.04 cloud image download URL
IMG_URL=https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img

# Download the base image only if it doesn't already exist
[ -f build/base.img ] || curl -fL --retry 3 -o build/base.img "$IMG_URL"

# Generate an SSH key pair for accessing the VM
[ -f build/id_ed25519 ] || ssh-keygen -t ed25519 -N "" -C cloud-project -f build/id_ed25519 >/dev/null

# Create a working copy and expand its virtual disk to 6 GB
cp build/base.img build/work.qcow2
qemu-img resize build/work.qcow2 6G >/dev/null

# Generate cloud-init configuration for installing the app
python3 build_seed.py
genisoimage -quiet -output build/seed.iso -volid cidata -joliet -rational-rock \
build/user-data build/meta-data

# Boot the VM to install Python and the app, then shut it down automatically
echo "Provisioning boot (the VM installs python + the app, then powers itself off)..."
timeout 1500 qemu-system-x86_64 -accel kvm -cpu host -machine q35 -smp 2 -m 2048 \
-drive file=build/work.qcow2,if=virtio,format=qcow2 \
-drive file=build/seed.iso,media=cdrom \
-netdev user,id=n0 -device virtio-net-pci,netdev=n0 \
-display none -serial file:build/provision-serial.log -monitor none

# Flatten the disk into one standalone image with no backing-file dependency
qemu-img convert -O qcow2 build/work.qcow2 cloud-project.qcow2

# Remove the temporary working disk
rm -f build/work.qcow2

# Display the final VM image information
qemu-img info cloud-project.qcow2 | sed -n '1,8p'

# Print the next steps
echo "Done. Next: ./net_up.sh   then   python3 ../bench/bench.py check"
