from pathlib import Path
import hashlib,json,struct
r=Path(__file__).resolve().parent
m=json.loads((r/'SHA256SUMS.json').read_text('utf-8'))
for n,h in m.items():
 p=(r/n).resolve()
 assert p.is_relative_to(r) and p.is_file(),n
 assert hashlib.sha256(p.read_bytes()).hexdigest()==h,n
s=json.loads((r/'S1参数.json').read_text('utf-8'))
assert [f'{v:08x}' for v in struct.unpack('<6I',struct.pack('<6f',*s['gains']))]==s['IEEE754']
assert s['gains']==[53.78,0,210.14,17.67,0,616.61]
assert s['period_ms']==dict(angleP=1,angleD=5,positionP=5,positionD=25)
print('PASS: all archived files, S1 exact bits and periods; no hardware access')
