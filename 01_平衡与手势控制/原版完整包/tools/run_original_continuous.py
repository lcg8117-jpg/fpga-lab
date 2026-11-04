"""Compare restored continuous/swipe stop with original user-qualified RTL."""
from pathlib import Path
import hashlib,json,os,re,shutil,subprocess,time
r=Path(__file__).resolve().parents[1];scope=json.loads((r/'integration/SOURCE_SCOPE.json').read_text('utf-8'))
baseline=Path(scope['baseline'])/'work/project/src/forest_motion_planner.v'
assert hashlib.sha256(baseline.read_bytes()).hexdigest()==scope['baseline_inputs']['src/forest_motion_planner.v']
stage=Path('D:/Codex_Scratch')/('ADB1_ORIGINAL_CONTINUOUS_'+str(time.time_ns()));stage.mkdir()
text=baseline.read_text('utf-8')
for name in re.findall(r'^module\s+(\w+)',text,re.M):text=re.sub(r'\b'+re.escape(name)+r'\b','legacy_'+name,text)
(stage/'legacy_qualified_planner.v').write_text(text,'utf-8')
names=['forest_motion_planner.v','forest_motion_coordinates.v','forest_motion_tables.v','forest_motion_finite_scurve.v','forest_motion_reference_shape.v']
hashes={}
for name in names:
    p=r/'work/project/src'/name;shutil.copy2(p,stage/name);hashes[name]=hashlib.sha256(p.read_bytes()).hexdigest()
for name in ['forest_motion_coordinates.v','forest_motion_tables.v']:assert hashes[name]==scope['baseline_inputs']['src/'+name]
shutil.copy2(r/'tests/tb_original_continuous.sv',stage/'tb_original_continuous.sv')
sim=Path('D:/modeltech64_10.4/win64');temp=stage/'temp';temp.mkdir();(temp/'Temp').mkdir()
env=dict(os.environ);env.update(TEMP=str(temp),TMP=str(temp),TMPDIR=str(temp));checks=[]
commands=[('library',[sim/'vlib.exe','work']),('compile',[sim/'vlog.exe','-sv',*names,'legacy_qualified_planner.v','tb_original_continuous.sv']),
          ('simulate',[sim/'vsim.exe','-c','-onfinish','exit','work.tb_original_continuous','-do','onerror {quit -code 2};run -all;quit -code 0'])]
for name,command in commands:
    p=subprocess.run([str(x) for x in command],cwd=stage,env=env,capture_output=True,text=True,errors='replace',timeout=300,creationflags=subprocess.CREATE_NO_WINDOW)
    text=p.stdout+p.stderr;(r/'reports'/('original_continuous_'+name+'.log')).write_text(text,'utf-8')
    ok=p.returncode==0 and '** Error:' not in text and '** Fatal:' not in text and (name!='simulate' or 'PASS ORIGINAL_CONTINUOUS' in text)
    checks.append(dict(name=name,passed=ok));print(name,ok,text[-1800:],flush=True)
    if not ok:raise RuntimeError(name+' failed')
proof=dict(passed=True,source_sha256=hashes,baseline_original_sha256=hashlib.sha256(baseline.read_bytes()).hexdigest(),stage=str(stage),checks=checks,
           scope='Same command/tick/encoder inputs, both directions, each5ms reference/velocity/acceleration/state exact against qualified original. Not a mechanical acceptance test.',hardware_actions=0)
(r/'reports/ORIGINAL_CONTINUOUS.json').write_text(json.dumps(proof,indent=2)+'\n','utf-8')
