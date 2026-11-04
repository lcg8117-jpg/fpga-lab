export function appendSamples(existing, incoming, now, reset = false) {
  const seen = new Set()
  return [...(reset ? [] : existing), ...incoming].filter(row => {
    if (!row || !Number.isFinite(row.received_at) || row.received_at * 1000 < now - 30000 || row.received_at * 1000 > now) return false
    if (row.sample_id && seen.has(row.sample_id)) return false
    if (row.sample_id) seen.add(row.sample_id)
    return true
  })
}

export function rmsAngle(samples, now) {
  const values = samples.filter(row => row.received_at * 1000 >= now - 3000 && row.received_at * 1000 <= now)
    .map(row => row.angle_deg).filter(Number.isFinite)
  return values.length ? Math.sqrt(values.reduce((sum, value) => sum + value * value, 0) / values.length) : null
}

export function canConfigure(data) {
  const status = data?.status
  return Boolean(data?.status_fresh && status?.stopped_config_allowed && status.pwm === 0 && status.pwm_zero &&
    !status.run_session && !status.swing_active && !status.zero_capture && !status.estop && !status.fault)
}

export function requestLabel(record) {
  return ({ queued:'已排队，等待 Host', pending:'等待 ACK', accepted:'Host 已接受，等待读回',
    verified:'实际读回已确认', balance:'已读回 BALANCE', swinging:'实际正在起摆', stopping:'等待停止读回',
    stopped:'实际停止已确认', rejected:'请求被拒绝', failed:'执行失败', indeterminate:'结果未知，未重发', cancelled:'已取消' })[record?.state] || '等待 Host 结果'
}

export function reconcileRequest(record, state) {
  const sameSession = record.host_session === state.session && record.host_firmware === state.firmware
  const result = sameSession ? state.requests?.[record.request_id] : undefined
  const next = result ? { ...record, ...result } : { ...record }
  if (['queued', 'pending', 'accepted', 'stopping'].includes(next.state) &&
      (!state.status_fresh || record.host_session !== state.session || record.host_firmware !== state.firmware)) {
    next.state = 'indeterminate'
    next.message = '状态中断或 Host 会话已变化，结果未知；保留 ID 供查询，未重发'
  }
  return next
}
