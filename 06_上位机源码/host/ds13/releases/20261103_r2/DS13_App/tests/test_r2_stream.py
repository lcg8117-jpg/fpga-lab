"""Offline CRC equivalence and delayed point-ACK regression; never accesses hardware."""
from pathlib import Path
import ast,hashlib,json,random,struct,sys,time
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'host'),str(ROOT/'tests')]
import protocol as p
from test_host import Tests,DelayedSerial
from live_control import LiveStroke

def reference_crc(data):
    value=0xffff
    for byte in data:
        value^=byte<<8
        for _ in range(8):value=((value<<1)^0x1021)&0xffff if value&0x8000 else (value<<1)&0xffff
    return value

class PointDelayedSerial(DelayedSerial):
    def write(self,raw):
        self.delay=.25 if raw[5]==p.LIVE_POINT else .02
        return super().write(raw)

def run_stream(stroke_class,duration):
    check=Tests();fake=PointDelayedSerial(.02);owner=check.owner(fake);stroke=stroke_class(lambda:owner)
    try:
        stroke.begin(0);started=time.perf_counter()
        while time.perf_counter()-started<duration:
            stroke.move(int((time.perf_counter()-started)*90)%160-80);time.sleep(.004)
        points=[r for r in fake.writes if r[5]==p.LIVE_POINT]
        times=[fake.write_times[struct.unpack_from('<I',r,8)[0]] for r in points]
        report=dict(points=len(points),point_interval_max_ms=max((b-a)*1000 for a,b in zip(times,times[1:])),
            lease_expired=fake.lease_expired,pressed=stroke.pressed,error=stroke.error,host_error=owner.state.error,
            unique_request_ids=len({struct.unpack_from('<I',r,8)[0] for r in points})==len(points))
        stroke.end();check.wait_for(lambda:any(r[5]==p.LIVE_END for r in fake.writes),.2)
        end=next(i for i,r in enumerate(fake.writes) if r[5]==p.LIVE_END)
        time.sleep(.28)
        assert all(r[5]!=p.LIVE_POINT for r in fake.writes[end+1:])
        assert all(r[5] in p.READS|p.LIVE_COMMANDS for r in fake.writes)
        return report
    finally:stroke.end();owner.stop(wait=True)

def main():
    rng=random.Random(131302)
    cases=[bytes(rng.randrange(256) for _ in range(rng.randrange(1025))) for _ in range(1000)]
    for data in cases:assert p.crc16(data)==reference_crc(data)
    frame=bytes(range(256));start=time.perf_counter()
    for _ in range(1000):reference_crc(frame)
    before=time.perf_counter()-start;start=time.perf_counter()
    for _ in range(1000):p.crc16(frame)
    after=time.perf_counter()-start
    legacy=ROOT/'tests/fixtures/pre_r2_live_control.py'
    scope={'__name__':'pre_r2_live_control'};exec(compile(legacy.read_text('utf-8'),str(legacy),'exec'),scope)
    negative=run_stream(scope['LiveStroke'],.8)
    assert negative['lease_expired']>0 and not negative['pressed'],negative
    positive=run_stream(LiveStroke,3)
    assert positive['pressed'] and positive['lease_expired']==0 and not positive['error'] and not positive['host_error'],positive
    assert positive['points']>=90 and positive['point_interval_max_ms']<100 and positive['unique_request_ids'],positive
    report=dict(passed=True,hardware_actions=0,scope='Simulated 250ms point ACK while telemetry remains fresh; not a teammate hardware result',
        crc_equivalence_cases=1000,crc_benchmark=dict(reference_seconds=before,current_seconds=after),
        prior_stop_and_wait_expected_failure=negative,current_independent_stream=positive,
        sources={str(pth.relative_to(ROOT)):hashlib.sha256(pth.read_bytes()).hexdigest() for pth in [ROOT/'host/client.py',ROOT/'host/live_control.py',ROOT/'host/protocol.py',Path(__file__),legacy]})
    (ROOT/'reports/R2_STREAM_CHECK.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
