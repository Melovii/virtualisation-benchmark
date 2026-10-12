#!/usr/bin/env python3
"""Benchmark the VM and Docker containers running the same Flask app"""

import argparse
import csv
import datetime
import json
import os
import pathlib
import platform
import random
import re
import shutil
import subprocess
import threading
import time
import urllib.request

# Locate the project and results directories
ROOT = pathlib.Path(__file__).resolve().parent.parent
RES = pathlib.Path(os.environ.get("CLOUD_PROJECT_RES", ROOT / "results"))

# Paths and network settings for the VM and containers
VBOX_DIR = ROOT / "vm"
QEMU_DIR = ROOT / "vm-qemu"
VBOX_NAME = "cloud-project-vm"
VBOX_URL = "http://192.168.56.10:5000"
QEMU_IP = "192.168.100.2"
TAP = "tap-cloudproj"
CTR_PORT = 5001

# Give the VM and containers the same CPU and memory budget
MEM_LIMIT = "2g"
TARGETS = ["vm", "ctr-ubuntu", "ctr-slim"]
IMAGES = {"ctr-ubuntu": "cloud-project-ubuntu", "ctr-slim": "cloud-project-slim"}

# Default tests: CPU-heavy hashing and a lightweight endpoint
DEFAULT_WORKLOADS = "compute=/compute?n=5000,index=/"


# ----------------------------------------------------------------------------- helpers
def run(cmd, check=True, cwd=None, timeout=None):
	return subprocess.run(cmd, check=check, capture_output=True, text=True, cwd=cwd, timeout=timeout)


def now_iso():
	return datetime.datetime.now().isoformat(timespec="seconds")


def parse_size_mb(text):
	"""'34.5MiB' / '1.2GiB' / '900kB' -> MB (MiB)."""
	m = re.match(r"\s*([\d.]+)\s*([KMGT]?i?B)", text, re.I)
	if not m:
		return None
	v, unit = float(m.group(1)), m.group(2).lower()
	factor = {"b": 1, "kib": 1024, "kb": 1000, "mib": 1024**2, "mb": 1000**2,
			"gib": 1024**3, "gb": 1000**3}.get(unit, 1)
	return v * factor / 1024**2


def parse_ab(text):
	"""Extract throughput/latency from ApacheBench output."""
	def num(pat):
		m = re.search(pat, text, re.M)
		return float(m.group(1)) if m else None

	complete = num(r"Complete requests:\s+(\d+)")
	failed = (num(r"Failed requests:\s+(\d+)") or 0) + (num(r"Non-2xx responses:\s+(\d+)") or 0)
	return {
		"rps": num(r"Requests per second:\s+([\d.]+)"),
		"requests": complete,
		"avg_ms": num(r"Time per request:\s+([\d.]+) \[ms\] \(mean\)"),
		"med_ms": num(r"^\s*50%\s+(\d+)"),
		"p95_ms": num(r"^\s*95%\s+(\d+)"),
		"p99_ms": num(r"^\s*99%\s+(\d+)"),
		"max_ms": num(r"^\s*100%\s+(\d+)"),
		"failed_rate": (failed / complete) if complete else None,
		"dropped": None,
	}


def wait_ready(url, timeout=300):
	"""Poll /health until HTTP 200. Returns time.monotonic() at first success."""
	end = time.monotonic() + timeout
	while time.monotonic() < end:
		try:
			with urllib.request.urlopen(url + "/health", timeout=0.5) as r:
				if r.status == 200:
					return time.monotonic()
		except Exception:
			time.sleep(0.02)
	raise TimeoutError(f"{url} not ready after {timeout}s")


def append_csv(path, row):
	RES.mkdir(parents=True, exist_ok=True)
	new = not path.exists()
	with open(path, "a", newline="") as f:
		w = csv.DictWriter(f, fieldnames=list(row.keys()))
		if new:
			w.writeheader()
		w.writerow(row)


# ----------------------------------------------------------------------------- targets
class QemuVM:
	"""QEMU/KVM guest on a TAP network. Every start uses a fresh copy-on-write overlay of the
	image (like `docker run` creating a new container from an image), discarded on stop."""
	name = "vm"
	url = f"http://{QEMU_IP}:5000"

	def __init__(self):
		self.img = QEMU_DIR / "cloud-project.qcow2"
		self.key = QEMU_DIR / "build" / "id_ed25519"
		self.overlay = pathlib.Path("/tmp/cloud-project-run.qcow2")
		self.pidfile = pathlib.Path("/tmp/cloud-project-qemu.pid")

	def pid(self):
		try:
			pid = int(self.pidfile.read_text().strip())
			os.kill(pid, 0)
			return pid
		except Exception:
			return None

	def _ssh(self, remote_cmd, timeout=15):
		cmd = ["ssh", "-i", str(self.key), "-o", "StrictHostKeyChecking=no",
			"-o", "UserKnownHostsFile=/dev/null", "-o", "BatchMode=yes",
			"-o", "ConnectTimeout=3", "-o", "LogLevel=ERROR", f"ubuntu@{QEMU_IP}", remote_cmd]
		try:
			return run(cmd, check=False, timeout=timeout)
		except subprocess.TimeoutExpired:
			return None

	def start(self, cpuset):
		self.stop()
		run(["qemu-img", "create", "-q", "-f", "qcow2", "-b", str(self.img), "-F", "qcow2", str(self.overlay)])
		cmd = ["qemu-system-x86_64", "-accel", "kvm", "-cpu", "host", "-machine", "q35",
			"-smp", "2", "-m", "2048",
			"-drive", f"file={self.overlay},if=virtio,format=qcow2",
			"-netdev", f"tap,id=n0,ifname={TAP},script=no,downscript=no",
			"-device", "virtio-net-pci,netdev=n0",
			"-display", "none", "-serial", "none", "-monitor", "none",
			"-daemonize", "-pidfile", str(self.pidfile)]
		if cpuset:
			cmd = ["taskset", "-c", cpuset] + cmd   # affinity is inherited by the daemonized child
		run(cmd)

	def stop(self):
		pid = self.pid()
		if pid:
			self._ssh("sudo systemctl poweroff", timeout=10)
			for _ in range(45):
				if not self.pid():
					break
				time.sleep(1)
			else:
				run(["kill", str(pid)], check=False)
				time.sleep(2)
				if self.pid():
					run(["kill", "-9", str(pid)], check=False)
		self.overlay.unlink(missing_ok=True)
		self.pidfile.unlink(missing_ok=True)

	def mem(self):
		d = {}
		pid = self.pid()
		if pid:
			rss_kb = run(["ps", "-o", "rss=", "-p", str(pid)], check=False).stdout.strip()
			if rss_kb:
				d["host_mb"] = int(rss_kb) / 1024      # host-side resident size of the QEMU process
		r = self._ssh("free -m | awk '/Mem:/{print $3}'")
		if r is not None:
			nums = re.findall(r"^\d+$", r.stdout, re.M)
			if nums:
				d["guest_mb"] = float(nums[-1])        # memory used inside the guest (no cache)
		return d

	def size_mb(self):
		st = self.img.stat()
		return st.st_blocks * 512 / 1024**2            # space actually allocated on disk


class VirtualBoxVM:
	name = "vm"
	url = VBOX_URL

	def pid(self):
		out = run(["pgrep", "-f", f"VBoxHeadless.*{VBOX_NAME}"], check=False).stdout.split()
		return int(out[0]) if out else None

	def start(self, cpuset):
		run(["VBoxManage", "startvm", VBOX_NAME, "--type", "headless"])
		pid = None
		for _ in range(100):
			pid = self.pid()
			if pid:
				break
			time.sleep(0.05)
		if pid and cpuset:
			run(["taskset", "-a", "-cp", cpuset, str(pid)], check=False)

	def stop(self):
		run(["VBoxManage", "controlvm", VBOX_NAME, "acpipowerbutton"], check=False)
		for _ in range(90):
			info = run(["VBoxManage", "showvminfo", VBOX_NAME, "--machinereadable"], check=False).stdout
			if 'VMState="poweroff"' in info:
				time.sleep(5)
				return
			time.sleep(1)
		run(["VBoxManage", "controlvm", VBOX_NAME, "poweroff"], check=False)
		time.sleep(8)

	def mem(self):
		d = {}
		pid = self.pid()
		if pid:
			rss_kb = run(["ps", "-o", "rss=", "-p", str(pid)], check=False).stdout.strip()
			if rss_kb:
				d["host_mb"] = int(rss_kb) / 1024
		if shutil.which("vagrant"):
			r = run(["vagrant", "ssh", "-c", "free -m | awk '/Mem:/{print $3}'"], check=False, cwd=VBOX_DIR)
			nums = re.findall(r"^\d+$", r.stdout, re.M)
			if nums:
				d["guest_mb"] = float(nums[-1])
		return d

	def size_mb(self):
		info = run(["VBoxManage", "showvminfo", VBOX_NAME, "--machinereadable"]).stdout
		total = 0
		for m in re.finditer(r'^"[^"]+-\d+-\d+"="(.+\.(?:vmdk|vdi|vhd))"$', info, re.M | re.I):
			if os.path.isfile(m.group(1)):
				total += os.path.getsize(m.group(1))
		return total / 1024**2


class Container:
	"""Docker container. Default: reached directly on the docker bridge IP (no port publishing),
	which is the container-side analogue of the VM's TAP network. --ctr-publish uses -p instead."""

	def __init__(self, name, publish=False):
		self.name = name
		self.image = IMAGES[name]
		self.cname = f"cloud-project-{name}"
		self.publish = publish
		self.url = None

	def start(self, cpuset):
		run(["docker", "rm", "-f", self.cname], check=False)
		cmd = ["docker", "run", "-d", "--name", self.cname, "--memory", MEM_LIMIT]
		if self.publish:
			cmd += ["-p", f"{CTR_PORT}:5000"]
		if cpuset:
			cmd += ["--cpuset-cpus", cpuset]
		run(cmd + [self.image])
		if self.publish:
			self.url = f"http://127.0.0.1:{CTR_PORT}"
		else:
			ip = run(["docker", "inspect", "-f",
					"{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", self.cname]).stdout.strip()
			self.url = f"http://{ip}:5000"

	def stop(self):
		run(["docker", "rm", "-f", self.cname], check=False)

	def mem(self):
		out = run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", self.cname]).stdout
		mb = parse_size_mb(out.split("/")[0])
		return {"host_mb": mb, "guest_mb": mb}   # same cgroup counter for both views

	def size_mb(self):
		out = run(["docker", "image", "inspect", "-f", "{{.Size}}", self.image]).stdout
		return int(out) / 1024**2


def target_names(args):
	names = [n.strip() for n in args.targets.split(",") if n.strip()]
	bad = [n for n in names if n not in TARGETS]
	if bad:
		raise SystemExit(f"unknown targets {bad}; choose from {TARGETS}")
	return names


def make_targets(args):
	t = {"vm": QemuVM() if args.vm_driver == "qemu" else VirtualBoxVM()}
	for n in IMAGES:
		t[n] = Container(n, publish=args.ctr_publish)
	return t


def stop_all(targets):
	for t in targets.values():
		try:
			t.stop()
		except Exception as e:      # cleanup must never hide the real error
			print("cleanup warning:", e)


class Sampler(threading.Thread):
	"""Samples memory a few times during a load run; keeps the peak per metric."""

	def __init__(self, target, period):
		super().__init__(daemon=True)
		self.target, self.period, self.peak = target, period, {}
		self._stop_evt = threading.Event()

	def _sample(self):
		try:
			for k, v in self.target.mem().items():
				if v is not None:
					self.peak[k] = max(self.peak.get(k, 0), v)
		except Exception:
			pass

	def run(self):
		while not self._stop_evt.wait(self.period):
			self._sample()

	def finish(self):
		self._stop_evt.set()
		self.join(timeout=30)
		self._sample()      # one last sample right after the load stops (covers runs shorter than the period)


# ----------------------------------------------------------------------------- commands
def is_wsl():
	p = pathlib.Path("/proc/version")
	return p.exists() and "microsoft" in p.read_text().lower()


def cmd_check(args):
	problems = []
	qemu = args.vm_driver == "qemu"
	tools = ["docker", "taskset", "ps", "ab"] + (["qemu-system-x86_64", "qemu-img", "ssh", "genisoimage"]
												if qemu else ["VBoxManage", "vagrant"])
	if args.tool == "k6":
		tools.append("k6")
	for tool in tools:
		path = shutil.which(tool)
		print(f"{'ok     ' if path else 'MISSING'} {tool}: {path or ''}")
		if not path and tool not in ("vagrant", "genisoimage"):
			problems.append(tool)
	cores = os.cpu_count() or 0
	print(f"logical CPUs: {cores}")
	if cores < 6:
		print("WARNING: <6 logical CPUs. SUT uses cores 0-1 and the load tool cores 4-5; they would overlap.")
	if is_wsl():
		print("NOTE: WSL2 detected. Containers share the WSL2 kernel; the QEMU VM is nested inside WSL2's own VM, "
			"so VM numbers carry extra overhead. State this in the report's Limitations.")
	elif platform.system() != "Linux":
		print("WARNING: not Linux. Docker Desktop hides a Linux VM; the comparison would be invalid.")
	if qemu:
		kvm_ok = os.access("/dev/kvm", os.R_OK | os.W_OK)
		print(f"{'ok     ' if kvm_ok else 'MISSING'} /dev/kvm read/write access")
		if not kvm_ok:
			problems.append("/dev/kvm (see walkthrough: KVM check; maybe `sudo usermod -aG kvm $USER` + restart WSL)")
		tap = run(["ip", "link", "show", TAP], check=False).returncode == 0 if shutil.which("ip") else False
		print(f"{'ok     ' if tap else 'MISSING'} tap device {TAP} (run vm-qemu/net_up.sh)")
		if not tap:
			problems.append(TAP)
		img = QemuVM().img
		print(f"{'ok     ' if img.exists() else 'MISSING'} VM image {img} (run vm-qemu/create_vm.sh)")
		if not img.exists():
			problems.append("vm image")
	gov = pathlib.Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
	if gov.exists():
		print(f"cpu governor: {gov.read_text().strip()}")
	if shutil.which("docker"):
		up = run(["docker", "info"], check=False).returncode == 0
		print(f"{'ok     ' if up else 'MISSING'} docker daemon reachable by this user")
		if not up:
			problems.append("docker daemon (start it; add your user to the docker group)")
		for n in target_names(args):
			if n in IMAGES:
				ok = run(["docker", "image", "inspect", IMAGES[n]], check=False).returncode == 0
				print(f"{'ok     ' if ok else 'MISSING'} image {IMAGES[n]} (docker build ...)")
				if not ok:
					problems.append(IMAGES[n])
	if problems:
		raise SystemExit(f"\nNOT READY: {problems}")
	print("\nREADY")


def cmd_env(args):
	RES.mkdir(parents=True, exist_ok=True)
	cmds = [
		("platform", None), ("/proc/version", ["cat", "/proc/version"]),
		("os-release", ["lsb_release", "-ds"]), ("uname -a", ["uname", "-a"]), ("lscpu", ["lscpu"]),
		("free -h", ["free", "-h"]), ("docker --version", ["docker", "--version"]),
		("docker cgroup / server", ["docker", "info", "--format", "{{.CgroupVersion}} / {{.ServerVersion}}"]),
		("qemu", ["qemu-system-x86_64", "--version"]), ("ab", ["ab", "-V"]),
		("VBoxManage --version", ["VBoxManage", "--version"]), ("k6 version", ["k6", "version"]),
		("python", ["python3", "--version"]),
	]
	lines = [f"collected: {now_iso()}",
			"NOTE: also record by hand (PowerShell): `wsl --version` and `winver` (Windows build), plus any .wslconfig limits."]
	for title, cmd in cmds:
		lines.append(f"\n=== {title} ===")
		if cmd is None:
			lines.append(platform.platform())
			continue
		try:
			r = run(cmd, check=False)
			lines.append((r.stdout + r.stderr).strip())
		except FileNotFoundError:
			lines.append("(not installed)")
	(RES / "environment.txt").write_text("\n".join(lines) + "\n")
	print(f"wrote {RES / 'environment.txt'}")


def cmd_sizes(args):
	targets = make_targets(args)
	path = RES / "sizes.csv"
	if path.exists():
		path.unlink()
	for name in target_names(args):
		mb = targets[name].size_mb()
		append_csv(path, {"timestamp": now_iso(), "target": name, "size_mb": round(mb, 1)})
		print(f"{name}: {mb:.1f} MB")


def cmd_startup(args):
	random.seed(args.seed)
	targets = make_targets(args)
	names = target_names(args)
	stop_all(targets)
	path = RES / "startup.csv"
	try:
		for i in range(args.warmup + args.runs):
			is_warm = i < args.warmup
			for name in random.sample(names, len(names)):
				t = targets[name]
				t0 = time.monotonic()
				t.start(args.cpuset)
				t_launch = time.monotonic()
				t_ready = wait_ready(t.url)
				time.sleep(args.settle)
				mem = t.mem()
				row = {
					"timestamp": now_iso(), "run": i + 1, "warmup": int(is_warm), "target": name,
					"launch_s": round(t_launch - t0, 3), "startup_s": round(t_ready - t0, 3),
					"mem_host_mb": round(mem.get("host_mb") or 0, 1),
					"mem_guest_mb": round(mem.get("guest_mb") or 0, 1),
				}
				append_csv(path, row)
				print(row)
				t.stop()
				time.sleep(args.cooldown)
	finally:
		stop_all(targets)


def run_ab(url, path, n, c, taskset):
	cmd = ["taskset", "-c", taskset, "ab", "-n", str(n), "-c", str(c), "-r", "-s", "30", f"{url}{path}"]
	r = run(cmd, check=False)
	return r.stdout + r.stderr


def run_k6(url, mode, args, out):
	cmd = ["taskset", "-c", args.load_cpuset, "k6", "run", "--quiet",
		"-e", f"BASE_URL={url}", "-e", f"MODE={mode}", "-e", f"VUS={args.vus}",
		"-e", f"RATE={args.rate}", "-e", f"DURATION={args.duration}", "-e", f"OUT={out}",
		str(ROOT / "bench" / "load_test.js")]
	r = run(cmd, check=False)
	return r.stdout + r.stderr


def cmd_load(args):
	random.seed(args.seed)
	targets = make_targets(args)
	names = target_names(args)
	stop_all(targets)
	raw = RES / "raw"
	raw.mkdir(parents=True, exist_ok=True)
	workloads = [w.split("=", 1) for w in args.workloads.split(",")] if args.tool == "ab" else [("mixed", "")]
	try:
		for rep in range(1, args.runs + 1):
			for name in random.sample(names, len(names)):
				t = targets[name]
				t.start(args.cpuset)
				wait_ready(t.url)
				time.sleep(args.warmup_s)
				idle = t.mem()
				for wl, wpath in workloads:
					tag = f"{args.tool}_{wl}_{name}_run{rep}"
					if args.tool == "ab":
						run_ab(t.url, wpath, 500, 10, args.load_cpuset)           # untimed warm-up
					sampler = Sampler(t, period=10 if name == "vm" else 5)
					sampler.start()
					if args.tool == "ab":
						text = run_ab(t.url, wpath, args.requests, args.vus, args.load_cpuset)
						(raw / f"{tag}.txt").write_text(text)
						s = parse_ab(text)
					else:
						out = raw / f"{tag}.json"
						text = run_k6(t.url, args.mode, args, out)
						(raw / f"{tag}.log").write_text(text)
						s = json.loads(out.read_text()) if out.exists() else {}
					sampler.finish()
					if not s or s.get("rps") is None:
						print(f"!! no usable result for {tag}; see results/raw/")
						continue
					row = {
						"timestamp": now_iso(), "tool": args.tool, "mode": "saturation", "workload": wl,
						"run": rep, "target": name, "vus": args.vus,
						"requests": s.get("requests"), "rps": s.get("rps"), "avg_ms": s.get("avg_ms"),
						"med_ms": s.get("med_ms"), "p95_ms": s.get("p95_ms"), "p99_ms": s.get("p99_ms"),
						"failed_rate": s.get("failed_rate"), "dropped": s.get("dropped"),
						"mem_idle_host_mb": round(idle.get("host_mb") or 0, 1),
						"mem_idle_guest_mb": round(idle.get("guest_mb") or 0, 1),
						"mem_peak_host_mb": round(sampler.peak.get("host_mb", 0), 1),
						"mem_peak_guest_mb": round(sampler.peak.get("guest_mb", 0), 1),
					}
					append_csv(RES / "load.csv", row)
					print(row)
				t.stop()
				time.sleep(args.cooldown)
	finally:
		stop_all(targets)


def main():
	base = argparse.ArgumentParser(add_help=False)
	base.add_argument("--vm-driver", choices=["qemu", "virtualbox"], default="qemu")
	base.add_argument("--targets", default="vm,ctr-ubuntu", help="comma list from: " + ",".join(TARGETS))
	base.add_argument("--ctr-publish", action="store_true", help="reach the container via -p 127.0.0.1:5001 instead of its bridge IP")
	base.add_argument("--tool", choices=["ab", "k6"], default="ab")

	p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	sub = p.add_subparsers(dest="cmd", required=True)
	sub.add_parser("check", parents=[base]).set_defaults(fn=cmd_check)
	sub.add_parser("env", parents=[base]).set_defaults(fn=cmd_env)
	sub.add_parser("sizes", parents=[base]).set_defaults(fn=cmd_sizes)

	def common(sp):
		sp.add_argument("--cpuset", default="0,1", help="host CPUs for the VM/container (default 0,1)")
		sp.add_argument("--seed", type=int, default=421)
		sp.add_argument("--cooldown", type=int, default=15, help="seconds idle between runs")

	s = sub.add_parser("startup", parents=[base]); common(s)
	s.add_argument("--runs", type=int, default=3)
	s.add_argument("--warmup", type=int, default=1, help="discarded warm-up rounds (flagged in CSV)")
	s.add_argument("--settle", type=int, default=20, help="seconds after ready before memory sample")
	s.set_defaults(fn=cmd_startup)

	l = sub.add_parser("load", parents=[base]); common(l)
	l.add_argument("--runs", type=int, default=3)
	l.add_argument("--requests", type=int, default=20000, help="ab: requests per measurement")
	l.add_argument("--vus", type=int, default=50, help="concurrency (ab -c / k6 VUs)")
	l.add_argument("--workloads", default=DEFAULT_WORKLOADS, help="ab only: name=path,name=path")
	l.add_argument("--warmup-s", type=int, default=15, dest="warmup_s")
	l.add_argument("--load-cpuset", default="4,5", dest="load_cpuset", help="host CPUs reserved for the load tool")
	l.add_argument("--mode", choices=["saturation", "rate"], default="saturation", help="k6 only")
	l.add_argument("--duration", default="60s", help="k6 only")
	l.add_argument("--rate", type=int, default=100, help="k6 rate mode only (req/s)")
	l.set_defaults(fn=cmd_load)

	args = p.parse_args()
	args.fn(args)


if __name__ == "__main__":
	main()
