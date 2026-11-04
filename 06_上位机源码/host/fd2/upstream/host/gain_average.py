"""Bounded observation of actual control gains; no control or transport output."""
from collections import deque
import math


def new_state():
    return dict(points=deque(maxlen=1002),signature=None,reason='awaiting_control',last_rx=0.0)


def clear(state,reason):
    state['points'].clear();state['signature']=None
    state['reason']=reason;state['last_rx']=0.0


def observe(state,item,now):
    flags=item['flags']
    reason=('not_coherent' if not item['coherent'] else 'fault' if item['fault'] else
            'stopped' if item['mode']!=3 or not flags&2 else
            'motor_inhibited' if not flags&4 else
            'adaptive_disabled' if not item['adaptive_enabled_at_launch'] else None)
    if reason:
        clear(state,reason);return
    active=item['active_gains'];base=item['manual_gains']
    values=(active[0],active[2],base[0],base[2])
    if any(not math.isfinite(value) or value<0 for value in values):
        clear(state,'invalid_gains');return
    signature=(tuple(base),item['stored_revision'])
    points=state['points'];epoch=item['control_epoch'];board=item['board_ms']
    elapsed=0
    if points:
        prior=points[-1]
        if signature!=state['signature']:
            clear(state,'manual_base_changed')
        elif epoch==prior[2] and board==prior[1]:
            # Replayed frames cannot extend coverage or refresh freshness.
            return
        elif epoch<=prior[2]:
            clear(state,'epoch_reset')
        elif epoch-prior[2]!=1 or ((board-prior[1])&0xffffffff)!=5:
            clear(state,'sample_gap')
        else:
            elapsed=prior[0]+5
    points.append((elapsed,board,epoch,*values,bool(item['adaptive_used_at_launch'])))
    state['signature']=signature;state['last_rx']=now
    while len(points)>2 and points[1][0]<=elapsed-5000:points.popleft()


def summarize(state,now,control_fresh):
    points=state['points']
    if not control_fresh:
        return dict(valid=False,reason='control_expired',windows=[])
    if not points:return dict(valid=False,reason=state['reason'],windows=[])
    if not state['last_rx'] or now-state['last_rx']>0.5:
        return dict(valid=False,reason='control_expired',windows=[])
    if len(points)<2:
        return dict(valid=False,reason=state['reason'] if not points else 'insufficient_duration',windows=[])
    windows=[];end=points[-1][0]
    for duration in (1000,5000):
        start=max(points[0][0],end-duration);covered=end-start
        totals=[0.0]*4;adopted_ms=0;intervals=0
        for left,right in zip(points,list(points)[1:]):
            width=right[0]-max(left[0],start)
            if width<=0:continue
            for index,value in enumerate(left[3:7]):totals[index]+=value*width
            adopted_ms+=width*left[7];intervals+=1
        means=[value/covered for value in totals]
        windows.append(dict(window_ms=duration,covered_ms=covered,intervals=intervals,
            complete=covered==duration,kp=means[0],kd=means[1],base_kp=means[2],base_kd=means[3],
            kp_change_percent=(means[0]/means[2]-1)*100 if means[2] else None,
            kd_change_percent=(means[1]/means[3]-1)*100 if means[3] else None,
            adopted_percent=100*adopted_ms/covered))
    return dict(valid=True,reason=state['reason'],windows=windows)
