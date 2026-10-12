#!/usr/bin/env python3

"""
Manual control of the QEMU VM for smoke tests
The benchmark script handles this automatically

Commands:
    python3 bench/vmctl.py start          Boot VM and wait for /health
    python3 bench/vmctl.py status         Check whether VM is running
    python3 bench/vmctl.py ssh            Open an interactive SSH shell
    python3 bench/vmctl.py ssh hostname   Run a command inside the VM
    python3 bench/vmctl.py stop           Shut down the VM
"""

import os
import pathlib
import sys
import time

# Import bench.py so we can reuse its VM controls and health check
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bench  # noqa: E402


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    vm = bench.QemuVM()
    if cmd == "start":
        t0 = time.monotonic()
        vm.start("0,1")
        bench.wait_ready(vm.url)
        print(f"ready after {time.monotonic() - t0:.1f} s at {vm.url} (pid {vm.pid()})")
    elif cmd == "stop":
        vm.stop()
        print("stopped")
    elif cmd == "status":
        print(f"running, pid {vm.pid()}" if vm.pid() else "not running")
    elif cmd == "ssh":
        args = ["ssh", "-i", str(vm.key), "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR", f"ubuntu@{bench.QEMU_IP}"] + sys.argv[2:]
        os.execvp("ssh", args)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
