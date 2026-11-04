<template>
  <main :class="['console-shell', sceneMode]">
    <header class="console-topbar">
      <h1>J280 <span>旋转倒立摆</span></h1>
      <nav class="scene-switch" aria-label="使用场景">
        <button :aria-pressed="sceneMode === 'bench'" @click="changeScene('bench')">无车实验台</button>
        <button :aria-pressed="sceneMode === 'car'" @click="changeScene('car')">车载遥控台</button>
      </nav>
      <div class="system-tools">
        <button class="text-button" @click="openSettings">参数与连接</button>
        <button class="button primary hardware-run" :disabled="!hostControls?.canRun" @click="hostControls?.run()">{{ hostControls?.runLabel || '启动摆控' }}</button>
        <button v-if="['ADB1','ADB1_FASTP','ADB1_07'].includes(state.firmware)" class="button primary" :disabled="!hostControls?.canSend('balance')" @click="hostControls?.send('balance')">手动平衡</button>
        <button v-if="sceneMode === 'car'" class="button stop" :disabled="state.replay?.active || !carControl.status.connected" @click="carControl.stop()">停止小车</button>
        <button class="button stop hardware-stop" :disabled="state.replay?.active || (['ADB1','ADB1_FASTP','ADB1_07'].includes(state.firmware) && !hostControls?.canSend('stop'))" @click="hostControls?.stop()">停止摆控</button>
      </div>
    </header>
    <section class="console-statusbar" aria-label="独立连接状态与模型工况">
      <div class="link-states">
          <span :class="{live:statusOnline}"><i></i>倒立摆：{{ state.replay?.active ? `${state.replay.label || '离线回放'} ${state.firmware || ''}${state.replay.finished ? ' · 已结束' : ''}` : statusOnline ? `${state.firmware} 已连接` : state.host_online ? '等待状态读回' : '未连接' }}</span>
          <span v-if="isFd2(state.firmware) && !state.replay?.active" class="fd2-links">USB {{ state.firmware === 'V4_USB_BLE' ? dualLinkLabel(state,'usb',clock) : (statusOnline && state.transport?.usb_connected ? '已连接' : '未连接') }} · BT10 {{ state.firmware === 'V4_USB_BLE' ? dualLinkLabel(state,'ble',clock) : (statusOnline && state.transport?.ble_connected ? '已连接' : '未连接') }}</span>
          <span v-if="state.firmware === 'SP24' && !precisionFresh" class="precision-stale">精确遥测{{ state.precision?.available === false ? '接口未就绪' : '陈旧／等待' }}</span>
        <span v-if="hostControls?.runRequest" class="run-request-status" role="status">{{ hostControls.runRequest.action === 'balance' ? '平衡请求' : '起摆请求' }}：{{ requestLabel(hostControls.runRequest) }}</span>
        <template v-if="sceneMode === 'car'">
          <span :class="{live:carControl.status.connected && carControl.apiOnline}"><i></i>遥控链路：{{ carControl.apiOnline && carControl.status.connected ? 'BLE 已验证' : '未连接' }}</span>
          <span :class="{live:!!carControl.feedback}"><i></i>车辆反馈：{{ carControl.feedback ? '已收到' : '暂无' }}</span>
        </template>
      </div>
      <div class="model-condition">
        <span>模型</span>
        <div class="load-mode-buttons" role="group" aria-label="3D 模型工况">
          <button :aria-pressed="loadMode === 'unloaded'" @click="loadMode = 'unloaded'">空载</button>
          <button :aria-pressed="loadMode === 'loaded'" @click="loadMode = 'loaded'">有负载</button>
        </div>
      </div>
    </section>
    <section class="console-workspace" :class="{ 'g3-workspace': sceneMode === 'bench' && state.firmware === 'G3_C1' }">
      <aside v-if="sceneMode === 'bench'" class="bench-instruments" aria-label="摆控真实读数">
        <header class="panel-heading"><h2>摆控数据</h2><span :class="['system-state', modeClass]">{{ modeLabel }}</span></header>
        <dl v-if="online" class="readouts bench-readouts">
          <div v-for="metric in visibleMetrics" :key="metric.key" :class="['readout',metric.key]">
            <dt>{{ metric.label }}<span>{{ metric.symbol }}</span></dt>
            <dd :class="metric.tone"><strong>{{ metric.value }}</strong><small>{{ metric.unit }}</small></dd>
          </div>
        </dl>
        <div v-else class="data-empty"><strong>{{ state.received_at ? '遥测中断' : '等待连接' }}</strong><button class="text-button" @click="openSettings">连接倒立摆</button></div>
        <button class="button compact" @click="openSettings">摆控操作与连接</button>
      </aside>
      <section class="model-panel" :aria-label="sceneMode === 'bench' ? '固定台架与摆杆模型' : '车载摆杆辅助模型'">
        <div class="model-stage">
        <DigitalTwin3D
          ref="digitalTwin"
          :scene-mode="sceneMode"
          :firmware="state.firmware || ''"
          :session="state.session || ''"
          :arm-angle="angleOnline ? periodicArm(state.arm_angle_deg) : null"
          :pendulum-angle="modelPendulumAngle"
          :mode="statusOnline ? state.mode_name : 'WAITING'"
          :sample-time="state.angle_received_at || state.observed_at"
          :sample-tick="state.fpga_time_ms ?? null"
          :arm-velocity="state.arm_velocity_deg_s"
          :pendulum-velocity="state.angle_velocity_deg_s"
          :pwm-percent="pwmOnline ? state.duty_actual_percent : null"
          :drawing-active="ds13Drawing"
          :loaded="loadMode === 'loaded'"
        />
        </div>
        <div v-if="!Number.isFinite(modelPendulumAngle)" class="model-caption" title="没有有效姿态数据时，模型保留上一姿态。"><span>{{ angleOnline && state.angle_display_limited ? '摆角超出量程，姿态暂不可用' : '暂无摆角数据' }}</span></div>
        <dl v-if="sceneMode === 'car' && online" class="car-pendulum-summary" aria-label="倒立摆实时反馈">
          <div v-for="metric in visibleMetrics" :key="metric.key"><dt>{{ metric.label }}</dt><dd>{{ metric.value }} {{ metric.unit }}</dd></div>
        </dl>
      </section>
      <Fd2FeaturePanel v-if="sceneMode === 'bench'" :state="hostState" :samples="chartSamples" :now="chartNow" :controls="hostControls" />
      <aside v-else class="remote-panel" aria-label="车载遥控操作区">
        <header class="panel-heading"><h2>小车遥控</h2><span :class="['small-status',{live:carControl.enabled}]">{{ carControl.enabled ? '已启用' : '未启用' }}</span></header>
        <fieldset :disabled="state.replay?.active" class="car-replay-guard"><CarControlPanel :control="carControl" /></fieldset>
        <div class="remote-enable"><button class="button primary" :disabled="state.replay?.active || !carControl.canEnable || carControl.enabled" @click="enableRemote">{{ carControl.enabled ? '遥控已启用' : '启用遥控' }}</button></div>
        <CarControlPad :enabled="carControl.enabled && !state.replay?.active" :connected="carControl.status.connected && !state.replay?.active" :held="carControl.held" @press="carControl.press" @release="carControl.release" @stop="carControl.stop()" />
        <div class="car-v83-actions"><button class="button primary" aria-label="急加速" :disabled="state.replay?.active || !carControl.canBoost" @click="carControl.boost()">急加速 · Shift</button><button class="button stop" aria-label="急刹" :disabled="state.replay?.active || !carControl.status.connected" @click="carControl.brake()">急刹 · 空格</button></div>
        <p class="remote-sequence">按住行驶，松开停止</p>
        <p v-if="carControl.notice" class="remote-notice" role="status">{{ carControl.notice }}</p>
        <p v-if="carControl.apiError || carControl.error || carControl.status.error" class="host-error" role="alert">{{ carControl.apiError || carControl.error || carControl.status.error }}</p>
        <section v-if="carControl.feedback" class="vehicle-feedback" aria-label="车辆实际反馈">
          <header class="panel-heading"><h3>车辆反馈</h3><span class="small-status">{{ carControl.feedback.active ? '驱动中' : '驱动已撤去' }}</span></header>
          <dl>
            <div><dt>左右 PWM</dt><dd>{{ fmt(carControl.feedback.left_pwm8400 / 84,1) }} / {{ fmt(carControl.feedback.right_pwm8400 / 84,1) }} %</dd></div>
            <div><dt>目标 / 渐变目标</dt><dd>{{ fmt(carControl.feedback.requested_q8 / 256,2) }} / {{ fmt(carControl.feedback.ramped_q8 / 256,2) }} 计数/5ms</dd></div>
            <div><dt>左右 5ms 编码器</dt><dd>{{ carControl.feedback.encoder_valid && carControl.feedback.left_delta5 != null ? carControl.feedback.left_delta5 : '--' }} / {{ carControl.feedback.encoder_valid && carControl.feedback.right_delta5 != null ? carControl.feedback.right_delta5 : '--' }}</dd></div>
            <div><dt>轨迹 / 阶段</dt><dd>{{ carControl.feedback.route_id }} / {{ carControl.feedback.phase_id }}</dd></div>
            <div><dt>左右轮计数</dt><dd>{{ carControl.feedback.encoder_valid && carControl.feedback.left_count != null ? carControl.feedback.left_count : '--' }} / {{ carControl.feedback.encoder_valid && carControl.feedback.right_count != null ? carControl.feedback.right_count : '--' }}</dd></div>
            <div><dt>转向读回</dt><dd>{{ carControl.feedback.servo_compare }}</dd></div>
            <div><dt>停止原因码</dt><dd>{{ carControl.feedback.stop_reason }}</dd></div>
          </dl>
        </section>
        <details class="operator-help car-help"><summary>操作说明</summary><div class="help-content"><p>先向左／向右打方向，再前进／后退。前轮舵机负责转向。</p><p><kbd>W</kbd> <kbd>↑</kbd> 前进　<kbd>S</kbd> <kbd>↓</kbd> 后退<br /><kbd>A</kbd> <kbd>←</kbd> 左转　<kbd>D</kbd> <kbd>→</kbd> 右转</p><p><kbd>Shift</kbd> 单次急加速（快速档、直行前进）；<kbd>空格</kbd> 急刹，<kbd>Esc</kbd> 停止并回正。<kbd>F8</kbd> 按住轨迹，可同时调速。切换场景、打开设置或离开窗口会取消遥控。</p><p>BLE 蓝牙：WH-BLE102。连接后需手动启用遥控；连接和车辆反馈分别判断。</p></div></details>
      </aside>
    </section>
    <Ds13DrawPanel v-if="sceneMode === 'bench' && state.firmware === 'DS13'" :state="hostState" :samples="chartSamples" :active="true" @drawing-change="ds13Drawing = $event" />
    <section v-else-if="online || chartSamples.length" class="waveform-section console-waveforms" aria-label="摆控实时响应">
      <header class="waveform-heading"><h2>摆控波形</h2><div class="waveform-tools"><template v-if="isFd2(state.firmware)"><label class="waveform-range">摆角量程 <select v-model.number="angleExtent" aria-label="摆角最小量程"><option :value="30">±30°</option><option :value="60">±60°</option></select></label><button class="text-button" title="按当前窗口重置；超出范围时自动扩展，之后保持量程" @click="waveform?.resetScale()">重置量程</button></template><details v-if="state.firmware === 'SP24'" class="precision-details"><summary>{{ fmt(state.precision?.received_hz,1) }} Hz<span v-if="state.precision?.recording_error || state.precision?.recording_lost_rows" class="precision-alert">记录异常</span></summary><div class="precision-summary"><p>{{ precisionSummary }}</p><p>记录错误：{{ state.precision?.recording_error || '无' }}；未记录 {{ state.precision?.recording_lost_rows ?? '--' }} 行</p></div></details><button class="text-button" @click="exportSamples">导出当前缓存</button><span title="悬停查看 120 秒，移开恢复 10 秒">{{ waveformWindowMs / 1000 }} 秒</span></div></header>
      <WaveformPanel ref="waveform" layout="horizontal" :firmware="state.firmware || ''" :session="state.session || ''" :angle-extent="angleExtent" :origin-count="state.precision?.origin_count || ''" :samples="chartSamples" :now="chartNow" :events="events" @window-change="waveformWindowMs = $event" />
    </section>

    <dialog ref="settingsDialog" class="engineering-dialog" @click="closeOnBackdrop">
      <header class="dialog-heading"><h2>设置</h2><button class="icon-button" aria-label="关闭设置" @click="settingsDialog.close()">×</button></header>
      <Fd2ControlPanel ref="hostControls" :state="hostState" @event="recordEvent" />
      <details class="operator-help"><summary>模型与数据说明</summary><div class="help-content"><p>空载／有负载只切换模型显示，不表示检测到实物负载，也不修改控制参数。</p><p>摆角随有效遥测更新；旋臂按有效累计角的周期内姿态显示，车载底盘保持示意。没有对应实时数据时不生成运动或波形。</p></div></details>
      <div class="engineering-row"><span>机械姿态映射</span><button class="button compact" @click="openCalibration">姿态标定</button></div>
      <dl class="engineering-readouts">
        <div><dt>编码器</dt><dd>{{ state.encoder ?? '--' }}</dd></div>
        <div><dt>旋臂累计角</dt><dd>{{ angleOnline ? fmt(state.arm_angle_deg) : '--' }} °（累计角）</dd></div>
        <div><dt>故障码</dt><dd :class="{ 'fault-text':state.fault }">{{ state.fault == null ? '--' : `0x${hex2(state.fault)}` }}</dd></div>
        <div><dt>旋臂角速度</dt><dd>{{ fmt(state.arm_velocity_deg_s, 1) }} °/s</dd></div>
      </dl>
    </dialog>
  </main>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, shallowRef } from 'vue'
import DigitalTwin3D from './DigitalTwin3D.vue'
import WaveformPanel from './WaveformPanel.vue'
import CarControlPanel from './CarControlPanel.vue'
import CarControlPad from './CarControlPad.vue'
import Fd2ControlPanel from './Fd2ControlPanel.vue'
import Fd2FeaturePanel from './Fd2FeaturePanel.vue'
import Ds13DrawPanel from './Ds13DrawPanel.vue'
import { isFd2, dualLinkLabel, appendFd2Samples, periodicArm, fd2Freshness, replayCacheNow, fd2RmsAngle,fd2TwinAngle } from './fd2Presentation.js'
import { createDs13SampleCache } from './ds13SampleCache.js'
import GesturePanel from './GesturePanel.vue'
import { useCarControl } from './useCarControl.js'
import { pageFresh } from './gesturePresentation.js'
import { appendSamples, rmsAngle as calculateRms, requestLabel } from './hostPresentation.js'
const sceneMode = ref('bench')
const carControl = useCarControl({isAllowed:() => sceneMode.value === 'car' && !state.replay?.active && !settingsDialog.value?.open})

const API = 'http://127.0.0.1:8000'
const WS = 'ws://127.0.0.1:8000/ws'
const HISTORY_MS = 120000

const state = reactive({
  mode_name:'WAITING',angle_deg:null,angle_velocity_deg_s:null,encoder:null,arm_angle_deg:null,
  arm_velocity_deg_s:null,duty_actual_percent:null,fault:null,received_at:0,angle_received_at:0,
  observed_at:0,status_fresh:false,telemetry_fresh:false,host_online:false,
  parameters:{},parameter_specs:[],requests:{},status:{},
})
const hostControls=ref(null)
const samples = shallowRef([])
const ds13Cache = createDs13SampleCache()
const chartSamples = shallowRef([])
const chartNow = ref(Date.now())
const waveformWindowMs = ref(10000)
const waveform = ref(null)
const angleExtent = ref(60)
const events = shallowRef([])
const settingsDialog = ref(null)
const digitalTwin = ref(null)
const ds13Drawing = ref(false)
const loadMode = ref('unloaded')
const clock = ref(Date.now())
let eventId = 0
let ws = null
let onlineTimer = null
let chartRenderTimer = null
let reconnectTimer = null
let destroyed = false

const statusOnline=computed(()=>isFd2(state.firmware)?fd2Freshness(state,clock.value).status:state.status_fresh && clock.value/1000-Number(state.observed_at)<2)
const hostState=computed(()=>({...state,status_fresh:statusOnline.value,display_fresh:isFd2(state.firmware)?fd2Fresh.value:state.display_fresh,presentation_now_ms:clock.value}))
const precisionFresh=computed(()=>state.firmware !== 'SP24' || Boolean(state.precision && state.precision.available !== false && !state.precision.stale &&
  Number.isFinite(state.precision.age_s) && state.precision.age_s + Math.max(0,clock.value/1000-Number(state.observed_at)) < .25))
const fd2Fresh = computed(() => fd2Freshness(state,clock.value).control)
const angleOnline = computed(() => isFd2(state.firmware) ? fd2Fresh.value : precisionFresh.value && pageFresh(state.angle_received_at, clock.value, statusOnline.value))
const modelPendulumAngle = computed(() => angleOnline.value ? isFd2(state.firmware) ? fd2TwinAngle(state) : state.angle_deg : null)
const pwmOnline = computed(() => isFd2(state.firmware) ? fd2Fresh.value : precisionFresh.value && pageFresh(state.arm_received_at, clock.value, statusOnline.value))
const precisionSummary=computed(()=>{
  const p=state.precision || {}
  return `${fmt(p.received_hz,1)} Hz · 缺帧 ${p.missing ?? '--'} · CRC ${p.crc_errors ?? '--'} · 接收龄 ${fmt(p.age_s*1000,0)} ms · PID 龄 ${fmt(p.epoch_age_ms,0)} ms · 待记 ${p.recording_backlog ?? '--'}${p.bridge_overrun ? ' · 桥缓存缺口' : ''}`
})
const online = computed(() => angleOnline.value || pwmOnline.value)
const modeClass = computed(() => !statusOnline.value ? 'idle' : ({ BALANCE:'balance', SWING:'swing', FAULT:'fault', ZERO:'zero' }[state.mode_name] || 'idle'))
const modeLabel = computed(() => state.replay?.active ? `${state.replay.label || '回放'} · ${state.mode_name || '等待'}` : !statusOnline.value ? '无新状态' : ({ BALANCE:'平衡', SWING:'起摆', FAULT:'故障', ZERO:'已抓零', ARMED:'就绪', IDLE:'待机' }[state.mode_name] || '待机'))
const rmsAngle = computed(() => isFd2(state.firmware) ? fd2RmsAngle(samples.value,chartNow.value) : calculateRms(samples.value,Math.max(clock.value,Date.now())))
// Field definitions keep the presentation independent of a particular mechanism.
const metrics = computed(() => [
  { key:'angle', label:'摆杆偏角', symbol:'α', value:fmt(state.angle_deg, 3), unit:'°', tone:'cyan',visible:angleOnline.value && Number.isFinite(state.angle_deg) },
  { key:'velocity', label:'摆杆角速度', symbol:'ω', value:fmt(state.angle_velocity_deg_s, 2), unit:'°/s', tone:'',visible:angleOnline.value && Number.isFinite(state.angle_velocity_deg_s) },
  { key:'pwm', label:'实际 PWM', value:fmtSigned(state.duty_actual_percent, 1), unit:'%', tone:'amber',visible:pwmOnline.value && Number.isFinite(state.duty_actual_percent) },
  { key:'rms', label:'3 秒 RMS', value:rmsAngle.value === null ? '--' : rmsAngle.value.toFixed(3), unit:'°', tone:'' },
])
const visibleMetrics = computed(() => metrics.value.filter(metric => metric.key === 'rms' ? angleOnline.value && rmsAngle.value !== null : metric.visible))

function fmt(v, digits = 2){ return v != null && Number.isFinite(Number(v)) ? Number(v).toFixed(digits) : '--' }
function fmtSigned(v, digits = 1){
  if (v == null || !Number.isFinite(Number(v))) return '--'
  const n = Number(v)
  return `${n > 0 ? '+' : ''}${n.toFixed(digits)}`
}
function exportSamples(){const blob=new Blob([JSON.stringify({firmware:state.firmware,replay:state.replay,samples:chartSamples.value},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='fd2-displayed-cache.json';a.click();URL.revokeObjectURL(url)}
function hex2(v){ return Number(v || 0).toString(16).toUpperCase().padStart(2, '0') }
function changeScene(mode){
  if(sceneMode.value===mode)return
  void carControl.deactivate('使用场景已切换')
  sceneMode.value=mode
}
function openSettings(){void carControl.deactivate('参数设置已打开');settingsDialog.value.showModal()}
async function enableRemote(){if(state.replay?.active)return;await carControl.enable();if(document.activeElement instanceof HTMLElement)document.activeElement.blur()}
function closeOnBackdrop(event){ if (event.target === settingsDialog.value) settingsDialog.value.close() }
function openCalibration(){
  settingsDialog.value.close()
  digitalTwin.value?.openCalibration()
}
// General event interface: only record confirmed actions, never synthetic activity.
function recordEvent(label, at = Date.now()){
  events.value = [...events.value, { id:++eventId, at, label }].filter(event => event.at >= at - HISTORY_MS)
  scheduleCharts()
}

let chartDataDirty = false
function scheduleCharts(){
  // A frozen replay has no moving time domain. Status polls must not repaint its history.
  if (state.replay?.active && !chartDataDirty && chartSamples.value.length) return
  if (chartRenderTimer) return
  chartRenderTimer = setTimeout(() => {
    chartRenderTimer = null
    const now = state.replay?.active && samples.value.length
      ? Math.max(...samples.value.map(row => (row.plot_at ?? row.received_at) * 1000)) : Date.now()
    const cacheNow = replayCacheNow(state, samples.value, now)
    const retained = state.firmware === 'DS13' && !state.replay?.active
      ? ds13Cache.prune(cacheNow) : samples.value.filter(sample => sample.received_at * 1000 >= cacheNow - HISTORY_MS)
    const changed = chartDataDirty || retained.length !== samples.value.length
    if (changed) {
      samples.value = retained
      chartSamples.value = retained
    }
    chartDataDirty = false
    chartNow.value = now
    const retainedEvents = events.value.filter(event => event.at >= now - HISTORY_MS)
    if (retainedEvents.length !== events.value.length) events.value = retainedEvents
  }, state.firmware === 'SP24' ? 33 : 70)
}
function applyTelemetry(data, rows = [], reset = false){
  if (!data || typeof data !== 'object') return
  if (data.observed_at && data.observed_at < state.observed_at) return
  const previousDs13 = state.firmware === 'DS13' && !state.replay?.active
  const previousSession = state.session
  const enteringReplay = data.replay?.active && !state.replay?.active
  Object.assign(state, data)
  // 遥测可早于下一次 500 ms 定时刷新到达，必须同步推进新鲜度判断的本机时钟。
  clock.value = Date.now()
  if (enteringReplay) void carControl.deactivate('离线回放禁用遥控')
  const previousSamples = samples.value
  if(data.firmware === 'DS13' && !state.replay?.active){
    samples.value=ds13Cache.append(rows,Date.now(),reset || !previousDs13 || previousSession!==data.session)
  }else{
    if(previousDs13)ds13Cache.clear()
    samples.value=isFd2(data.firmware)?appendFd2Samples(samples.value,rows,replayCacheNow(state,[...(reset?[]:samples.value),...rows]),reset):appendSamples(samples.value,rows,Date.now(),reset)
  }
  if (reset || samples.value.length !== previousSamples.length || samples.value.some((row,i)=>row!==previousSamples[i])) chartDataDirty = true
  if(reset)events.value=[]
  scheduleCharts()
}
async function loadSnapshot(){
  try{
    const response = await fetch(`${API}/snapshot`)
    const data = await response.json()
    if (data.latest) applyTelemetry(data.latest,Array.isArray(data.history)?data.history:[])
    scheduleCharts()
  }catch(_error){}
}
function connectWs(){
  if (destroyed) return
  ws = new WebSocket(WS)
  ws.onmessage = event => {
    try{
      const message = JSON.parse(event.data)
      if (message.type === 'telemetry') applyTelemetry(message.data,message.samples||[],message.reset===true)
    }catch(_error){}
  }
  ws.onclose = () => {
    if (!destroyed) reconnectTimer = setTimeout(connectWs, 1000)
  }
  ws.onopen=loadSnapshot
  ws.onerror = () => { try{ ws.close() }catch(_error){} }
}
onMounted(async () => {
  await loadSnapshot()
  connectWs()
  onlineTimer = setInterval(() => { clock.value = Date.now(); scheduleCharts() }, 50)
})
onBeforeUnmount(() => {
  destroyed = true
  if (onlineTimer) clearInterval(onlineTimer)
  if (chartRenderTimer) clearTimeout(chartRenderTimer)
  if (reconnectTimer) clearTimeout(reconnectTimer)
  try{ ws?.close() }catch(_error){}
})
</script>
