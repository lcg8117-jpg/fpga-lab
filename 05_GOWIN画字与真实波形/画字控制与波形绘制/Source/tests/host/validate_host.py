"""Run headless FakeSerial tests and bind the exact Host inputs."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]

def hashes():
    return {str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
            for base in (ROOT/'host',ROOT/'tests/host') for p in base.rglob('*')
            if p.is_file() and p.suffix in ('.py','.json','.ps1','.md')
            and 'results' not in p.parts and '__pycache__' not in p.parts and not any(x.startswith('tmp') for x in p.parts)}

def main():
    before=hashes();out=ROOT/'tests/host/results';out.mkdir(exist_ok=True)
    run=subprocess.run([sys.executable,'-B','-X','utf8','-m','unittest','discover','-s',str(ROOT/'tests/host'),'-p','test_host.py','-v'],cwd=ROOT,capture_output=True,timeout=60)
    output=(run.stdout+run.stderr).decode('utf8',errors='replace');(out/'host.log').write_text(output,encoding='utf8')
    unchanged=before==hashes();passed=run.returncode==0 and unchanged and '\nOK\n' in output.replace('\r\n','\n')
    result={'status':'PASS' if passed else 'FAIL','returncode':run.returncode,'hardware_access':False,'live_GUI':False,'source_unchanged':unchanged,'source_sha256':before,'log':'tests/host/results/host.log'}
    (out/'RESULT.json').write_text(json.dumps(result,indent=2),encoding='utf8');print(output);print(result['status'],len(before),'source-bound Host inputs');return 0 if passed else 1

if __name__=='__main__':raise SystemExit(main())
