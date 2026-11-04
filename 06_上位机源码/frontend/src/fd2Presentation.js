export const isFd2 = firmware => /^(?:V[1-5]|ADB1|ADB1_FASTP|ADB1_07|V4_R28|V4_USB_BLE|G3_C1|DS13)$/.test(firmware || '')
export function v4LastPositionLabel(request) {
 if (!Number.isInteger(request?.value) || request.value < 1 || request.value > 6) return '尚无已确认的位置指令'
 const route = request.route === 'ble' ? '蓝牙' : request.route === 'usb' ? 'USB' : '未知链路'
 return `${request.value * 60}° `
}
export function dualLinkLabel(state, route, now = Date.now()) {
 const age = now / 1000 - state.observed_at
 if (!state.host_online || !Number.isFinite(age) || age < 0 || age > 1) return '状态未知'
 if (route === 'ble' && state.transport?.ble_quarantined) return state.transport?.usb_ready ? '异常，位置改走USB' : '异常，请确认USB'
 if (!state.transport?.[`${route}_connected`]) return '未连接'
 return state.transport?.[`${route}_ready`] ? '有回包' : '等待回包'
}
export function fd2TwinAngle(state) {
 if(Object.hasOwn(state,'pendulum_pose_deg'))return Number.isFinite(state.pendulum_pose_deg) ? state.pendulum_pose_deg : null
 // Older snapshots can still display in-range control angles. A clipped endpoint has no recoverable pose.
 return state.angle_display_limited || !Number.isFinite(state.angle_deg) ? null : state.angle_deg
}
export function fd2FaultLabel(value) {
 const label=({1:'角度越界保护',3:'急停保护',4:'ADC 越量程保护',5:'编码器测量保护',7:'超速保护',8:'运算故障保护',9:'起摆失败保护'})[value] || '未知故障'
 return `${label}（${value}）`
}
export function fd2Actions(firmware) {
 const common=['stop','estop','zero','balance','clear-fault']
 if (['V1','V2','V4','ADB1','ADB1_FASTP','ADB1_07'].includes(firmware)) common.push('start-swing')
 if(['V3','ADB1','ADB1_FASTP','ADB1_07'].includes(firmware)) common.push('adapt-enable','adapt-disable')
 if(['V4','V5','V4_R28','V4_USB_BLE'].includes(firmware)) common.push('position','hold')
 if(firmware==='V5') common.push('trajectory')
 if(['ADB1_FASTP','ADB1_07','V4_R28','V4_USB_BLE'].includes(firmware)) common.push('read-gains','set-gains')
 if(firmware==='G3_C1') common.push('play-c1','cancel-c1')
 return common
}

export function appendFd2Samples(existing,incoming,now,reset=false){const seen=new Set();
return [...(reset?[]:existing),...incoming].filter(row=>{if(!row || !Number.isFinite(row.received_at)||row.received_at*1000<now-120000||row.received_at*1000>now)return false;
const id=row.sample_id;
if(id&&seen.has(id))return false;
if(id)seen.add(id);
return true})}

export function periodicArm(value){return Number.isFinite(value)?((value+180)%360+360)%360-180:null}

export const gestureLabels={0:'无',1:'右滑',2:'左滑',3:'上滑',4:'下滑',5:'靠近',6:'远离',7:'顺时针',8:'逆时针',9:'挥手STOP'}
export function gestureEventLabel(firmware, code) {
  if (['ADB1','ADB1_FASTP','ADB1_07'].includes(firmware) && code === 4) return '下滑STOP'
  if (['ADB1','ADB1_FASTP','ADB1_07'].includes(firmware) && code === 9) return '挥手（无停止动作）'
  return gestureLabels[code] ?? '—'
}

export const fd2PositionPresets = Array.from({length:6}, (_,i) => ({value:i+1, label:`${(i+1)*60}°`}))
export const fd2TrajectoryPresets = [
  ['正弦往返','渐入渐出正弦'], ['三角往返','圆滑三角往返'], ['台阶驻留','分段台阶驻留'],
  ['圆顶往返','圆顶驻留往返'], ['递增幅度','递增幅度三段'], ['扫频往返','有界扫频往返'],
].map(([label,description],i) => ({value:i+1,label,description}))

export function isNewFd2Gesture(previous, current, controlFresh) {
  const event = current.gesture_event
  return Boolean(controlFresh && ['V1','V2','ADB1','ADB1_FASTP','ADB1_07'].includes(current.firmware) && previous &&
    previous.session === current.session && previous.firmware === current.firmware &&
    Number.isInteger(previous.gesture_event?.sequence) && Number.isInteger(event?.sequence) &&
    event.sequence > 0 && event.sequence !== previous.gesture_event.sequence &&
    Number.isInteger(event.code) && event.code >= 1 && event.code <= 9)
}

export function fd2MotionLabel(action) {
  if (action == null) return '—'
  if (action >= 0x11 && action <= 0x16) return `相对定位 ${fd2PositionPresets[action-0x11].label}`
  if (action >= 0x21 && action <= 0x26) return fd2TrajectoryPresets[action-0x21].label
  return `动作 ${action}`
}

export function gainPair(g){return Array.isArray(g)?[g[0],g[2]]:[g?.kp??g?.angle_kp,g?.kd??g?.angle_kd]}

export function actualGainWindow(averages,windowMs){const w=averages?.valid?averages.windows?.find(w=>w.window_ms===windowMs):null;
return w?{kp:w.kp,kd:w.kd,coverage:Math.min(1,w.covered_ms/windowMs)}:{kp:null,kd:null,coverage:0}}

export function canFd2Command(state,action,now=Date.now()){if(state.replay?.active||!isFd2(state.firmware)||!fd2Actions(state.firmware).includes(action))return false;
if(['stop','estop'].includes(action))return Boolean(state.host_online);
const fresh=fd2Freshness(state,now);
return fresh.status&&(!['position','trajectory','play-c1'].includes(action)||fresh.control)}

export function fd2Freshness(state,now=Date.now()){
 const elapsed=Math.max(0,now/1000-Number(state.observed_at)),replay=Boolean(state.replay?.active)
 const valid=age=>Number.isFinite(age)&&age>=0&&age+elapsed<.5-1e-9
 return {status:!replay&&Boolean(state.status_fresh)&&valid(state.status_age_s),control:Boolean(state.display_fresh||state.fd2?.control_fresh)&&(replay||valid(state.fd2?.control_age_s)),diagnostic:Boolean(state.fd2?.diagnostic_fresh)&&(replay||valid(state.fd2?.diagnostic_age_s))}
}

export function canDispatchFd2(state,action,busy=false,now=Date.now()){return (!busy||['stop','estop'].includes(action))&&canFd2Command(state,action,now)}

export function commandFailure(error){return error.httpStatus?{...(typeof error.result==='object'?error.result:{}),state:error.result?.state||'rejected',message:error.message}:{state:'indeterminate',message:`${error.message}；结果未知，请查询，未重发`}}

export function replayCacheNow(state,rows,now=Date.now()){return state.replay?.active&&rows.length?Math.max(...rows.map(r=>r.received_at*1000)):now}

export function gainSegments(rows,field){let previous,segment;
const result=[];
for(const row of rows){if(!Number.isFinite(row[field])||!Number.isFinite(row.plot_at)){previous=undefined;
continue}if(!previous||row.gap_before||row.session_epoch!==previous.session_epoch||Math.abs(row.plot_at-previous.plot_at-.005)>.0001){segment=[];
result.push(segment)}segment.push(row);
previous=row}return result}

export const swingLabel=value=>['SWM4','SA21','144'][value]??'—'
export function swingStageLabel(swing,mode){if(mode!=='SWING')return '起摆未运行';
return ({0:'准备 / 等待接平衡',10:'等待连续下垂静止',11:'正向激励',13:'反向激励',40:'自由滑行 / 等待捕获',70:'重试前等待静止',90:'源失败 / 输出禁止'})[swing?.stage]??(swing?.profile===2?'峰值脉冲运行':'未识别的起摆阶段')}
/** FD2 statistics follow the same board-time domain as the waveform, including frozen replay. */
export function fd2RmsAngle(samples, endMs) {
  const rows = samples.filter(row => Number.isFinite(row.plot_at) &&
    row.plot_at * 1000 >= endMs - 3000 && row.plot_at * 1000 <= endMs &&
    (!Array.isArray(row.channels) || row.channels.includes('angle_deg')) &&
    Number.isFinite(row.angle_deg))
  return rows.length ? Math.sqrt(rows.reduce((sum, row) => sum + row.angle_deg ** 2, 0) / rows.length) : null
}

export function formatMotionValue(key, value) {
  return /_deg(?:_s)?$/.test(key) && Number.isFinite(value) ? value.toFixed(3) : value
}
