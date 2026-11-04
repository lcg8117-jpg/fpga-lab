from pathlib import Path
import hashlib,json,os,subprocess,time
ROOT=Path(__file__).resolve().parents[1];SRC=ROOT/'V5/project/src'
BIN=Path(os.environ.get('DS12_ICARUS_BIN','D:/Codex_Scratch/SWD4_AutoTune_20261020/W1_Icarus_Portable/ucrt64/bin'))
env=os.environ.copy();env['PATH']=str(BIN)+';'+env['PATH']
inputs=[ROOT/'tests/live_planner_tb.sv']+[SRC/name for name in ('forest_motion_planner.v','forest_motion_tables.v','forest_motion_coordinates.v')]
hashes={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
out=ROOT/'tests/live_planner.vvp';started=time.time()
r=subprocess.run([str(BIN/'iverilog.exe'),'-g2012','-s','live_planner_tb','-o',str(out),*[str(p) for p in inputs]],capture_output=True,env=env)
log=r.stdout+r.stderr
if r.returncode==0:
    r=subprocess.run([str(BIN/'vvp.exe'),str(out)],capture_output=True,env=env,cwd=ROOT/'tests');log+=r.stdout+r.stderr
(ROOT/'reports/live_planner.log').write_bytes(log)
passed=r.returncode==0 and b'PASS live_planner' in log
(ROOT/'reports/live_planner.json').write_text(json.dumps(dict(passed=passed,input_sha256=hashes,seconds=time.time()-started,hardware_actions=0),indent=2),encoding='utf-8')
print(log.decode(errors='replace'));raise SystemExit(0 if passed else 1)
