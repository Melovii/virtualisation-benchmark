#!/usr/bin/env python3
"""Generate cloud-init configuration for the VM's initial boot."""
import pathlib
import sys

here = pathlib.Path(__file__).resolve().parent
build = here / "build"

app = (here.parent / "app" / "app.py").read_text()
req = (here.parent / "app" / "requirements.txt").read_text()
pubkey = (build / "id_ed25519.pub").read_text().strip()


def indent(text, n=6):
    return "\n".join((" " * n + line) if line else "" for line in text.splitlines())


service = """[Unit]
Description=cloud-project demo API
After=network.target

[Service]
WorkingDirectory=/srv/app
ExecStart=/opt/venv/bin/gunicorn --workers 2 --bind 0.0.0.0:5000 app:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""

netplan = """network:
  version: 2
  ethernets:
    nic0:
      match:
        name: "e*"
      addresses: [192.168.100.2/24]
"""

user_data = f"""#cloud-config
hostname: cloud-project-vm
users:
  - name: ubuntu
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    lock_passwd: true
    ssh_authorized_keys:
      - {pubkey}
package_update: true
packages:
  - python3
  - python3-venv
write_files:
  - path: /srv/app/app.py
    content: |
{indent(app)}
  - path: /srv/app/requirements.txt
    content: |
{indent(req)}
  - path: /etc/systemd/system/cloud-project.service
    content: |
{indent(service)}
  - path: /etc/netplan/60-cloud-project.yaml
    permissions: '0600'
    content: |
{indent(netplan)}
runcmd:
  - [python3, -m, venv, /opt/venv]
  - [/opt/venv/bin/pip, install, --no-cache-dir, -r, /srv/app/requirements.txt]
  - [systemctl, enable, cloud-project.service]
  - [systemctl, disable, apt-daily.timer, apt-daily-upgrade.timer, unattended-upgrades.service]
  - [rm, -f, /etc/netplan/50-cloud-init.yaml]
  - [apt-get, clean]
power_state:
  mode: poweroff
  message: provisioning finished
  timeout: 60
  condition: true
"""

(build / "user-data").write_text(user_data)
(build / "meta-data").write_text(
    "instance-id: cloud-project-provision\nlocal-hostname: cloud-project-vm\n"
)

print("wrote", build / "user-data", "and meta-data", file=sys.stderr)
