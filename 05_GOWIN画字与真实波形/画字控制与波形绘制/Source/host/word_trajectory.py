"""Finite D1 word motion INPUT. Rendering uses only measured encoder data.

All coordinates are Forest counts (1040/revolution). No PID writes, motion,
transport access, learning, plant model or output-font lookup in this module.
"""
import json
import math
import bisect
from pathlib import Path
from draw_trajectory import validate, checksum

DT = .1
COUNTS_PER_REV = 1040
MAX_DURATION = 102.


def _interpolate(values, at):
    at = max(0., min(len(values)-1., at))
    i = min(len(values)-2, int(at)); fraction = at-i
    return values[i]*(1-fraction)+values[i+1]*fraction


def _bridge(a, b, ticks):
    # C2 input transitions; never applied to encoder/PWM output.
    return [a+(b-a)*(10*z**3-15*z**4+6*z**5)
            for z in (i/ticks for i in range(ticks))]


def _retime_pair(x, past, ticks):
    """One common input clock, sized by the larger of the two arm motions.

    This changes the commanded time allocation, not any measured coordinate.
    Both paths retain the same geometric parameter and the same total time.
    """
    arc=[0.]
    for a,b,c,d in zip(x,x[1:],past,past[1:]):
        arc.append(arc[-1]+max(abs(b-a),abs(d-c)))
    result_x=[];result_past=[]
    for k in range(ticks+1):
        value=arc[-1]*k/ticks
        i=min(len(arc)-2,max(0,bisect.bisect_right(arc,value)-1))
        q=(value-arc[i])/(arc[i+1]-arc[i]) if arc[i+1]!=arc[i] else 0.
        result_x.append(x[i]+q*(x[i+1]-x[i]))
        result_past.append(past[i]+q*(past[i+1]-past[i]))
    return result_x,result_past


def _quantize_input(values, passes):
    # Preserve C4's declared INPUT convolution, then choose Q4 rounding under
    # the original speed/acceleration/jerk guards. No output filter is used.
    from draw_trajectory import validate
    values=list(values)
    for _ in range(passes):
        values=[sum(w*values[max(0,min(len(values)-1,i+j-2))]for j,w in enumerate((1,4,6,4,1)))/16 for i in range(len(values))]
    costs={(0,0,0):0.};parents=[]
    for i,target in enumerate(values):
        target*=16
        candidates=[0] if i<4 or i>=len(values)-4 else range(math.ceil(target-2),math.floor(target+2)+1)
        updated={};back={}
        for (position,delta,second),cost in costs.items():
            for point in candidates:
                change=point-position;dd=change-delta;ddd=dd-second
                if abs(point)>3072 or abs(change)>96 or abs(dd)>32 or abs(ddd)>16:continue
                state=(point,change,dd);score=cost+(point-target)**2
                if score<updated.get(state,float('inf')):
                    updated[state]=score;back[state]=(position,delta,second)
        if not updated:raise ValueError('固定时长内不能满足原Q4运动约束')
        # At most a small fixed number of nearby rounding states are kept.
        best=sorted(updated,key=updated.get)[:256]
        costs={k:updated[k]for k in best};parents.append({k:back[k]for k in best})
    state=min(costs,key=costs.get);points=[]
    for back in reversed(parents):points.append(state[0]);state=back[state]
    return validate(list(reversed(points)))


def _integer_program(planned, tolerance=2.):
    """Whole-program integer projection, <=2 counts from requested input.

    Search only integer position/slope states, enforce D1 step/acceleration
    at every point and require final zero/zero-slope. No physical feedback.
    """
    costs={(0,0):0.}; parents=[]
    for i,target in enumerate(planned):
        candidates=(0,) if i==0 or i>=len(planned)-2 else range(
            max(-192,math.ceil(target-tolerance)),min(192,math.floor(target+tolerance))+1)
        next_cost={}; back={}
        for (position,delta),cost in costs.items():
            for point in candidates:
                change=point-position
                if abs(change)>6 or abs(change-delta)>2:continue
                state=(point,change);score=cost+(point-target)**2+.001*change*change
                if score<next_cost.get(state,float('inf')):
                    next_cost[state]=score;back[state]=(position,delta)
        if not next_cost:raise ValueError('固定时长内整数轨迹不可行')
        costs=next_cost;parents.append(back)
    state=(0,0)
    if state not in costs:raise ValueError('无法在固定末段回零停稳')
    points=[]
    for back in reversed(parents):points.append(state[0]);state=back[state]
    return list(reversed(points))


def _easy_start_plan():
    """Load the MATLAB-authored input knots, then recompute their exact preview."""
    from draw_trajectory import clocked_curve,FORMAT
    data=json.loads(Path(__file__).with_name('easy_start_plan.json').read_text(encoding='utf8'))
    points=validate(data['points']);m=data['word_metadata']
    if data.get('format')!=FORMAT or data.get('tracking_mode')!=1 or m.get('font_revision')!='EasyStart_SingleStroke_R3':
        raise ValueError('EasyStart输入字形版本不匹配')
    if checksum(points,1)!=m['checksum'] or (len(points)-3)*.2!=m['total_s']:
        raise ValueError('EasyStart输入校验或时长不匹配')
    q=clocked_curve(points,1);start=m['word_start_ms']//5;end=m['word_end_ms']//5;lag=m['window_ms']//5
    preview=[(q[i]/65536,(q[i]-q[i-lag])/65536*2*math.pi/1040/(m['window_ms']/1000))for i in range(start,end)]
    return dict(format=FORMAT,sample_ms=100,tracking_mode=1,points=points,word_metadata=m,target_only_xy=preview)


def generate(word):
    """C6 half-rate input plan:108-count width and explicit tracking-mode CRC.

    Declared INPUT convolution, followed by a clocked C2 spline.
    Measured output is never smoothed or remapped to font coordinates.
    """
    from draw_trajectory import smooth_input,clocked_curve,FORMAT
    if word not in ('GOWIN','EasyStart'):raise ValueError('仅支持 GOWIN / EasyStart')
    if word=='EasyStart':return _easy_start_plan()
    source=json.loads(Path(__file__).with_name('word_paths.json').read_text(encoding='utf8'))[word]
    ticks=180 if word=='GOWIN'else 200
    seek,gap,offset=(25,20,0.) if word=='GOWIN' else (21,24,10.)
    ret=25;lag=ticks+gap
    oldx=[_interpolate(source['x'],k/2000*(len(source['x'])-1)) for k in range(2001)]
    oldpast=[_interpolate(source['past'],k/2000*(len(source['past'])-1)) for k in range(2001)]
    x=[v*108/480+offset for v in oldx]
    vertical=97.2*lag/(ticks+20)
    past=[x[k]+(oldpast[k]-oldx[k])*vertical/480 for k in range(2001)]
    x,past=_retime_pair(x,past,ticks)
    planned=[0.]*4+_bridge(0,past[0],seek)+past[:-1]+_bridge(past[-1],x[0],gap)+x[:-1]+_bridge(x[-1],0,ret)+[0.]*4
    passes=8 if word=='GOWIN'else 4
    points=_quantize_input(planned,passes);q=clocked_curve(points,tracking_mode=1)
    if (len(points)-3)*DT*2>MAX_DURATION:raise ValueError('本字的慢速轨迹超过96秒，未生成')
    start=(4+seek+ticks+gap-1)*200;end=start+ticks*200;window=lag*200
    ideal=[(q[i]/65536,(q[i]-q[i-window//5])/65536*2*math.pi/1040/(window/1000))for i in range(start//5,end//5)]
    metadata=dict(word=word,format='J280WordC6',sample_ms=100,total_s=(len(points)-3)/5,
        pre_motion_s=start/1000,word_s=ticks/5,window_ms=window,word_start_ms=start,word_end_ms=end,
        span_count=108,vertical_encoding_scale_vs_W1=1.62,previous_W1_span_count=60,
        input_time_allocation='shared maximum arm-distance clock; slow36/40s writing',
        previous_C4_span_count=80,pre_motion_wait_increased=True,nominal_position_offset_count=offset,
        executed_reference_span_count=max(v[0]for v in ideal)-min(v[0]for v in ideal),
        travel_count=sum(abs(b-a)for a,b in zip(points,points[1:]))/16,
        checksum=checksum(points,1),point_count=len(points),coordinate_scale=16,
        axes='X=actual Forest ENC; Y=(ENC(t)-ENC(t-window))*2*pi/1040/window_s',
        evidence='Clocked reference plan only; mechanical following remains unverified',
        pid_modified=False,output_smoothing=False,input_binomial_passes=passes,
        input_interpolation='uniform cubic B-spline,40 samples per200ms segment',
        tracking_mode=1,playback_scale=2,playback_sample_ms=200,
        max_integer_projection_error_count=1.,integer_projection_bound_count=1)
    return dict(format=FORMAT,sample_ms=100,tracking_mode=1,points=points,word_metadata=metadata,target_only_xy=ideal)


def measured_trace(rows, metadata, ack_epoch, *, origin_ms=None):
    """Causal exact board-time pairs, with gaps and unsafe intervals broken.

    Rows: epoch, board_ms, actualENC, motion_state, mode, fault, permission.
    Onset is first observed WAVE after accepted play ACK, uncertainty <=5ms
    with complete 200Hz data. The average uses two exact encoder endpoints;
    an unrelated lost interior sample does not destroy those endpoints.
    Missing endpoints and gaps in the plotted samples remain explicit gaps.
    """
    eligible=[r for r in rows if 0<((r[0]-ack_epoch)&0xffffffff)<0x80000000]
    first=next((i for i,r in enumerate(eligible) if r[3]==6),None)
    if origin_ms is None and first is None:return []
    start_ms=eligible[first][1] if origin_ms is None else origin_ms
    lag=metadata['window_ms'];samples={};out=[];prior=None
    for r in eligible[first if first is not None else 0:]:
        dt=((r[1]-start_ms)&0xffffffff)
        if dt>=0x80000000:break
        if r[4]!=3 or r[5] or not r[6]:break
        if dt<metadata['word_end_ms'] and r[3]!=6:break
        if prior is not None:
            time_step=(r[1]-prior[1])&0xffffffff;epoch_step=(r[0]-prior[0])&0xffffffff
            if not 0<time_step<0x80000000 or not 0<epoch_step<0x80000000:
                break
            if (time_step!=5 or epoch_step!=1) and metadata['word_start_ms']<=dt<metadata['word_end_ms']:
                out.append(None)
        samples[dt]=r[2]
        if metadata['word_start_ms']<=dt<metadata['word_end_ms']:
            if dt-lag in samples:
                out.append((dt,r[2],(r[2]-samples[dt-lag])*2*math.pi/1040/(lag/1000)))
            else:out.append(None)
        prior=r
    return out
