"""Compile memory-safety regressions using shared sanitizer objects (four compilers)."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
root=Path(__file__).resolve().parents[1]
src=root.parent/'vendor/HMA/src'
build=root/'bin/memory_check_objects'
build.mkdir(exist_ok=True)
flags=['g++','-std=c++11','-O1','-g1','-fsanitize=address,undefined','-fno-omit-frame-pointer','-I',str(src)]
names=['eval','operator','solution','util','data','evolution','reference_runtime']
def compile_one(name):
 subprocess.run(flags+['-c',str(src/(name+'.cpp')),'-o',str(build/(name+'.o'))],check=True)
with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(compile_one,names))
for test in ['move_contract','psi_contract','resource_contract']:
 subprocess.run(flags+[str(root/'tests'/(test+'.cpp'))]+[str(build/(name+'.o')) for name in names]+['-o',str(root/'bin'/(test+'_fixed_asan'))],check=True)
