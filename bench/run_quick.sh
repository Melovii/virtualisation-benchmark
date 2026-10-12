#!/usr/bin/env bash
# Measurement profile: VM vs one container (same Ubuntu 24.04 userland), 2 workloads.
# 3 startup rounds (+1 discarded warm-up) and 3 load runs per target: ~40-60 min unattended.
# Prerequisites: docker image built, VM image built (vm-qemu/create_vm.sh), tap up (vm-qemu/net_up.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
T=vm,ctr-ubuntu
python3 bench/bench.py check --targets $T
python3 bench/bench.py env
python3 bench/bench.py sizes --targets $T
python3 bench/bench.py startup --targets $T --runs 3 --warmup 1
python3 bench/bench.py load --targets $T --runs 3
python3 bench/analyze.py
echo "Done. Tables/figures are in report/. Cleanup when finished: bash bench/cleanup.sh"
