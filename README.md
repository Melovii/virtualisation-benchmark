# Virtual Machine vs Container

**Question:** If we were to run the same app, under the same Ubuntu 24.04 userland and same budget (2 CPUs, 2 GB): how faster would a container setup compared to a VM, how much memory and disk does each use, and how different are throughput and latency?

**Alternatives:**

- `vm` (QEMU/KVM guest)
- `ctr-ubuntu` (Docker container on `ubuntu:24.04`)
- `ctr-slim` (`python:3.12-slim`), enable with `--targets vm,ctr-ubuntu,ctr-slim`

**Layout**

```
app/       	Flask API + Dockerfiles
bench/     	harness, analysis, cleanup
report/    	IEEE LaTeX report + generated tables/figures
results/  	CSVs + raw ab output
vm/       	VirtualBox/Vagrant alternative (--vm-driver virtualbox)
vm-qemu/  	QEMU/KVM image build + TAP network (default VM driver)
```

## Environment (WSL2 or native Linux)

- WSL2 works: Docker Engine and QEMU/KVM both run inside the same WSL2 Linux VM. Containers share the WSL2 kernel.
  The QEMU guest is **nested** (KVM inside WSL2's own VM), so VM numbers include extra overhead. A native Linux host avoids this, but running it on WSL2 adds limitations.
- Do **not** use Docker Desktop's engine (the harness reaches containers on their bridge IP). Install Docker Engine inside your WSL distro (or Linux).
- Needs `/dev/kvm` (read/write), >= 6 logical CPUs (VM/container on cores 0-1, `ab` on cores 4-5), and `sudo`.

## Commands (from the repo root)

```bash
# 0. packages
sudo apt update && sudo apt install -y qemu-system-x86 qemu-utils genisoimage apache2-utils iproute2 openssh-client
#    Docker Engine: https://docs.docker.com/engine/install/ubuntu  (then: sudo usermod -aG docker $USER, restart shell)

# 1. container
docker build -t cloud-project-ubuntu -f app/Dockerfile.ubuntu app/

# 2. VM image (one time, ~10-20 min) and network (redo after every `wsl --shutdown`)
bash vm-qemu/create_vm.sh
bash vm-qemu/net_up.sh

# 3. pre-flight, then measure (~40-60 min unattended; keep the PC idle)
python3 bench/bench.py check
bash bench/run_quick.sh        # check, env, sizes, startup, load, analyze

# 4. tables/figures are now in report/tables and report/figures; build report/main.tex on Overleaf
# 5. when finished
bash bench/cleanup.sh          # screenshot its empty "leftovers" output as cost/cleanup evidence
```

## Status

| Component                                    | Status                                                                |
| -------------------------------------------- | --------------------------------------------------------------------- |
| Flask app + Dockerfiles                      | TODO: confirm built and run on my machine                             |
| Benchmark harness / analysis (`bench/`)    | TODO: confirm full run completed                                      |
| QEMU/KVM VM image (`vm-qemu/create_vm.sh`) | TODO: untested, debug as needed                                       |
| TAP network (`vm-qemu/net_up.sh`)          | TODO: untested                                                        |
| Docker bridge-IP access                      | TODO: untested                                                        |
| VirtualBox/Vagrant alternative (`vm/`)     | TODO: untested                                                        |
| Report (`report/main.tex`)                 | TODO: replace placeholders (`\todo{}`), diagram, results discussion |

**Results:** TODO: add measured numbers once a full run finishes.

**Known issues / things I had to fix:** TODO

## Known methodological limits (state them in the report)

- Nested virtualization under WSL2; the CPU pinning may not map to physical cores under WSL2.
- Network paths differ: VM on a TAP device, container on the docker bridge. Neither uses NAT, but they are not identical.
- Host-side VM memory (QEMU process RSS) includes guest page cache; container memory comes from the cgroup counter and excludes cache. Compare the guest "used" column too, and say which you use for which claim.
- Three repetitions give a rough spread, not a confidence interval. Do not claim significance from small gaps.
- Caches are warm for all measured runs (a warm-up round is discarded); cold-start is not measured.
- No disk I/O test; one machine; one application.
