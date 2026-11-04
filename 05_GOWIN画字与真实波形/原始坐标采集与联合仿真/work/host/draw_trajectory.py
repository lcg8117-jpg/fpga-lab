"""Finite preparation and acknowledged staging while the PID keeps balance."""
import math
import struct
import time

MAX_POINTS=512
SAMPLE_MS=100
MAX_POSITION=3072
COUNTS_SCALE=16
FORMAT='J280DrawC4Q4'
BUFFER_SIGNATURE=0x44524334
MAX_TRAVEL=1040
SEED=0x6A09E667

def checksum(points,tracking_mode=0):
    if type(tracking_mode) is not int or tracking_mode not in (0,1):raise ValueError('未知轨迹跟踪模式')
    result=SEED^len(points)^tracking_mode
    for index,point in enumerate(points):
        result=((result<<1)|(result>>31))&0xffffffff
        result^=(index<<16)|(point&0xffff)
    return result

def validate(points):
    if not isinstance(points,(list,tuple)) or not 8<=len(points)<=MAX_POINTS:
        raise ValueError('C4轨迹须为8～512个100ms的Q4整数节点')
    previous=0;prior_delta=0;prior_second=0;travel=0
    for point in points:
        if type(point) is not int or abs(point)>MAX_POSITION:raise ValueError('C4节点超出±192计数')
        delta=point-previous;second=delta-prior_delta;third=second-prior_second;travel+=abs(delta)
        if abs(delta)>96 or abs(second)>32 or abs(third)>16:raise ValueError('C4节点不满足速度/加速度/jerk边界')
        if travel>MAX_TRAVEL*16:raise ValueError('目标累计路程超过1圈Forest计数')
        previous=point;prior_delta=delta;prior_second=second
    if any(points[:4]) or any(points[-4:]):raise ValueError('C4轨迹首尾各需4个零节点')
    return list(points)

def smooth_input(values,passes=8):
    values=list(values)
    for _ in range(passes):
        values=[sum(w*values[max(0,min(len(values)-1,i+j-2))] for j,w in enumerate((1,4,6,4,1)))/16 for i in range(len(values))]
    result=[round(v*16)for v in values];result[:4]=[0]*4;result[-4:]=[0]*4
    return validate(result)

def clocked_curve(points,tracking_mode=0):
    # Exact integer oracle for the RTL constant-divider recurrence. Returned
    # values are planned Q16 references, never measured encoder samples.
    validate(points);checksum(points,tracking_mode);out=[0];steps=40 if tracking_mode else 20
    for k in range(len(points)-3):
        p0,p1,p2,p3=points[k:k+4]
        c0=(p0+4*p1+p2)*steps**3;c1=(-3*p0+3*p2)*steps**2
        c2=(3*p0-6*p1+3*p2)*steps;c3=-p0+3*p1-3*p2+p3
        denominator=6*steps**3*16
        for j in range(1,steps+1):
            n=c0+c1*j+c2*j*j+c3*j*j*j
            q=(abs(n)*65536+denominator//2)//denominator;out.append(-q if n<0 else q)
    return out

def prepare(raw):
    if not isinstance(raw,(list,tuple)) or not 1<=len(raw)<=MAX_POINTS:raise ValueError('记录为空或超长')
    if any(type(v)not in(int,float)or not math.isfinite(v)or abs(v)>192 for v in raw):raise ValueError('记录超出±192计数或包含非有限值')
    # Conservative input retiming precedes spline validation; no output filter.
    values=[0.]*20;current=0.
    for target in [*raw,0.]:
        while abs(target-current)>.001:
            current+=max(-2,min(2,target-current));values.append(current)
            if len(values)>492:raise ValueError('重定时后超出节点容量，请缩短记录')
        values.extend([current]*2)
    values.extend([0.]*20)
    return smooth_input(values)

def upload_word(field,value):
    if type(value) is not int or not 0<=value<=0xffffffff:raise ValueError('上传字须为32位无符号整数')
    if field==0 and not 8<=value<=MAX_POINTS:raise ValueError('点数越界')
    if field==1:
        point=struct.unpack('<h',struct.pack('<H',value&0xffff))[0]
        if value>>25 or abs(point)>MAX_POSITION:raise ValueError('点索引保留位或位置越界')
    if field==3 and value!=0:raise ValueError('ABORT值必须为0')
    if field==7 and value not in (0,1):raise ValueError('未知轨迹跟踪模式')
    if field==8 and value not in (0,1,2):raise ValueError('未知存储选择')
    if field not in (0,1,2,3,7,8):raise ValueError('未知上传字段')
    return value

def await_ack(owner,ticket,cancel,timeout=1.4,*,return_rejected=False):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if cancel.is_set():raise RuntimeError('上传已取消；不会自动重发或播放')
        with owner.state.lock:
            result=owner.state.command_results.pop(ticket,None)
            connected=owner.state.connected;error=owner.state.error
        if result is not None:
            if result['status']!=0 and not return_rejected:raise RuntimeError(f"设备拒绝上传请求，状态{result['status']}")
            return result
        if not connected or error:raise RuntimeError(error or '连接已中断')
        cancel.wait(.005)
    raise TimeoutError('上传应答结果未知；已停止，不会重发')

def _session_stamp(owner):
    """Read only the small identity/invalidation state, never trace histories."""
    with owner.state.lock:
        state=owner.state
        if not state.connected or not state.authenticated or state.error or state.log_error:
            raise RuntimeError(state.error or state.log_error or '当前连接身份未核验；请重新连接后明确准备')
        return (owner.logical_session,state.board_resets,state.ram_generation)


def grant_current(owner,grant,*,allow_pending=False):
    """A local grant only gates UI; every manual PLAY still reads RAM afresh."""
    if owner is None or not grant:return False
    try:
        if owner is not grant['owner'] or _session_stamp(owner)!=grant['stamp']:return False
        with owner.state.lock:
            candidates=[(getattr(owner.state,stamp,0),getattr(owner.state,name,None))
                        for name,stamp in (('last_ack','last_ack_rx'),('last_control','last_control_rx'),('latest','latest_rx'))]
            latest=max(candidates,key=lambda pair:pair[0])[1]
            return bool(latest and latest.get('config_revision')==grant['revision'] and
                        (allow_pending or not owner.state.pending_mutations))
    except (RuntimeError,AttributeError,KeyError):return False


def upload(owner,points,cancel,progress=lambda current,total:None,*,tracking_mode=0,
           phase=lambda message:None,verify_only=False):
    """Fresh metadata verification, with an acknowledged upload only on mismatch.

    The firmware exposes a 32-bit rotate/XOR checksum, not a cryptographic
    digest or point-by-point RAM reads. Matching metadata is the protocol's
    reuse boundary. Revision/epoch checks reject observed concurrent changes;
    the protocol does not provide an atomic snapshot across other transports.
    """
    from protocol import STATUS,WRITE_CONFIG,READ_CONFIG,FIRMWARE,WIRE_VERSIONS
    started=time.monotonic();points=validate(points);expected=checksum(points,tracking_mode)
    stamp=_session_stamp(owner);last_epoch=None;last_request=None
    def command(opcode,group=0,payload=b''):
        nonlocal stamp,last_epoch,last_request
        # Serialize the check and enqueue with STOP and other UI mutations.
        with owner.command_lock:
            if cancel.is_set():raise RuntimeError('准备已取消；不会自动重发或播放')
            if _session_stamp(owner)!=stamp:raise RuntimeError('连接、复位或缓冲区事务已变化；请重新明确准备')
            ticket=owner.command(opcode,group,payload,source='DRAW_UPLOAD')
            next_stamp=_session_stamp(owner)
            expected_stamp=(*stamp[:2],stamp[2]+int(opcode==WRITE_CONFIG and group==252))
            if next_stamp!=expected_stamp:raise RuntimeError('准备期间出现其他缓冲区事务；不继续写入或播放')
            stamp=next_stamp
        ack=await_ack(owner,ticket,cancel)
        if cancel.is_set() or _session_stamp(owner)!=stamp:raise RuntimeError('准备已取消或连接/缓冲区事务变化')
        # SerialOwner maps a queue ticket to a strictly matching wire RID.
        # Never assume ticket == RID: internal heartbeats consume RIDs too.
        rid=ack.get('request_id');epoch=ack.get('control_epoch')
        if (type(rid) is not int or rid<=0 or (last_request is not None and rid<=last_request) or
            ack.get('opcode')!=opcode):raise RuntimeError('上传应答请求ID或操作码不匹配；不继续写入或播放')
        if (ack.get('variant'),ack.get('firmware_id'),ack.get('wire_version'))!=(5,FIRMWARE[5],WIRE_VERSIONS[5]):
            raise RuntimeError('准备应答固件身份不匹配')
        if opcode in (READ_CONFIG,WRITE_CONFIG) and (ack.get('config_group')!=group or ack.get('config_id')!=payload[0]):
            raise RuntimeError('上传或独立读回应答组或字段不匹配；不宣布核验成功')
        if (type(epoch) is not int or (last_epoch is not None and ((epoch-last_epoch)&0xffffffff)>=0x80000000) or
            type(ack.get('config_revision')) is not int):raise RuntimeError('读回时序或配置版本未知/倒退；不继续准备')
        if ack['mode'] not in (0,1,2,3,4):raise RuntimeError('固件状态不支持轨迹暂存')
        last_epoch=epoch;last_request=rid
        return ack
    first=command(STATUS);stopped=first['mode']!=3
    with owner.state.lock:
        if owner.state.pending_mutations:raise RuntimeError('存在未决写事务；请先确认结果再准备')
    phase('核验已有板内轨迹')
    deadline=time.monotonic()+150
    while True:
        ready=command(READ_CONFIG,252,b'\x06')
        if ready['config_read_value']==1:break
        if verify_only:raise RuntimeError('轨迹RAM尚被运动或校零占用；不会PLAY')
        if time.monotonic()>=deadline:raise TimeoutError('当前运动或校零仍占用轨迹缓冲；未写入，不强制停止')
        phase('等待当前运动或校零释放RAM；不强制停止')
        if cancel.wait(.1):raise RuntimeError('准备已取消')
        command(STATUS)
    expected_reads={0:len(points),1:len(points),2:expected,3:1,4:BUFFER_SIGNATURE,7:tracking_mode,8:0}
    def read_fingerprint(revision):
        reads={}
        for field in expected_reads:
            ack=command(READ_CONFIG,252,bytes((field,)))
            if ack['config_revision']!=revision:raise RuntimeError('独立读回期间配置版本变化；不复用、不重传、不播放')
            reads[field]=ack['config_read_value']
        return reads
    revision=ready['config_revision'];reads=read_fingerprint(revision)
    reused=reads==expected_reads
    if not reused:
        if verify_only:raise RuntimeError('设备独立读回与当前计划不匹配；请重新明确准备，不会PLAY')
        if reads[4]!=BUFFER_SIGNATURE:raise RuntimeError('轨迹RAM签名不匹配；不写入')
        phase('已有轨迹不匹配，开始逐点上传')
        def write(field,value):
            nonlocal revision
            try:
                ack=command(WRITE_CONFIG,252,bytes((field,))+struct.pack('<I',value))
                if ack['config_revision']!=((revision+1)&0xffffffff):raise RuntimeError('上传期间配置版本出现未预期变更')
                revision=ack['config_revision'];return ack
            except Exception as exc:
                label={0:'BEGIN',1:f'POINT[{value>>16}]',2:'COMMIT',7:'TRACKING_MODE',8:'CUSTOM_RAM_SELECT'}[field]
                raise RuntimeError(f'上传{label}未完成：{exc}；未核验，不会播放，也不会自动重发。') from exc
        write(8,0);write(7,tracking_mode);write(0,len(points))
        for index,point in enumerate(points):
            write(1,(index<<16)|(point&0xffff));progress(index+1,len(points))
        write(2,expected);phase('COMMIT已确认，正在独立读回7项元数据')
        if read_fingerprint(revision)!=expected_reads:raise RuntimeError('设备独立读回不匹配；不会宣布上传成功或自动播放')
    if cancel.is_set() or _session_stamp(owner)!=stamp:raise RuntimeError('准备已取消或资格失效')
    grant=dict(owner=owner,stamp=stamp,revision=revision)
    if not grant_current(owner,grant):raise RuntimeError('核验后状态已变化；请重新明确准备')
    return dict(points=len(points),duration_ms=(len(points)-3)*SAMPLE_MS*(2 if tracking_mode else 1),checksum=expected,
                tracking_mode=tracking_mode,uploaded_while_stopped=stopped,reused=reused,
                elapsed_s=time.monotonic()-started,grant=grant)
