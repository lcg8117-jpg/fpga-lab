"""Run the same whole-system comparator against unmodified DS12 as a negative control.

DS12 has no motion_session node. Only that read-only observation is replaced by
constant zero in the TEST WRAPPER. No DS12 module, state, mux or behavior is added.
"""
from pathlib import Path
import hashlib, json, sys
sys.dont_write_bytecode = True
import balance_compare as comparison

original_wrapper = comparison.wrapper
def ds12_wrapper():
    return original_wrapper().replace('candidate.motion_session', "1'b0 /* DS12 node absent: observation only */")

comparison.wrapper = ds12_wrapper
sys.argv = [sys.argv[0], '--candidate', 'D:/Codex_Scratch/J280_LIVE_S1_BUILD_20261103_CHAT/product/V5/project/src', '--label', 'ds12_negative']
try:
    comparison.main()
finally:
    path = comparison.ROOT/'reports/balance_ds12_negative.json'
    if path.exists():
        data=json.loads(path.read_text('utf-8'))
        data['negative_control']=True
        data['missing_probe']='candidate.motion_session does not exist; its wrapper observation is constant zero. No production behavior is changed.'
        data['negative_runner_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        path.write_text(json.dumps(data,ensure_ascii=False,indent=2),'utf-8')
