from pathlib import Path
import subprocess,time,json,sys
import os
def children(pid):
 try: direct=[int(x) for x in Path(f"/proc/{pid}/task/{pid}/children").read_text().split()]
 except FileNotFoundError:return []
 return direct+[g for child in direct for g in children(child)]
root=Path(__file__).resolve().parents[1]
out=root/'evidence/cus1000_profile_30s';out.mkdir(parents=True,exist_ok=False)
command=[sys.executable,str(root/'run.py'),'--input',str(root.parent/'benchmark_bridge/fixtures/real_cus1000/instance.json'),'--output-dir',str(out/'run'),'--budget-s','30','--seed','2655651288']
started=time.monotonic();p=subprocess.Popen(command);peak_total=peak_native=threads=0;native_cpu={};samples=0
with (out/'resources.jsonl').open('w') as log:
 while p.poll() is None:
  allp=[p.pid]+children(p.pid)
  total=0;native=[]
  for pid in allp:
   try:
    status={line.split(":",1)[0]:line.split(":",1)[1].strip() for line in Path(f"/proc/{pid}/status").read_text().splitlines() if ":" in line};rss=int(status.get("VmRSS","0 kB").split()[0])*1024;total+=rss;name=status["Name"]
    if name.startswith('hma_reference'):
     nt=int(status["Threads"]);stat=Path(f"/proc/{pid}/stat").read_text().split(") ",1)[1].split();cpu=(int(stat[11])+int(stat[12]))/os.sysconf("SC_CLK_TCK");native_cpu[pid]=cpu
     threads=max(threads,nt);peak_native=max(peak_native,rss)
     native.append(dict(pid=pid,rss_bytes=rss,threads=nt,cpu_s=cpu,affinity=sorted(os.sched_getaffinity(pid))))
   except (FileNotFoundError,ProcessLookupError,PermissionError):pass
  peak_total=max(peak_total,total);samples+=1
  log.write(json.dumps(dict(elapsed_s=time.monotonic()-started,tree_rss_bytes=total,native=native))+'\n');log.flush();time.sleep(.1)
summary=dict(command=command,returncode=p.returncode,elapsed_s=time.monotonic()-started,peak_tree_rss_bytes=peak_total,peak_native_rss_bytes=peak_native,max_native_threads=threads,native_cpu_s=sum(native_cpu.values()),samples=samples)
(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
