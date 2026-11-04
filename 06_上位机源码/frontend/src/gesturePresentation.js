const names = {1:'右滑',2:'左滑',3:'上滑',4:'下滑',5:'靠近',6:'远离',7:'顺时针',8:'逆时针',9:'挥手'}
const actions = {0:'无动作',1:'上滑起摆',2:'相对定位',3:'两秒转圈',4:'取消并保持',5:'挥手 STOP'}
const reasons = {0:'无事件',1:'手势未开启',2:'传感器未就绪或通信错误',3:'起摆期间已忽略',4:'尚未实际平衡接管',
  5:'HOLD 未就绪',6:'轨迹忙，未排队',7:'固件已接受',8:'反向手势已取消参考',9:'保护或起摆资格拒绝',10:'无动作映射',11:'轨迹模块拒绝',12:'等待轨迹模块确认'}

export function gestureGuide(firmware) {
  if (firmware === 'SP24') return {
    items:[{label:'上滑',hint:'请求起摆'},{label:'左／右滑',hint:'平衡后相对定位；反向取消'},
      {label:'顺／逆时针',hint:'正／反一圈，轨迹 2 秒'},{label:'挥手',hint:'平衡后请求停止摆控'}],
    note:'起摆至实际接管完成前，所有手势包括挥手均被忽略。停止请用独立 STOP。挥手约等待 1 秒；轨迹忙时不排队，取消后可用新手势再次启动。',
  }
  return {items:[{label:'右滑',hint:'请求起摆'},{label:'顺／逆时针',hint:'平衡后正／反一圈'},
    {label:'左滑',hint:'取消参考并保持'},{label:'挥手',hint:'请求停止摆控'}],
    note:'挥手识别约等待 1 秒；急停请用独立 STOP。转圈忙时不排队，途中取消后先 STOP 再重新起摆／平衡。'}
}

export function pageFresh(receivedAt, now, statusFresh) {
  const age = now / 1000 - receivedAt
  return Boolean(statusFresh && Number.isFinite(receivedAt) && receivedAt > 0 && age >= 0 && age < 2)
}

export function gesturePresentation(state) {
  const row = state.parameters?.gesture_enable
  const enabled = state.status_fresh && row?.current && [0,1].includes(row.value) ? row.value === 1 : null
  if (state.firmware === 'SP24') {
    const event = state.gesture_event, runtime = state.gesture_runtime
    const currentTime = Number.isFinite(state.presentation_now_ms) ? state.presentation_now_ms : state.observed_at * 1000
    const runtimeFresh = pageFresh(runtime?.received_at, currentTime, state.status_fresh)
    const recognition = event?.event_count > 0 && event.code > 0
      ? `${names[event.code] || `手势 ${event.code}`} · 事件 ${event.event_count} · 板时 ${event.event_ms} ms（保留快照）`
      : '尚无有效手势记录'
    let request = event?.event_count > 0 ? `${actions[event.action] || '未知动作'} · ${reasons[event.reason] || `原因 ${event.reason}`}` : '未报告接受结果'
    if (!state.status_fresh && event) request += '（历史记录）'
    let actual = '无新状态读回'
    if (state.status_fresh) {
      if (state.mode_name === 'FAULT' || state.status?.fault || state.fault) actual = '故障状态'
      else if (state.mode_name === 'SWING' || state.status?.swing_active) actual = '当前正在起摆'
      else if (state.mode_name === 'BALANCE') actual = `当前 BALANCE${runtimeFresh && runtime.fresh_balance_ready && runtime.hold_initialized ? ' · HOLD 就绪' : ' · 等待 HOLD 许可读回'}`
      else if (state.status?.pwm === 0 && state.status?.pwm_zero && !state.status?.run_session) actual = '当前停止已读回'
      else actual = '等待读回动作状态'
    }
    return {enabled,recognition,request,actual,
      sensor:runtimeFresh ? `${runtime.ready ? '就绪' : '未就绪'} · 错误 ${runtime.error ?? '--'}／累计 ${runtime.error_count ?? '--'}` : '无新状态读回',
      handoff:runtimeFresh ? runtime.blocked ? '手势已屏蔽，使用独立 STOP' : runtime.fresh_balance_ready && runtime.hold_initialized ? '实际接管许可已读回' : '等待实际接管与 HOLD 就绪' : '无新状态读回'}
  }
  const gesture = state.gesture
  let recognition = '尚无有效手势记录'
  if (gesture?.error) recognition = `识别异常 · 错误 ${gesture.error}`
  else if (gesture?.sequence > 0 && names[gesture.code]) recognition = `${names[gesture.code]} · 事件 ${gesture.sequence}`
  let request = '未报告接受结果'
  if (gesture?.accepted && gesture?.rejected) request = '接受／拒绝标志冲突'
  else if (gesture?.rejected) request = '固件已拒绝'
  else if (gesture?.accepted) request = '固件已接受'
  if (!state.status_fresh && gesture) request += '（历史记录）'
  let actual = '无新状态读回'
  if (state.status_fresh) {
    if (state.mode_name === 'FAULT' || state.status?.fault || state.fault) actual = '故障状态'
    else if (state.mode_name === 'SWING' || state.status?.swing_active) actual = '当前正在起摆'
    else if (state.mode_name === 'BALANCE') actual = `当前平衡${gesture?.busy ? ' · 圈参考忙' : ''}`
    else if (state.status?.pwm === 0 && state.status?.pwm_zero && !state.status?.run_session && !state.status?.swing_active) actual = '当前停止已读回'
    else actual = '等待读回动作状态'
  }
  return {enabled, recognition, request, actual}
}
