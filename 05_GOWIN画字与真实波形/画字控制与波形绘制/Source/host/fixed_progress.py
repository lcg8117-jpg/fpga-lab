"""Observed encoder span for one matched fixed job; never a target estimate."""
class FixedSpan:
    def __init__(self):
        self.key=None;self.start_ms=None;self.low=None;self.high=None;self.last_epoch=None

    def update(self,state):
        d=state.get('latest_diagnostic')
        if not d or d.get('motion_action')not in (0x23,0x24):return self.text()
        key=(state.get('board_resets',0),d['motion_request_id'],d['motion_action'])
        if key!=self.key:
            self.key=key;self.start_ms=d['board_ms'];self.low=self.high=self.last_epoch=None
        for row in state.get('history',[]):
            epoch,ms,enc=row[0],row[6],row[4]
            if ((ms-self.start_ms)&0xffffffff)>=0x80000000:continue
            if self.last_epoch is not None and not 0<((epoch-self.last_epoch)&0xffffffff)<0x80000000:continue
            self.low=enc if self.low is None else min(self.low,enc)
            self.high=enc if self.high is None else max(self.high,enc);self.last_epoch=epoch
        return self.text()

    def text(self):
        if self.low is None:return '实际往返跨度：等待本次固定轨迹的真实ENC数据'
        degrees=(self.high-self.low)*360/1040
        verdict='已观察到至少270°' if degrees>=270 else '尚未观察到270°'
        return f'实际ENC范围 {self.low}～{self.high}计数；已记录往返跨度 {degrees:.2f}°，{verdict}。这不是目标幅度。'
