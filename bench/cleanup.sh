#!/usr/bin/env bash
# Remove everything this project created (VM, containers, images, tap device, VM image files).
set -u
cd "$(dirname "$0")/.."
docker rm -f cloud-project-ctr-ubuntu cloud-project-ctr-slim 2>/dev/null
docker image rm cloud-project-ubuntu cloud-project-slim 2>/dev/null
[ -f /tmp/cloud-project-qemu.pid ] && kill "$(cat /tmp/cloud-project-qemu.pid)" 2>/dev/null
rm -f /tmp/cloud-project-run.qcow2 /tmp/cloud-project-qemu.pid
sudo ip link del tap-cloudproj 2>/dev/null
rm -rf vm-qemu/build vm-qemu/cloud-project.qcow2
# VirtualBox variant (only if you used --vm-driver virtualbox)
( cd vm 2>/dev/null && vagrant destroy -f 2>/dev/null )
VBoxManage unregistervm cloud-project-vm --delete 2>/dev/null
echo "--- leftovers (should print nothing from cloud-project) ---"
docker ps -a --format '{{.Names}}' | grep cloud-project || true
docker images --format '{{.Repository}}' | grep cloud-project || true
ls vm-qemu/cloud-project.qcow2 2>/dev/null || true
ip link show tap-cloudproj 2>/dev/null || true
pgrep -af qemu-system | grep cloud-project || true
echo "done"
