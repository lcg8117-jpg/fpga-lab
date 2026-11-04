<template>
<aside class="gesture-panel fd2-feature">
<header class="panel-heading">
<h2>{{ state.firmware === 'V3' ? '自适应调节' : `${state.firmware || 'FD2'} ${['V4','V5','V4_R28','V4_USB_BLE'].includes(state.firmware) ? '运动操作' : '功能状态'}` }}</h2>
<div class="feature-runtime" :title="state.replay?.active ? '离线回放' : fresh ? '实时' : '等待数据'" :class="{balancing:statusFresh && state.mode_name==='BALANCE',faulted:statusFresh && !!state.fault}"><i></i><strong>{{ runtimeLabel }}</strong></div>
</header>
<p v-if="statusFresh && state.mode_name==='SWING'" class="swing-stage">{{ swingLabel(state.swing?.profile) }} · {{ swingStageLabel(state.swing,state.mode_name) }}</p>
<p v-if="statusFresh && state.fault" class="host-error" role="alert">{{ fd2FaultLabel(state.fault) }}</p>
<p v-if="state.firmware === 'DS13'">手绘和实际跟随位于页面下方。</p>
<G3C1Panel v-if="state.firmware === 'G3_C1'" :state="state" :controls="controls" />
<Fd2GesturePanel v-if="['V1','V2','ADB1','ADB1_FASTP','ADB1_07'].includes(state.firmware)" :state="state" :fresh="fresh" />
<p v-if="state.firmware === 'ADB1_FASTP'">运动指令间隔期间，光学下滑 STOP 也会被屏蔽。</p>
<p v-if="state.firmware === 'ADB1_07'">光学下滑 STOP 始终优先，不受运动指令间隔屏蔽。</p>
<Fd2MotionPanel v-if="['V4','V5','V4_R28','V4_USB_BLE'].includes(state.firmware)" :state="state" :controls="controls" :diagnostic-fresh="diagnosticFresh" />
<div v-if="['ADB1','ADB1_FASTP','ADB1_07'].includes(state.firmware)" class="adaptive-state">
<span>旋臂实际 {{ read(state.motion?.actual_count) }}</span><span>运动参考 {{ read(state.motion?.reference_count) }}</span><span>最终目标 {{ read(state.motion?.final_target_count) }}</span>
</div>
<template v-if="['V3','ADB1','ADB1_FASTP','ADB1_07'].includes(state.firmware)">
<div v-if="['ADB1_FASTP','ADB1_07'].includes(state.firmware)" class="adaptive-actions">
<button class="button compact" :disabled="!controls?.canSend('adapt-enable')" @click="controls.send('adapt-enable')">启用自适应</button>
<button class="button compact" :disabled="!controls?.canSend('adapt-disable')" @click="controls.send('adapt-disable')">关闭自适应</button>
</div>
<div class="adaptive-state"><span>启用 <strong>{{ read(state.adaptive?.enabled) }}</strong></span><span>实际采用 <strong>{{ read(state.adaptive?.used) }}</strong></span></div>
<div class="adaptive-gains" aria-label="实际角度增益">
<div><span>实际 Kp</span><strong>{{ fmt(gainPair(state.adaptive?.active_gains)[0]) }}</strong></div>
<div><span>实际 Kd</span><strong>{{ fmt(gainPair(state.adaptive?.active_gains)[1]) }}</strong></div>
</div>
<p class="gain-legend"><span>P</span>　D <span class="gain-scale">（各自刻度）</span></p>
<svg viewBox="0 0 300 70" aria-label="实际 Kp 琥珀色、Kd 银色趋势">
<g v-for="(field,i) in ['angle_kp','angle_kd']" :key="field">
<polyline v-for="(points,n) in trend(field)" :key="n" :points="points" fill="none" :stroke="i?'#C7CBBE':'#DBAA64'" stroke-width="1.5"/>
</g>
</svg>
</template>
<p v-if="state.fd2?.error || state.fd2?.log_error" class="host-error" role="alert">{{ state.fd2?.error || state.fd2?.log_error }}</p>
<details v-if="state.firmware !== 'G3_C1'" class="feature-diagnostics">
<summary>诊断详情</summary>
<template v-if="['V3','ADB1','ADB1_FASTP','ADB1_07'].includes(state.firmware)">
<p v-for="[key,label] in [['manual_gains','手动基值'],['active_gains','实际增益'],['target_gains','候选目标']]" :key="key">{{ label }}：{{ gains(state.adaptive?.[key]) }}</p>
<p>冻结原因：{{ read(state.adaptive?.freeze_reason) }}</p>
<p v-for="seconds in [1,5]" :key="seconds">{{ seconds }} 秒实际均值：{{ fmt(average(seconds).kp) }} / {{ fmt(average(seconds).kd) }} · 覆盖 {{ fresh ? `${Math.round(average(seconds).coverage*100)}%` : '—' }}</p>
</template>
<template v-if="['V1','V2','ADB1','ADB1_FASTP','ADB1_07'].includes(state.firmware)">
<p>识别事件 #{{ read(state.gesture_event?.sequence) }} · {{ fresh ? gestureEventLabel(state.firmware, state.gesture_event?.code) : '—' }}</p>
<p>全局最近动作原因：{{ read(state.action_reason) }}</p>
<p>起摆方案：{{ statusFresh ? swingLabel(state.swing?.profile) : '—' }}</p>
</template>
<template v-if="['V4','V5','V4_R28','V4_USB_BLE'].includes(state.firmware)">
<dl>
<div v-for="[key,label] in motionFields" :key="key">
<dt>{{ label }}</dt>
<dd>{{ readMotion(key) }}</dd>
</div>
</dl>
<p>动作请求：{{ readDiagnostic(state.motion?.request_id) }} / {{ readDiagnostic(state.motion?.action) }}</p>
<p>轨迹生成完成：{{ readDiagnostic(state.motion?.trajectory_done) }}</p>
<p>实际跟踪到位：{{ readDiagnostic(state.motion?.tracking_settled) }}</p>
</template>
<p>CRC {{ state.fd2?.crc_errors ?? '—' }} · 缺序 {{ state.fd2?.sequence_gaps ?? '—' }}<br>USB {{ state.transport?.usb_connected ? '已连接' : '未连接' }} · BT10 {{ state.transport?.ble_connected ? '已连接' : '未连接' }}</p>
</details>
</aside>
</template>
<script setup>
import {computed} from 'vue'
import Fd2GesturePanel from './Fd2GesturePanel.vue'
import Fd2MotionPanel from './Fd2MotionPanel.vue'
import G3C1Panel from './G3C1Panel.vue'
import {actualGainWindow,gainPair,gestureEventLabel,fd2Freshness,gainSegments,swingLabel,swingStageLabel,formatMotionValue,fd2FaultLabel} from './fd2Presentation.js'
const props=defineProps({state:Object,samples:Array,now:Number,controls:Object}),fresh=computed(()=>fd2Freshness(props.state,props.state.presentation_now_ms??Date.now()).control)
const statusFresh=computed(()=>props.state.replay?.active||fd2Freshness(props.state,props.state.presentation_now_ms??Date.now()).status)
const diagnosticFresh=computed(()=>fd2Freshness(props.state,props.state.presentation_now_ms??Date.now()).diagnostic)
const runtimeLabel=computed(()=>!statusFresh.value ? '等待运行状态' : ({BALANCE:'平衡运行',SWING:'正在起摆',FAULT:'设备故障',ZERO:'已抓零',ARMED:'准备就绪',IDLE:'待机'})[props.state.mode_name] || '等待运行状态')
const motionFields=[['origin_count','动作原点计数'],['final_target_count','最终目标计数'],['actual_count','实际累计计数'],['reference_count','当前参考计数'],['planner_phase','规划阶段'],['tracking_error_deg','跟踪误差 °'],['measured_velocity_deg_s','实际速度 °/s']]
function read(v){return !fresh.value||v==null?'—':typeof v==='boolean'?v?'是':'否':v}
function readMotion(key){return ['actual_count','reference_count'].includes(key)?read(props.state.motion?.[key]):readDiagnostic(formatMotionValue(key,props.state.motion?.[key]))}
function readDiagnostic(v){return !fd2Freshness(props.state,props.state.presentation_now_ms??Date.now()).diagnostic||v==null?'—':typeof v==='boolean'?v?'是':'否':v}
function fmt(v){return fresh.value&&Number.isFinite(v)?v.toFixed(3):'—'}
function gains(g){return !fresh.value||!g?'—':gainPair(g).map(fmt).join(' / ')}
function average(s){return fresh.value?actualGainWindow(props.state.adaptive?.averages,s*1000):{kp:null,kd:null,coverage:0}}
function trend(field){const rows=(props.samples||[]).filter(r=>r.plot_at*1000>=props.now-10000&&r.plot_at*1000<=props.now);
const values=rows.map(r=>r[field]).filter(Number.isFinite);
if(!values.length||!fresh.value)return [];
const min=Math.min(...values),max=Math.max(...values);
return gainSegments(rows,field).map(segment=>segment.map(r=>`${300*(r.plot_at*1000-props.now+10000)/10000},${60-50*(r[field]-min)/(max-min||1)}`).join(' '))}
</script>
<style scoped>.fd2-feature{overflow:auto;padding:4px 0 8px}
.fd2-feature>.panel-heading{margin-bottom:20px}
.panel-heading>span{font-size:13px;color:#A6AA9B;white-space:nowrap}
.feature-runtime{display:flex;align-items:center;gap:8px;color:#C7CBBE;white-space:nowrap}
.feature-runtime i{width:7px;height:7px;background:#858C7D;border-radius:50%;flex:none}
.feature-runtime strong{font-weight:400;font-size:15px}
.feature-runtime.balancing{color:#BDD0AC}.feature-runtime.balancing i{background:#A1BB90}
.feature-runtime.faulted{color:#E8A193}.feature-runtime.faulted i{background:#E8A193}
.swing-stage{margin:0 0 24px;font-size:15px}
.adaptive-actions{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0}
.adaptive-state{display:flex;flex-wrap:wrap;gap:8px 20px;color:#aeb2a3;font-size:14px}
.adaptive-state strong{color:#e5e8dc;font-weight:500}
.adaptive-gains{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:24px 0 20px}
.adaptive-gains div{min-width:0;display:flex;flex-direction:column;gap:8px}
.adaptive-gains span{color:#aeb2a3;font-size:14px}
.adaptive-gains strong{color:#e9ebe4;font-size:clamp(21px,2vw,30px);font-weight:500;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.adaptive-gains div:first-child strong{color:#DBAA64}
.feature-diagnostics{margin-top:22px;color:#A6AA9B;font-size:14px;line-height:1.7}
.feature-diagnostics>summary{cursor:pointer;width:fit-content}
.feature-diagnostics dl{margin-top:16px}
h3{font-size:14px;font-weight:500;margin:20px 0 12px}
dl{margin:0}
dl div{display:flex;justify-content:space-between;gap:12px;margin:9px 0;font-size:13px}
dt,p{color:#aeb2a3}
dd{margin:0;text-align:right;overflow-wrap:anywhere}
p{font-size:12px;line-height:1.7}
svg{width:100%;height:70px}
.gain-legend span{color:#DBAA64}
.gain-legend .gain-scale{color:#aeb2a3}
@media (min-width:951px) and (max-height:1000px){.fd2-feature>.panel-heading{margin-bottom:14px;padding-bottom:12px}.feature-diagnostics{margin-top:12px}}
</style>
