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
    if field not in (0,1,2,3,7):raise ValueError('未知上传字段')
    return value

def await_ack(owner,ticket,cancel,timeout=1.4):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if cancel.is_set():raise RuntimeError('上传已取消；不会自动重发或播放')
        with owner.state.lock:
            result=owner.state.command_results.pop(ticket,None)
            connected=owner.state.connected;error=owner.state.error
        if result is not None:
            if result['status']!=0:raise RuntimeError(f"设备拒绝上传请求，状态{result['status']}")
            return result
        if not connected or error:raise RuntimeError(error or '连接已中断')
        cancel.wait(.005)
    raise TimeoutError('上传应答结果未知；已停止，不会重发')

def upload(owner,points,cancel,progress=lambda current,total:None,*,tracking_mode=0):
    from protocol import STATUS,WRITE_CONFIG,READ_CONFIG
    points=validate(points);expected=checksum(points,tracking_mode)
    def command(opcode,group=0,payload=b''):
        if cancel.is_set():raise RuntimeError('上传已取消')
        ticket=owner.command(opcode,group,payload,source='DRAW_UPLOAD')
        return await_ack(owner,ticket,cancel)
    # Only source identity/ACK and the RAM ownership predicate govern data
    # staging. Downward pose, STOP and a latched motor fault never enable
    # motion and must not block writing an independent finite program.
    first=command(STATUS)
    if first['mode'] not in (0,1,2,3,4):
        raise RuntimeError('当前固件状态不支持轨迹暂存；没有写入或启动运动')
    stopped=first['mode']!=3
    # Existing finite motion returns to its origin on its own. Waiting avoids
    # cancelling near a peak where a new origin would fail the travel guard.
    deadline=time.monotonic()+150
    while True:
        status=command(STATUS)
        if status['mode'] not in (0,1,2,3,4):
            raise RuntimeError('固件状态不支持轨迹暂存；未核验成功，不会播放')
        ready=command(READ_CONFIG,252,b'\x06')['config_read_value']
        if ready==1:break
        if time.monotonic()>=deadline:raise TimeoutError('当前运动或校零仍占用轨迹缓冲；未写入，不强制停止')
        if cancel.wait(.1):raise RuntimeError('上传已取消')
    def write(field,value):
        try:return command(WRITE_CONFIG,252,bytes((field,))+struct.pack('<I',value))
        except Exception as exc:
            label={0:'BEGIN',1:f'POINT[{value>>16}]',2:'COMMIT',7:'TRACKING_MODE'}[field]
            raise RuntimeError(f'上传{label}未完成：{exc}；未核验，不会播放。请查看设备状态；不会自动重发，也无需为上传扶直或RUN。') from exc
    write(7,tracking_mode)
    write(0,len(points))
    for index,point in enumerate(points):
        write(1,(index<<16)|(point&0xffff));progress(index+1,len(points))
    write(2,expected)
    reads={field:command(READ_CONFIG,252,bytes((field,)))['config_read_value'] for field in (0,1,2,3,4,7)}
    if reads!={0:len(points),1:len(points),2:expected,3:1,4:BUFFER_SIGNATURE,7:tracking_mode}:
        raise RuntimeError('设备独立读回不匹配；不会宣布上传成功或自动播放')
    return dict(points=len(points),duration_ms=(len(points)-3)*SAMPLE_MS*(2 if tracking_mode else 1),checksum=expected,tracking_mode=tracking_mode,uploaded_while_stopped=stopped)
