"""Run one owned ModelSim/Simulink process pair without touching hardware."""
from pathlib import Path
import hashlib,json,subprocess,sys,time,re,shutil,os
ROOT=Path(__file__).absolute().parents[1]
case=ROOT/'cosim/cases'/sys.argv[1]
assert case.is_dir() and not (case/'EXECUTION_RECEIPT.json').exists()
freeze=json.loads((case/'MODEL_FREEZE.json').read_text('utf8'))
for name,expected in freeze['files'].items():
 assert hashlib.sha256((case/name).read_bytes()).hexdigest()==expected,name
for name,expected in freeze['native_files'].items():
 assert hashlib.sha256((ROOT/'cosim'/freeze.get('native_directory','native')/name).read_bytes()).hexdigest()==expected,name
 assert hashlib.sha256((case/'native_snapshot'/name).read_bytes()).hexdigest()==expected,name
for name,expected in freeze['model_sources'].items():
 assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected,name
shutil.copyfile(__file__,case/'EXECUTED_run.py')
receipt={'status':'RUNNING','scope':'MATCHED_CONDITIONAL_MODEL_NOT_PHYSICAL_ACCEPTANCE','hardware_actions':0,
 'runner_sha256':hashlib.sha256((case/'EXECUTED_run.py').read_bytes()).hexdigest()}
started=time.time();server=None;matlab=None
receipt['started_unix']=started
def kill(p):
 if p is not None and p.poll() is None:
  subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW);p.wait(timeout=20)
try:
 with (case/'modelsim.stdout.log').open('wb') as ms,(case/'matlab.stdout.log').open('wb') as ml,(case/'matlab.stderr.log').open('wb') as err:
  server=subprocess.Popen(['E:/Moildesim/win64/vsim.exe','-c','-modelsimini','modelsim.ini','-onfinish','exit','-l','modelsim_native.log','-do','do start_case.do'],cwd=case,stdin=subprocess.PIPE,stdout=ms,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
  deadline=time.monotonic()+60
  while 'V5_CURRENT_FIELD_SERVER_READY' not in (case/'modelsim.stdout.log').read_text(errors='replace'):
   if server.poll() is not None:raise RuntimeError('ModelSim exited before readiness')
   if time.monotonic()>deadline:raise TimeoutError('ModelSim readiness timeout')
   time.sleep(.2)
  matlab=subprocess.Popen(['D:/Program Files/MATLAB/R2025b/bin/matlab.exe','-batch',f"run('{(case/'run_case.m').as_posix()}')"],cwd=case,stdout=ml,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW)
  receipt.update(modelsim_pid=server.pid,matlab_pid=matlab.pid)
  (case/'EXECUTION_RECEIPT.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf8')
  last_progress=0
  while matlab.poll() is None:
   if server.poll() is not None:raise RuntimeError('ModelSim exited during MATLAB')
   markers=re.findall(r'F2_PROGRESS sample=(\d+)([^\r\n]*)',(case/'modelsim.stdout.log').read_text(errors='replace'))
   if markers and int(markers[-1][0])>last_progress:
    last_progress=int(markers[-1][0]);fields={k:int(v) for k,v in re.findall(r'(\w+)=(-?\d+)',markers[-1][1])}
    progress=dict(case=case.name,sample=last_progress,physical_ms=fields.pop('physical_ms',last_progress-1),
     wall_unix=time.time(),elapsed_s=time.time()-started,cache_enabled=freeze.get('simulation_actor_cache',False),**fields)
    temp=case/'PROGRESS.tmp';temp.write_text(json.dumps(progress,indent=2)+'\n',encoding='utf8');os.replace(temp,case/'PROGRESS.json')
    if last_progress%10000==0:print(json.dumps(progress),flush=True)
   time.sleep(.5)
  assert matlab.returncode==0,'MATLAB nonzero'
  server.stdin.write(b'quit -code 0 -force\n');server.stdin.flush();server.wait(timeout=20)
  assert server.returncode==0 and (case/'RAW_TRACE.mat').exists()
  receipt.update(status='PASS_EXECUTION_ONLY',trace_sha256=hashlib.sha256((case/'RAW_TRACE.mat').read_bytes()).hexdigest())
except Exception as error:receipt.update(status='FAIL',error=str(error))
finally:
 kill(matlab);kill(server)
 receipt.update(elapsed_s=time.time()-started,matlab_exit_code=None if matlab is None else matlab.returncode,modelsim_exit_code=None if server is None else server.returncode,freeze_sha256=hashlib.sha256((case/'MODEL_FREEZE.json').read_bytes()).hexdigest())
 (case/'EXECUTION_RECEIPT.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf8');print(json.dumps(receipt))
if receipt['status']!='PASS_EXECUTION_ONLY':raise SystemExit(1)
