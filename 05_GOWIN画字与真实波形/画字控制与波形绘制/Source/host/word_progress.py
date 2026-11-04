"""Read-only drawing completion evidence, independent of font appearance."""


def progress(rows, metadata, ack_epoch, *, origin_ms=None):
    eligible=[r for r in rows if 0<((r[0]-ack_epoch)&0xffffffff)<0x80000000]
    first=next((i for i,r in enumerate(eligible) if r[3]==6),None)
    if origin_ms is None:
        if first is None:return dict(stage='waiting',text='等待本次板端轨迹开始',origin_ms=None,ended=False)
        origin_ms=eligible[first][1]
    elapsed=0;interrupted=False
    for row in eligible:
        dt=(row[1]-origin_ms)&0xffffffff
        if dt>=0x80000000:continue
        elapsed=max(elapsed,dt)
        if row[4]!=3 or row[5] or not row[6] or (dt<metadata['word_end_ms'] and row[3]!=6):
            interrupted=True;break
    start=metadata['word_start_ms'];end=metadata['word_end_ms']
    if interrupted:stage='interrupted';text='本次运动中断：STOP、故障或输出许可丢失，不能判为成功'
    elif elapsed<start:stage='pre_motion';text=f'先行运动 {elapsed/1000:.1f}/{start/1000:.1f}秒'
    elif elapsed<end:stage='writing';text=f'正在画字 {(elapsed-start)/(end-start)*100:.1f}%'
    else:stage='word_ended';text='书写时段结束，核对真实数据并等待返回本次起点'
    return dict(stage=stage,text=text,origin_ms=origin_ms,elapsed_ms=elapsed,
                ended=elapsed>=end,interrupted=interrupted)


def coverage(trace, metadata):
    expected=(metadata['word_end_ms']-metadata['word_start_ms'])//5
    times=[r[0] for r in trace if r is not None]
    complete=(len(times)==expected and times==list(range(metadata['word_start_ms'],metadata['word_end_ms'],5)))
    return dict(complete=complete,received=len(times),expected=expected,
                meaning='Complete ENC/average-speed data; not proof of readable letters or physical angle accuracy.')


def return_status(state, request_id, ack_epoch, *, now):
    d=state.get('latest_diagnostic');control=state.get('last_control')
    if not state.get('control_fresh') or not control:return 'stale','状态过期，不能确认实际停稳'
    if control['mode']!=3 or control['fault'] or not control['motor_permitted_at_completion']:
        return 'interrupted','已停止或故障，不能判为成功'
    if not d or now-state.get('last_diagnostic_rx',0)>.5:return 'waiting','等待新鲜的板端到位诊断'
    if d.get('motion_request_id')!=request_id:return 'other','诊断属于其他动作，不作本次完成证据'
    distance=(d['last_completed_epoch']-ack_epoch)&0xffffffff
    if distance>=0x80000000:return 'stale','诊断早于本次播放，不作完成证据'
    if d.get('trajectory_done') and d.get('tracking_settled'):
        return 'settled','已回到本次起点并停稳：板端确认连续到位500毫秒；仍保持平衡'
    if d.get('trajectory_done'):return 'settling','参考已回到本次起点，实际尚未连续到位'
    return 'returning','字迹时段结束，仍在返回任务原点'
