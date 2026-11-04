"""Both plots use captured FPGA stroke time; actual is always encoder data."""
from collections import deque
DEGREES_PER_COUNT=360/1040
class Timeline:
    def __init__(self):
        self.rows=deque(maxlen=40000);self.job_id=None;self.origin=0;self.last_ms=None
        self.cursor=0.;self.tracking=False;self.phase=0;self.gaps=0;self.state='等待现场绘画'
    def ingest(self,item,received_at=None):
        if item.get('kind')!='telemetry' or not item.get('coherent'):return False
        phase=item['play_state'];job=item['job_id'];stamp=item['board_ms'];self.phase=phase
        if phase in (3,4,6) and job and job!=self.job_id and item['mode']==3 and not item['fault']:
            self.rows.clear();self.job_id=job;self.origin=item['origin'];self.last_ms=None;self.tracking=True;self.gaps=0
        if not self.tracking:return False
        if item['mode']!=3 or item['fault']:
            self.tracking=False;self.state='已停机，保留本次曲线';return False
        if job!=self.job_id or item['origin']!=self.origin:return False
        if self.last_ms is not None:
            delta=(stamp-self.last_ms)&0xffffffff
            if not 0<delta<0x80000000:return False
            gap=delta>7
        else:gap=False
        t=item['play_time_ms']/1000
        if not 0<=t<=165 or (self.rows and t<self.rows[-1]['t']):return False
        self.last_ms=stamp;self.gaps+=int(gap);self.cursor=t
        self.rows.append(dict(t=t,target_deg=item['live_offset']*DEGREES_PER_COUNT,
            actual_deg=(item['wide_encoder']-self.origin)*DEGREES_PER_COUNT,
            reference_deg=(item['wide_target']-self.origin)*DEGREES_PER_COUNT,
            board_ms=stamp,control_epoch=item['control_epoch'],job_id=job,point_sequence=item['point_sequence'],
            raw_encoder=item['physical_encoder_raw'],origin=self.origin,received_at=received_at,gap=gap,play_state=phase))
        self.state={6:'实时跟随中',3:'松手后平滑停止',4:'等待实物停稳',0:'本笔结束，继续平衡'}.get(phase,'保持')
        if phase==0:self.tracking=False
        return True
