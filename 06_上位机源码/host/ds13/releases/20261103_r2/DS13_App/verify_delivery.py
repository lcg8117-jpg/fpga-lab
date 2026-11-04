"""Read-only DS13 artifact verification. No device access."""
from pathlib import Path
import hashlib,json,struct
ROOT=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(name):return json.loads((ROOT/name).read_text('utf-8'))
def main():
    manifest=read('SHA256SUMS.json')
    for name,h in manifest.items():
        p=(ROOT/name).resolve();assert p.is_relative_to(ROOT) and p.is_file() and sha(p)==h,name
    build=read('reports/native/RESULT.json');assert build['accepted'] and not build['reused_synthesis']
    assert build['clock_target_mhz']==50 and build['final_fmax_mhz']>=50
    assert build['setup_violated_endpoints']==build['hold_violated_endpoints']==0
    assert sha(ROOT/'firmware/J280_DS13_S1_LIVE.fs')==build['bitstream_sha256']
    for name,h in build['source_sha256'].items():assert sha(ROOT/'V5/project'/name)==h,name
    for name,h in build['evidence_sha256'].items():assert sha(ROOT/'reports/native'/name)==h,name
    proof=read('reports/system_test.json');assert proof['passed']
    for name,h in proof['source_sha256'].items():assert sha(ROOT/'V5/project/src'/name)==h,name
    for name,h in proof['test_sha256'].items():assert sha(ROOT/'tests'/name)==h,name
    balance=read('reports/balance_ds13.json');assert balance['passed']
    for name,h in balance['source_sha256']['candidate'].items():assert sha(ROOT/'V5/project/src'/name)==h,name
    proof=read('reports/live_planner.json');assert proof['passed']
    for name,h in proof['input_sha256'].items():assert sha(ROOT/name)==h,name
    proof=read('reports/host_evidence.json');assert proof['passed']
    for name,h in proof['input_sha256'].items():assert sha(ROOT/name)==h,name
    for name,h in proof['evidence_sha256'].items():assert sha(ROOT/name)==h,name
    for report,key in (('reports/R2_STREAM_CHECK.json','sources'),('reports/R2_GUI_LOAD_CHECK.json','source_sha256')):
        r2=read(report);assert r2['passed'] and r2['hardware_actions']==0
        for name,h in r2[key].items():assert sha(ROOT/name)==h,name
    entry=read('reports/TEAM_ENTRY_CHECK.json');assert entry['passed']
    assert sha(ROOT/'launch_gui.py')==entry['source_sha256']
    for name,h in read('SOURCE_PROVENANCE.json')['balance_files_unchanged'].items():assert sha(ROOT/'V5/project/src'/name)==h,name
    p=read('S1参数.json');assert p['firmware_id']=='44533133' and p['schema']=='44535002'
    assert struct.unpack('<6I',struct.pack('<6f',*p['gains']))==tuple(int(v,16) for v in p['ieee754_hex'])
    print('PASS: %d files; original DS13 50MHz bitstream; R2 host checks; exact S1; all sources and evidence bound.'%len(manifest))
if __name__=='__main__':main()
