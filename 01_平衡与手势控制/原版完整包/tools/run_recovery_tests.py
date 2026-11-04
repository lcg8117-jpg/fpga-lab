"""Independent command-path and absolute response-search regression."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,time
ROOT=Path(__file__).resolve().parents[1]
stage=Path('D:/Codex_Scratch')/('ADB1_RECOVERY_TEST_'+str(time.time_ns()));stage.mkdir()
names=['forest_motion_reference_shape.v','forest_d1_adaptive.v']
hashes={}
for n in names:
    p=ROOT/'work/project/src'/n;shutil.copy2(p,stage/n);hashes[n]=hashlib.sha256(p.read_bytes()).hexdigest()
for n in ['tb_reference_shape.sv','tb_absolute_search.sv']:shutil.copy2(ROOT/'tests'/n,stage/n)
sim=Path('D:/modeltech64_10.4/win64');temp=stage/'temp';temp.mkdir();(temp/'Temp').mkdir()
env=dict(os.environ);env.update(TEMP=str(temp),TMP=str(temp),TMPDIR=str(temp));checks=[]
commands=[('library',[sim/'vlib.exe','work']),('compile',[sim/'vlog.exe','-sv',*names,'tb_reference_shape.sv','tb_absolute_search.sv'])]
commands += [(n,[sim/'vsim.exe','-c','-onfinish','exit','work.'+n,'-do','onerror {quit -code 2};run -all;quit -code 0']) for n in ['tb_reference_shape','tb_absolute_search']]
for n,c in commands:
    p=subprocess.run([str(x) for x in c],cwd=stage,env=env,capture_output=True,text=True,errors='replace',timeout=300,creationflags=subprocess.CREATE_NO_WINDOW)
    text=p.stdout+p.stderr;(ROOT/'reports'/('recovery_'+n+'.log')).write_text(text,'utf-8')
    passed=p.returncode==0 and '** Error:' not in text and '** Fatal:' not in text and (n in ['library','compile'] or 'PASS' in text)
    checks.append(dict(name=n,passed=passed));print(n,passed,text[-1800:],flush=True)
    if not passed:raise RuntimeError(n+' failed')
assert all(hashlib.sha256((ROOT/'work/project/src'/n).read_bytes()).hexdigest()==h for n,h in hashes.items())
(ROOT/'reports/RECOVERY_TESTS.json').write_text(json.dumps(dict(passed=True,source_sha256=hashes,checks=checks,stage=str(stage),hardware_actions=0,physical_acceptance=False),indent=2)+'\n','utf-8')
