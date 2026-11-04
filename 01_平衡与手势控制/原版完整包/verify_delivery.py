from pathlib import Path
import hashlib,json,subprocess,sys
r=Path(__file__).resolve().parent
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
manifest=json.loads((r/'SHA256_MANIFEST.json').read_text('utf-8'))
for n,d in manifest.items():assert h(r/n)==d,n
q=json.loads((r/'QUALIFIED.json').read_text('utf-8'))
assert q['accepted'] and q['firmware_id']=='41444231' and h(r/q['firmware'])==q['firmware_sha256']
for n,d in q['source_sha256'].items():assert h(r/'work/project'/n)==d,n
for n,d in q['unchanged_adb1_inputs'].items():assert h(r/'work/project'/n)==d,n
assert q['fmax_mhz']>=50 and q['unrouted_nets']==0
p=subprocess.run([sys.executable,'-B',str(r/'tools/recheck_portable.py'),'--build',str(r/'native_evidence')],capture_output=True,text=True,encoding='utf-8',errors='replace')
assert p.returncode==0,p.stdout+p.stderr
native=json.loads(p.stdout)
assert native['recheck']=='PASS' and native['bitstream_sha256']==q['firmware_sha256']
print('PASS: ADB1 recovery and absolute P/D delivery; no hardware actions')
