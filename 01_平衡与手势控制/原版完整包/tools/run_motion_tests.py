"""Offline discrete reference, math, command chaining and coordinate regressions."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,time

ROOT=Path(__file__).resolve().parents[1]
stage=Path('D:/Codex_Scratch')/('ADB1_BRAKE_QUEUE_TEST_'+str(time.time_ns()));stage.mkdir()
names=['forest_motion_finite_scurve.v','forest_motion_planner.v','forest_motion_coordinates.v','forest_motion_tables.v','forest_d1_gesture_decode.v','forest_motion_brake_step.v','forest_motion_reference_shape.v']
hashes={}
for n in names:
    p=ROOT/'work/project/src'/n;shutil.copy2(p,stage/n);hashes[n]=hashlib.sha256(p.read_bytes()).hexdigest()
for source in ('tests/tb_finite_scurve.sv','tests/tb_motion_brake_queue.sv','tests/fixtures/tb_motion_coordinates.v','tests/tb_gesture_start_guard.sv','tests/tb_brake_step.sv','tests/tb_optical_interval_980.sv'):
    shutil.copy2(ROOT/source,stage/Path(source).name)
sim=Path('D:/modeltech64_10.4/win64')
temp=stage/'temp';temp.mkdir();(temp/'Temp').mkdir()
env=dict(os.environ);env.update(TEMP=str(temp),TMP=str(temp),TMPDIR=str(temp))
checks=[]
commands=[('library',[sim/'vlib.exe','work']),('compile',[sim/'vlog.exe','-sv',*names,'tb_finite_scurve.sv','tb_motion_brake_queue.sv','tb_motion_coordinates.v','tb_gesture_start_guard.sv','tb_brake_step.sv','tb_optical_interval_980.sv'])]
commands += [(name,[sim/'vsim.exe','-c','-onfinish','exit','work.'+name,'-do','onerror {quit -code 2};run -all;quit -code 0']) for name in ('tb_finite_scurve','tb_motion_brake_queue','tb_motion_coordinates','tb_gesture_start_guard','tb_brake_step','tb_optical_interval_980')]
for name,cmd in commands:
    p=subprocess.run([str(x) for x in cmd],cwd=stage,env=env,capture_output=True,text=True,errors='replace',timeout=300,creationflags=subprocess.CREATE_NO_WINDOW)
    output=p.stdout+p.stderr;(ROOT/'reports'/('motion_'+name+'.log')).write_text(output,'utf-8')
    ok=p.returncode==0 and '** Error:' not in output and '** Fatal:' not in output and (name in ('library','compile') or 'PASS' in output)
    checks.append(dict(name=name,passed=ok));print(name,ok,output[-2200:],flush=True)
    if not ok:raise RuntimeError('Motion test failed; inspect reports/motion_'+name+'.log')
assert all(hashlib.sha256((ROOT/'work/project/src'/n).read_bytes()).hexdigest()==h for n,h in hashes.items())
result=dict(passed=True,checks=checks,source_sha256=hashes,stage=str(stage),hardware_actions=0,physical_overshoot_verified=False,
            scope='Discrete reference endpoint/caps, exact serial arithmetic, both encoder kits, one pending command, original continuous opposite-swipe braking, STOP cancellation. Ideal follower is not a mechanical plant.')
(ROOT/'reports/MOTION_TESTS.json').write_text(json.dumps(result,indent=2)+'\n','utf-8')
