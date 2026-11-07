"""Real native child retains the scheduler flock after its wrapper is SIGKILLed."""
from pathlib import Path
import fcntl
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time

root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('hma_wrapper',root/'run.py')
wrapper=importlib.util.module_from_spec(spec);spec.loader.exec_module(wrapper)
out=root/'evidence'/f'lock_inheritance_{time.time_ns()}'
out.mkdir()
lock=out/'scheduler.lock'
owner=os.open(lock,os.O_CREAT|os.O_RDWR,0o600)
fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
identity=os.fstat(owner)
env=os.environ.copy();env['ICLR_BENCHMARK_LOCK_FD']=str(owner)
command=[sys.executable,str(root/'run.py'),'--input',str(root/'evidence/fixtures/resources/instance.json'),
         '--output-dir',str(out/'run'),'--budget-s','10','--ack-file',str(out/'never_ack')]
native=None;proc=None;contender=None;result={}
try:
    with (out/'wrapper.log').open('w') as log:
        proc=subprocess.Popen(command,env=env,pass_fds=(owner,),stdout=log,stderr=subprocess.STDOUT)
    os.close(owner);owner=None
    expires=time.monotonic()+5
    while time.monotonic()<expires:
        assert proc.poll() is None,'wrapper exited before native candidate'
        events=out/'run/events.jsonl'
        if events.exists() and events.stat().st_size:
            children=Path(f'/proc/{proc.pid}/task/{proc.pid}/children').read_text().split()
            for child in children:
                candidate=int(child)
                if Path(f'/proc/{candidate}/exe').resolve()==root/'bin/hma_reference':
                    native=candidate;break
            if native:break
        time.sleep(.01)
    assert native is not None,'native child did not start'
    inherited=Path(f'/proc/{native}/fd/{env["ICLR_BENCHMARK_LOCK_FD"]}')
    stat=inherited.stat()
    assert (stat.st_dev,stat.st_ino)==(identity.st_dev,identity.st_ino),'native inherited a different descriptor'
    proc.kill();proc.wait(timeout=2)
    os.kill(native,0)
    contender=os.open(lock,os.O_RDWR)
    try:fcntl.flock(contender,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:result['blocked_while_orphan_native_alive']=True
    else:raise AssertionError('flock released while native remained alive')
    os.kill(native,signal.SIGTERM)
    expires=time.monotonic()+3
    while True:
        try:
            fcntl.flock(contender,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result['released_after_native_exit']=True;break
        except BlockingIOError:
            if time.monotonic()>=expires:raise AssertionError('lock retained after native exit')
            time.sleep(.01)
    # Missing lock stays compatible with standalone use; bad descriptors fail closed.
    old=os.environ.pop('ICLR_BENCHMARK_LOCK_FD',None)
    try:
        assert wrapper.inherited_lock_fds()==()
        for value in ['not-a-number','-1','999999999']:
            os.environ['ICLR_BENCHMARK_LOCK_FD']=value
            try:wrapper.inherited_lock_fds()
            except (ValueError,OSError):pass
            else:raise AssertionError(f'invalid descriptor accepted: {value}')
    finally:
        if old is None:os.environ.pop('ICLR_BENCHMARK_LOCK_FD',None)
        else:os.environ['ICLR_BENCHMARK_LOCK_FD']=old
    result.update(passed=True,native_pid=native,wrapper_returncode=proc.returncode,
                  inherited_fd=int(env['ICLR_BENCHMARK_LOCK_FD']),native_descriptor_matches_lock=True,
                  malformed_closed_and_negative_fds_rejected=True,standalone_without_lock_compatible=True,
                  output=str(out),command=command)
finally:
    if owner is not None:os.close(owner)
    if contender is not None:os.close(contender)
    if proc is not None and proc.poll() is None:proc.kill();proc.wait()
    if native is not None:
        try:os.kill(native,signal.SIGTERM)
        except ProcessLookupError:pass
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
(root/'evidence/lock_inheritance.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
