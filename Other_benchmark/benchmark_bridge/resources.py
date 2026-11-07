"""Linux resource evidence; measures the execution server and owned processes."""
from __future__ import annotations
import os
from pathlib import Path
import platform
import socket
import time


def process_info(pid):
    root = Path("/proc") / str(pid)
    try:
        fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
        status = dict(line.split(":", 1) for line in (root / "status").read_text().splitlines())
        return {"pid": int(pid), "ppid": int(fields[1]), "pgrp": int(fields[2]),
                "name": status["Name"].strip(), "state": fields[0],
                "cpu_s": (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK"),
                "rss_bytes": int(status.get("VmRSS", "0 kB").split()[0]) * 1024,
                "peak_rss_bytes": int(status.get("VmHWM", "0 kB").split()[0]) * 1024,
                "threads": int(status["Threads"]),
                "affinity": sorted(os.sched_getaffinity(int(pid)))}
    except (OSError, ValueError, KeyError, IndexError):
        return None


def physical_cpu_representatives():
    cores = {}
    for cpu in sorted(os.sched_getaffinity(0)):
        topology = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
        key = ((topology / "physical_package_id").read_text().strip(),
               (topology / "core_id").read_text().strip())
        cores.setdefault(key, cpu)
    return list(cores.values())


def hardware_snapshot():
    mem = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    cpus = physical_cpu_representatives()
    cgroup = Path("/sys/fs/cgroup")
    relative = Path("/proc/self/cgroup").read_text().strip().split("::")[-1].lstrip("/")
    current = cgroup / relative
    controls = {}
    while current == cgroup or cgroup in current.parents:
        for name in ("cpu.max", "memory.max", "memory.current", "cpuset.cpus.effective"):
            f = current / name
            if f.exists():
                controls[str(f)] = f.read_text().strip()
        if current == cgroup:
            break
        current = current.parent
    processes = [p for path in Path("/proc").glob("[0-9]*")
                 if (p := process_info(int(path.name))) and p["rss_bytes"] > 50 * 1024**2]
    return {"host": socket.gethostname(), "platform": platform.platform(),
            "logical_cpu_count": os.cpu_count(), "affinity": sorted(os.sched_getaffinity(0)),
            "available_physical_cores": len(cpus), "physical_cpu_representatives": cpus,
            "memory_total_bytes": int(mem["MemTotal"].split()[0]) * 1024,
            "memory_available_bytes": int(mem["MemAvailable"].split()[0]) * 1024,
            "cgroup_controls": controls, "loadavg": list(os.getloadavg()),
            "visible_large_processes": processes,
            "process_visibility_pid_namespace": os.readlink("/proc/self/ns/pid")}


class JobSampler:
    def __init__(self, process_group, worker_pid):
        self.group = process_group
        self.worker_pid = worker_pid
        self.known = set([process_group, worker_pid])
        self.peak_rss = 0
        self.peak_threads = 0
        self.max_cpu_percent = 0.
        self.seen = {}
        self.previous = None

    def sample(self):
        # Discover descendants through procfs child lists, without scanning the
        # whole host at 32 samples per second.
        pending = list(self.known)
        while pending:
            pid = pending.pop()
            try:
                children = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
            except OSError:
                continue
            for child in children:
                if int(child) not in self.known:
                    self.known.add(int(child))
                    pending.append(int(child))
        members = []
        for pid in self.known:
            info = process_info(pid)
            if info and (pid == self.worker_pid or info["pgrp"] == self.group):
                members.append(info)
                old = self.seen.setdefault(pid, {"peak_rss_bytes": 0, "peak_threads": 0})
                old.update(name=info["name"], affinity=info["affinity"],
                    peak_rss_bytes=max(old["peak_rss_bytes"], info["peak_rss_bytes"]),
                    peak_threads=max(old["peak_threads"], info["threads"]), cpu_s=info["cpu_s"])
        now = time.monotonic()
        rss = sum(p["rss_bytes"] for p in members)
        cpu = sum(p["cpu_s"] for p in self.seen.values())
        cpu_percent = None
        if self.previous:
            cpu_percent = max(0., (cpu - self.previous[1]) / (now - self.previous[0]) * 100)
            self.max_cpu_percent = max(self.max_cpu_percent, cpu_percent)
        self.previous = now, cpu
        self.peak_rss = max(self.peak_rss, rss)
        self.peak_threads = max(self.peak_threads, max((p["threads"] for p in members
                                                       if p["pid"] != self.worker_pid), default=0))
        return {"monotonic_s": now, "rss_bytes": rss, "cpu_percent_one_core": cpu_percent,
                "processes": members}

    def summary(self):
        return {"sample_period_s": 1., "peak_job_rss_bytes": self.peak_rss,
                "max_solver_process_threads": self.peak_threads,
                "max_job_cpu_percent_one_core": self.max_cpu_percent,
                "processes": self.seen}
