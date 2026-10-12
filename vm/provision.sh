#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update -y
apt-get install -y --no-install-recommends python3 python3-venv
mkdir -p /srv/app
cp /tmp/app.py /tmp/requirements.txt /srv/app/
python3 -m venv /opt/venv
/opt/venv/bin/pip install --no-cache-dir -r /srv/app/requirements.txt

cat >/etc/systemd/system/cloud-project.service <<'UNIT'
[Unit]
Description=cloud-project demo API
After=network.target

[Service]
WorkingDirectory=/srv/app
ExecStart=/opt/venv/bin/gunicorn --workers 2 --bind 0.0.0.0:5000 app:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable cloud-project.service

# Remove background noise that would perturb timing (document this in the report).
systemctl disable --now apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service 2>/dev/null || true

apt-get clean
rm -rf /var/lib/apt/lists/*
