#!/usr/bin/env bash

# Create the host side of the VM's private network
# Host: 192.168.100.1 | VM: 192.168.100.2
# Requires sudo.. re-run after WSL shuts down or reboots

set -euo pipefail

# Name of the virtual network interface
TAP=tap-cloudproj

# Skip creation if the interface already exists
if ip link show "$TAP" >/dev/null 2>&1; then
echo "$TAP already exists"
else
	# Create a TAP interface owned by the current user
	sudo ip tuntap add dev "$TAP" mode tap user "$USER"

	# Assign the host's IP address on the private network
	sudo ip addr add 192.168.100.1/24 dev "$TAP"

	# Enable the network interface
	sudo ip link set "$TAP" up
fi

# Display the interface's current IP address and status
ip -brief addr show "$TAP"
