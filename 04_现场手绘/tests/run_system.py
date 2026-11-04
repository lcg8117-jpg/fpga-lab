"""Compile and execute full 50 MHz RTL with actual 2 Mbaud UART in software only."""
from pathlib import Path
import importlib.util,os,subprocess,json,time,hashlib
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('build_helper',ROOT/'tests/rtl_compile.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
folder=ROOT/'tests/system_run';folder.mkdir(exist_ok=True)
src=ROOT/'V5/project/src'
before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in src.iterdir() if p.is_file()}
test_hashes={name:hashlib.sha256((ROOT/'tests'/name).read_bytes()).hexdigest() for name in ('system_probe.v','system_driver.cpp','rtl_compile.py','run_system.py')}
(ROOT/'reports/system_test.json').write_text(json.dumps(dict(passed=False,status='BUILDING',source_sha256=before,test_sha256=test_hashes,hardware_actions=0),indent=2),encoding='utf-8')
exe=helper.build(folder,'system_probe',['-I'+str(src),str(ROOT/'tests/system_probe.v')]+[str(p) for p in src.glob('*.v')],str(ROOT/'tests/system_driver.cpp'))
started=time.time()
r=subprocess.run([str(exe)],cwd=folder,capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
(ROOT/'reports/system_test.log').write_text(r.stdout+r.stderr,encoding='utf-8')
print(r.stdout,r.stderr,flush=True)
after={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in src.iterdir() if p.is_file()}
assert before==after,'RTL changed during verification'
(ROOT/'reports/system_test.json').write_text(json.dumps(dict(passed=r.returncode==0,exit_code=r.returncode,wall_seconds=time.time()-started,
 source_sha256=before,test_sha256=test_hashes,hardware_actions=0),indent=2),encoding='utf-8')
raise SystemExit(r.returncode)
