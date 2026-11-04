<template>
  <section class="fd2-motion" aria-label="旋臂运动操作">
    <div v-if="state.firmware==='V5'" class="motion-tabs" role="group" aria-label="运动功能">
      <button v-for="[value,label] in [['position','相对定位'],['trajectory','有限轨迹']]" :key="value" :aria-pressed="tab===value" @click="tab=value">{{ label }}</button>
    </div>
    <h3 v-else>相对定位</h3>
    <div class="preset-grid" :class="tab">
      <button v-for="preset in presets" :key="`${tab}-${preset.value}`" :disabled="!canSend(tab)" :title="tab==='position' ? `从本次实测位置相对转动 ${preset.label}` : preset.description"
        :class="{requested:matchesRequest(preset.value)}" @click="controls.send(tab,preset.value)">
        <span>{{ preset.label }}</span>
      </button>
    </div>
    <p class="motion-hint">{{ tab==='position' ? '以本次实测位置为起点，相对转动。' : '有限次数结束后，返回本次原点并保持。' }}</p>
    <div class="motion-feedback" aria-live="polite">
      <div v-if="state.firmware==='V4_USB_BLE'" class="motion-current"><span>最近接受定位</span><strong>{{ v4LastPositionLabel(state.motion?.last_position_request) }}</strong></div>
      <div v-else class="motion-current"><span>设备动作</span><strong>{{ diagnosticFresh ? fd2MotionLabel(state.motion?.action) : '等待运动读回' }}</strong></div>
      <p v-if="lastRequest" class="motion-request"><span>{{ requestName }}</span><span>{{ requestLabel(lastRequest) }}</span><button v-if="lastRequest.state==='indeterminate'" class="text-button" @click="controls.query(lastRequest)">查询结果</button></p>
      <p v-if="lastRequest?.message && ['rejected','failed','indeterminate'].includes(lastRequest.state)" class="motion-error" role="status">{{ lastRequest.message }}</p>
      <dl class="motion-readings"><div><dt>跟踪误差</dt><dd>{{ degrees(state.motion?.tracking_error_deg, state.firmware==='V4_USB_BLE' ? controlFresh : diagnosticFresh) }}<small> °</small></dd></div><div><dt>实际速度</dt><dd>{{ degrees(state.motion?.measured_velocity_deg_s) }}<small> °/s</small></dd></div></dl>
      <p v-if="state.firmware==='V4_USB_BLE'" class="motion-hint">距最终目标 {{ remainingDegrees }}°（编码器实测）</p>
      <div class="motion-completion">
        <span :class="{complete:diagnosticFresh && state.motion?.trajectory_done}"><i></i>参考{{ diagnosticFresh && typeof state.motion?.trajectory_done === 'boolean' ? state.motion.trajectory_done ? '完成' : '未完成' : '待读回' }}</span>
        <span :class="{complete:diagnosticFresh && state.motion?.tracking_settled}"><i></i>实际{{ diagnosticFresh ? state.motion?.tracking_settled ? '到位' : '未到位' : '待读回' }}</span>
      </div>
    </div>
    <div class="motion-actions">
      <button class="button hold-action" :disabled="!canSend('hold')" @click="controls.send('hold')">取消并保持</button>
      <button class="button zero-action" :disabled="!canSend('zero')" @click="controls.send('zero')">抓零</button>
    </div>
  </section>
</template>

<script setup>
import {computed,ref,watch} from 'vue'
import {fd2PositionPresets,fd2TrajectoryPresets,fd2MotionLabel,fd2Freshness,v4LastPositionLabel} from './fd2Presentation.js'
import {requestLabel} from './hostPresentation.js'
const props=defineProps({state:{type:Object,required:true},controls:Object,diagnosticFresh:Boolean})
const tab=ref('position')
watch([()=>props.state.firmware,()=>props.state.session],()=>{tab.value='position'})
const presets=computed(()=>tab.value==='trajectory'?fd2TrajectoryPresets:fd2PositionPresets)
const controlFresh=computed(()=>fd2Freshness(props.state,props.state.presentation_now_ms??Date.now()).control)
const remainingDegrees=computed(()=>controlFresh.value && Number.isFinite(props.state.motion?.remaining_to_target_deg) ? props.state.motion.remaining_to_target_deg.toFixed(2) : '—')
const lastRequest=computed(()=>props.controls?.records?.find(r=>r.host_session===props.state.session && r.host_firmware===props.state.firmware && ['position','trajectory','hold','zero'].includes(r.action)))
const requestName=computed(()=>{
  const r=lastRequest.value
  if(!r)return ''
  if(r.action==='hold')return '取消并保持'
  if(r.action==='zero')return '抓零'
  return r.action==='position'?`定位 ${fd2PositionPresets[r.value-1]?.label || '—'}`:fd2TrajectoryPresets[r.value-1]?.label || '轨迹'
})
function canSend(action){return Boolean(props.controls?.canSend(action))}
function matchesRequest(value){return lastRequest.value?.action===tab.value && lastRequest.value?.value===value && !['rejected','failed','cancelled'].includes(lastRequest.value.state)}
function degrees(value,fresh=props.diagnosticFresh){return fresh && Number.isFinite(value)?value.toFixed(2):'—'}
</script>

<style scoped>
.fd2-motion h3{font-size:17px;font-weight:400;margin:0 0 16px}
.motion-tabs{display:grid;grid-template-columns:1fr 1fr;border-bottom:1px solid #4B4E44;margin-bottom:18px}
.motion-tabs button{border:0;border-bottom:2px solid transparent;background:none;padding:10px 4px 13px;color:#A6AA9B;font-size:17px}
.motion-tabs button[aria-pressed=true]{color:#F1F2E9;border-bottom-color:#DBAA64}
.preset-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.preset-grid button{min-width:0;height:52px;padding:6px;border:1px solid #5E6455;border-radius:5px;background:#2D312A;color:#F1F2E9;font-size:22px}
.preset-grid.trajectory{grid-template-columns:repeat(3,minmax(0,1fr))}
.preset-grid.trajectory button{height:44px;font-size:16px}
.preset-grid button:not(:disabled):hover{border-color:#DBAA64;background:#393A2D}
.preset-grid button.requested{border-color:#DBAA64;background:#4A402D;color:#F1CF9E}
.motion-hint{font-size:13px;color:#A6AA9B;line-height:1.6;margin:12px 0 20px}
.motion-feedback{border-top:1px solid #4B4E44;padding-top:17px}
.motion-current{display:flex;flex-direction:column;gap:8px}
.motion-current>span,.motion-readings dt{font-size:14px;color:#A6AA9B}
.motion-current strong{font-size:22px;line-height:1.35;font-weight:400}
.motion-request{display:flex;flex-wrap:wrap;gap:4px 10px;line-height:1.5;color:#DBAA64;font-size:13px;margin:12px 0 0}
.motion-request>span:first-child{color:#C7CBBE}
.motion-error{font-size:13px;color:#E8A193;overflow-wrap:anywhere;line-height:1.5}
.motion-readings{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:18px 0}
.motion-readings dd{font-family:var(--numbers);font-size:23px;margin:7px 0 0;white-space:nowrap}
.motion-readings small{font-family:inherit;font-size:13px;color:#A6AA9B}
.motion-completion{display:flex;flex-wrap:wrap;gap:10px 20px;font-size:14px;color:#A6AA9B}
.motion-completion span{display:flex;align-items:center;gap:7px}
.motion-completion i{width:6px;height:6px;border-radius:50%;background:#717769}
.motion-completion .complete{color:#BDD0AC}.motion-completion .complete i{background:#A1BB90}
.motion-actions{display:grid;grid-template-columns:1fr 72px;gap:10px;margin-top:22px}
.motion-actions .button{height:40px;padding:6px;font-size:15px}
.hold-action{border-color:#997B4F;color:#E7C28C}
@media (min-width:2200px){.preset-grid button{height:64px;font-size:26px}.preset-grid.trajectory button{height:53px;font-size:18px}.motion-current strong{font-size:26px}.motion-tabs button{font-size:19px}.motion-hint,.motion-request{font-size:15px}.motion-readings dd{font-size:28px}.motion-actions .button{height:46px;font-size:17px}}
@media (min-width:951px) and (max-height:1000px){
  .motion-tabs{margin-bottom:10px}.motion-tabs button{padding:8px 4px 9px;font-size:16px}
  .preset-grid button{height:44px}.preset-grid.trajectory button{height:38px;font-size:15px}
  .motion-hint{margin:8px 0 12px}.motion-feedback{padding-top:12px}
  .motion-current{flex-direction:row;justify-content:space-between;align-items:center;gap:8px}.motion-current strong{font-size:18px}
  .motion-request{margin-top:8px;font-size:12px}.motion-readings{margin:12px 0}.motion-readings dd{font-size:21px}
  .motion-actions{margin-top:14px}.motion-actions .button{height:36px}
}
</style>
