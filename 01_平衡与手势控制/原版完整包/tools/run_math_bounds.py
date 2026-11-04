"""Analytical bounds for all11525 permitted nominal-turn parameter combinations.

This is not11525 RTL simulations. Separate finite RTL tests cover8 profiles.
"""
from pathlib import Path
import hashlib,json
r=Path(__file__).resolve().parents[1];source=r/'work/project/src/forest_motion_finite_scurve.v'
count=0;maximum_weight=0;maximum_ticks=0
ceil=lambda a,b:(a+b-1)//b
for cps in range(60,521):
    v=170394 if cps==520 else (cps*335544)>>10
    for a in [131,262,524,1049,1966]:
        for j in [3,7,13,26,98]:
            ramp=ceil(a,j);middle=max(ramp,ceil(v,a));accel=ramp+middle
            denominator=max(accel,ceil(1040*65536,v));weight=ramp*middle*denominator
            assert 0<weight<2**31 and denominator>=accel and middle>=ramp
            assert ceil(1040*65536,denominator)<=v
            assert ceil(1040*65536,middle*denominator)<=a
            assert ceil(1040*65536,weight)<=j
            maximum_weight=max(maximum_weight,weight);maximum_ticks=max(maximum_ticks,denominator+accel);count+=1
assert count==11525
proof=dict(passed=True,parameter_combinations=count,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
           maximum_weight=maximum_weight,maximum_nominal_ticks=maximum_ticks,
           scope='Analytical integer bounds only;8 separately simulated finite RTL profiles. Added FPGA pipeline clocks do not change tick-domain equations.',hardware_actions=0)
(r/'reports/FINITE_PLAN_BOUNDS.json').write_text(json.dumps(proof,indent=2)+'\n','utf-8')
print(json.dumps(proof))
