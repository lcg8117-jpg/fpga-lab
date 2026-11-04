"""One-button GOWIN integration. No connection, ZERO, RUN, PID or STOP side effects.

DisplayPipeline is GUI/transport independent. GowinController accepts the existing
SerialOwner-compatible connection; the Tk widget only calls play() and poll().
"""
from pathlib import Path
import hashlib,json,time,threading,queue
from measured_sequence import SequenceTrace
from sequential_glyph import GlyphMapper
from display_origin import sampled_display_origin
from draw_trajectory import validate,checksum,upload,await_ack,grant_current
from protocol import TRAJECTORY,READ_CONFIG,FIRMWARE

PLAN_SHA='ecc254ad5eab7be328e8fd58e2acaeb4c82a6f89b147b5bb344c4cf4ad2eee6c'
PLAN_PATH=Path(__file__).parent/'plans/Trial_Visible_C1.json'

def load_plan():
    raw=PLAN_PATH.read_bytes()
    if len(raw)>4*1024**2 or hashlib.sha256(raw).hexdigest()!=PLAN_SHA:
        raise ValueError('C1候选轨迹文件缺失或改变')
    data=json.loads(raw);points=validate(data['points']);meta=data['word_metadata']
    if len(points)!=403 or checksum(points,1)!=0x5c5f817f:
        raise ValueError('GOWIN节点/CRC不匹配')
    return data

def phases(meta):
    return [dict(name='prelude',start_ms=0,end_ms=meta['word_start_ms']),
            dict(name=meta['word'],start_ms=meta['word_start_ms'],end_ms=meta['word_end_ms']),
            dict(name='tail',start_ms=meta['word_end_ms'],end_ms=round((meta['total_s']+20)*1000))]

class DisplayPipeline:
    """Feed verified actual frames. No timer-generated points or serial access."""
    def __init__(self,manifest):
        self.trace=SequenceTrace(manifest);self.mapper=GlyphMapper(manifest)
        self.pending_break=False

    def feed(self,frame):
        point=self.trace.feed(frame)
        if point is None:return None
        # GlyphMapper detects actual source-index/time gaps itself.
        return self.mapper.feed(point)

    def export(self):
        return dict(format='J280GowinIntegrationV1',manifest=self.trace.manifest,
                    raw_trace=self.trace.export(),mapping=self.mapper.metadata,
                    mapped_points=self.mapper.points,plan_sha256=PLAN_SHA)

def require_ready(state):
    row=state.get('last_control') or {};diag=state.get('latest_diagnostic') or {}
    if not state.get('connected') or not state.get('authenticated'):
        raise RuntimeError('请先通过现有上位机连接设备')
    if state.get('error') or state.get('log_error'):
        raise RuntimeError(str(state.get('error') or state.get('log_error')))
    if not state.get('control_fresh') or (row.get('firmware_id'),row.get('schema'))!=(FIRMWARE[5],0x46443405):
        raise RuntimeError('等待当前固件的新鲜控制数据')
    if row.get('mode')!=3 or row.get('fault') or not row.get('motor_permitted_at_completion'):
        raise RuntimeError('请先扶直，点击校零，再点击开始平衡；稳定后播放')
    if time.monotonic()-state.get('last_diagnostic_rx',0)>.5 or (diag.get('kit'),diag.get('physical_counts_per_rev'),diag.get('forest_counts_per_rev'))!=(1,1040,1040):
        raise RuntimeError('等待本机IP570、1040计数/圈的当前诊断')

class GowinController:
    """Prepare/reuse RAM, read direction, then send one PLAY. Never retry motion.

    owner must expose command(), command_lock, logical_session, state.snapshot(),
    state.lock/command_results and trace(); see INTEGRATION_CN.md for data-only API.
    """
    def __init__(self,owner,*,export_directory=None):
        self.owner=owner;self.plan=load_plan();self.meta=self.plan['word_metadata']
        self.export_directory=Path(export_directory) if export_directory else None
        self.events=queue.SimpleQueue();self.cancel=threading.Event();self.worker=None
        self.busy=False;self.status='就绪：扶直校零、开始平衡，再点击播放运动图像'
        self.pipeline=None;self.ack=None;self.anchor=None;self.play_ms=None
        self.last_epoch=None;self.reset_count=None;self.board_origin=None
        self._saved=False;self.latest_relative_ms=0;self.generation=0

    def play(self):
        if self.busy:return False
        try:require_ready(self.owner.state.snapshot())
        except Exception as exc:self.status=str(exc);return False
        self.busy=True;self.cancel.clear();self.ack=None;self.anchor=None
        self.play_ms=None;self.last_epoch=None;self.board_origin=None
        self.pipeline=None;self._saved=False;self.latest_relative_ms=0;self.generation+=1
        self.reset_count=self.owner.state.snapshot().get('board_resets',0)
        self.status='正在核验板内轨迹；不匹配时才上传'
        def work():
            try:
                result=upload(self.owner,self.plan['points'],self.cancel,tracking_mode=1,
                    progress=lambda a,b:self.events.put(('status',f'准备轨迹 {a}/{b}')),
                    phase=lambda msg:self.events.put(('status',msg)))
                ticket=self.owner.command(READ_CONFIG,253,b'\x06',source='GOWIN_PLAY')
                direction=await_ack(self.owner,ticket,self.cancel)
                if (direction.get('opcode'),direction.get('config_group'),direction.get('config_id'),direction.get('config_read_value'))!=(READ_CONFIG,253,6,1):
                    raise RuntimeError('当前运动方向不是原轨迹的方向1')
                if direction.get('config_revision')!=result['grant']['revision']:
                    raise RuntimeError('准备期间参数已改变；未播放')
                with self.owner.command_lock:
                    require_ready(self.owner.state.snapshot())
                    if self.cancel.is_set() or not grant_current(self.owner,result['grant']):
                        raise RuntimeError('准备资格已失效；未播放')
                    ticket=self.owner.command(TRAJECTORY,payload=b'\x07',source='GOWIN_ONE_BUTTON')
                ack=await_ack(self.owner,ticket,self.cancel)
                if (ack.get('opcode'),ack.get('config_group'),ack.get('config_id'),ack.get('firmware_id'))!=(TRAJECTORY,0,7,FIRMWARE[5]) or not isinstance(ack.get('request_id'),int) or ack['request_id']<=0 or not isinstance(ack.get('control_epoch'),int) or ack['control_epoch']<=0:
                    raise RuntimeError('PLAY应答身份不符；结果保留，不自动重发')
                self.events.put(('played',ack))
            except Exception as exc:self.events.put(('error',str(exc)))
        self.worker=threading.Thread(target=work,name='GowinPreparePlay',daemon=True)
        self.worker.start();return True

    def _finish(self,message):
        self.busy=False;self.status=message
        if self.export_directory and self.pipeline and not self._saved:
            self.export_directory.mkdir(parents=True,exist_ok=True)
            data=self.pipeline.export();data['status_text']=message
            data['firmware_id']=f'{FIRMWARE[5]:08X}'
            raw=json.dumps(data,ensure_ascii=False,separators=(',',':')).encode('utf-8')
            if len(raw)>16*1024**2:raise RuntimeError('单次显示记录超过16MiB')
            name=f'GOWIN_{time.time_ns()}_{self.ack["request_id"]}.json'
            with (self.export_directory/name).open('xb') as f:f.write(raw)
            self._saved=True

    def close(self):
        # The owning application retains motor/STOP authority. This cancels only
        # the local pending preparation and drawing subscription.
        self.cancel.set()
        if self.pipeline:self.pipeline.trace.cancel('display_closed')

    def poll(self):
        out=[]
        while not self.events.empty():
            kind,value=self.events.get()
            if kind=='status':self.status=value
            elif kind=='error':self._finish(value)
            elif kind=='played':self.ack=value;self.status='运动已确认，等待轨迹进入先行段'
        if not self.busy or not self.ack:return out
        state=self.owner.state.snapshot()
        if state.get('board_resets',0)!=self.reset_count:
            if self.pipeline:self.pipeline.trace.cancel('board_reset')
            self._finish('设备复位，本次显示结束');return out
        if not state.get('connected') or not state.get('authenticated') or state.get('error') or state.get('log_error'):
            if self.pipeline:self.pipeline.trace.cancel('connection_interrupted')
            self._finish('连接中断或报错，本次记录已保留');return out
        if not state.get('control_fresh'):
            self.status='等待新鲜采样，暂停画笔';return out
        ack_epoch=self.ack['control_epoch'];rid=self.ack['request_id']
        history=[r for r in state.get('word_history',[]) if 0<((r[0]-ack_epoch)&0xffffffff)<0x80000000]
        diag=state.get('latest_diagnostic') or {}
        diag_fresh=time.monotonic()-state.get('last_diagnostic_rx',0)<=.5
        if self.anchor is None:
            if diag_fresh and diag.get('motion_request_id')==rid and type(diag.get('origin')) is int and 0<=((diag.get('last_completed_epoch',0)-ack_epoch)&0xffffffff)<0x80000000:
                self.board_origin=diag['origin'];self.anchor=dict(basis='matched_diagnostic',origin=diag['origin'],controller_origin_confirmed=True)
            else:self.anchor=sampled_display_origin(state,rid,ack_epoch,self.reset_count)
        terminal=next((r for r in history if r[5] or r[4]!=3 or not r[6]),None)
        wave=next((r for r in history if r[3]==6 and not r[5] and r[4]==3 and r[6]),None)
        if self.play_ms is None and wave:self.play_ms=wave[1]
        if self.pipeline is None and self.anchor is not None and self.play_ms is not None:
            manifest=dict(counts_per_rev=1040,window_ms=self.meta['window_ms'],play_ms=self.play_ms,
                origin=self.anchor['origin'],origin_provenance=self.anchor,request_id=rid,
                session_id=f'gowin_{self.owner.logical_session}_{rid}_{ack_epoch}',source_kind='physical_log',phases=phases(self.meta))
            self.pipeline=DisplayPipeline(manifest)
        if self.pipeline:
            # Post-PLAY diagnostics bind the request. A late controller origin
            # confirms provenance, but never moves already plotted raw samples.
            epoch=diag.get('last_completed_epoch',0)
            if diag_fresh and epoch>=ack_epoch and diag.get('motion_request_id',0)>0:
                if diag['motion_request_id']!=rid or (self.board_origin is not None and diag.get('origin')!=self.board_origin):
                    self.pipeline.trace.cancel('request_or_origin_changed');self._finish('任务身份变化，本次显示结束');return out
                if self.board_origin is None:self.board_origin=diag.get('origin')
            for row in history:
                if row[1]<self.play_ms or (self.last_epoch is not None and row[0]<=self.last_epoch):continue
                if (row[4]!=3 or not row[6]) and not row[5]:
                    self.pipeline.trace.cancel('motor_or_mode_interrupted');break
                if row[1]-self.play_ms<self.meta['word_end_ms'] and row[3]!=6 and not row[5]:
                    self.pipeline.trace.cancel('trajectory_interrupted');break
                p=self.pipeline.feed(dict(board_ms=row[1],control_epoch=row[0],encoder=row[2],session_id=self.pipeline.trace.manifest['session_id'],request_id=rid,fault=row[5],coherent=True))
                self.last_epoch=row[0];self.latest_relative_ms=row[1]-self.play_ms
                if p:out.append(p)
                if row[5]:break
            if terminal:
                self._finish(f'运动中断，故障码{terminal[5]}；已有图形和记录保留')
            elif self.pipeline.trace.status in ('fault','cancelled','invalidated'):
                self._finish('本次采样终止，记录已保留')
            elif self.latest_relative_ms>=round(self.meta['total_s']*1000):
                self._finish('本次GOWIN显示完成；返回和平衡继续由板端执行，实际停稳以板端回传为准')
            else:
                remaining=max(0,(self.meta['word_start_ms']-self.latest_relative_ms)/1000)
                self.status=(f'先行运动，{remaining:.1f}秒后开始字形' if remaining else '正在逐字绘制G → O → W → I → N')
        elif terminal:self._finish(f'进入轨迹前运动中断，故障码{terminal[5]}')
        return out
