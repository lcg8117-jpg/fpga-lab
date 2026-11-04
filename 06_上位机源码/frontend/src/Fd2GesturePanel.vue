<template>
  <section class="fd2-gesture" aria-label="光学手势识别">
    <h3>光学手势</h3>
    <div :key="feedbackId" class="recognition-display" :class="{recognized:active}" role="status" aria-live="polite" aria-atomic="true">
      <GestureIcon :kind="active ? icons[code] : 'hand'" />
      <div><span>{{ active ? '已识别手势' : fresh ? '识别就绪' : '等待实时数据' }}</span><strong>{{ active ? label(code) : fresh ? '等待手势' : '识别暂停' }}</strong></div>
    </div>
    <p class="last-gesture">{{ fresh && lastCode ? `${retained ? '保留识别' : '上次识别'}：${label(lastCode)}` : '将手移至传感器前方' }}</p>
    <details class="gesture-instructions">
      <summary>手势说明</summary>
      <div class="gesture-reference-grid">
        <div v-for="(kind,n) in icons" :key="n"><GestureIcon :kind="kind" /><span>{{ label(Number(n)) }}</span></div>
      </div>
      <p>上方显示识别事件；设备实际动作以运行状态为准。</p>
    </details>
  </section>
</template>

<script setup>
import {computed,ref,watch} from 'vue'
import GestureIcon from './GestureIcon.vue'
import {gestureEventLabel,isNewFd2Gesture} from './fd2Presentation.js'
const props=defineProps({state:{type:Object,required:true},fresh:Boolean})
const icons={1:'right',2:'left',3:'up',4:'down',5:'near',6:'far',7:'turn',8:'reverse-turn',9:'hand'}
const code=ref(null),lastCode=ref(null),retained=ref(true),feedbackId=ref(0),activeUntil=ref(0)
const active=computed(()=>props.fresh && (props.state.presentation_now_ms??Date.now()) < activeUntil.value)
const label=value=>!['ADB1','ADB1_FASTP','ADB1_07'].includes(props.state.firmware) && value===9 ? '挥手' : gestureEventLabel(props.state.firmware,value)
let previous
watch([()=>props.state.firmware,()=>props.state.session,()=>props.state.gesture_event?.sequence,()=>props.fresh],()=>{
  if(!props.fresh) {
    activeUntil.value=0
    previous=undefined
    return
  }
  const current={firmware:props.state.firmware,session:props.state.session,gesture_event:{...props.state.gesture_event}}
  if (!previous || previous.session!==current.session || previous.firmware!==current.firmware) {
    activeUntil.value=0
    lastCode.value=null
    retained.value=true
  }
  if(isNewFd2Gesture(previous,current,props.fresh)) {
    code.value=current.gesture_event.code
    lastCode.value=code.value
    retained.value=false
    activeUntil.value=(props.state.presentation_now_ms??Date.now())+1200
    feedbackId.value++
  } else if(props.fresh && current.gesture_event.sequence>0 && current.gesture_event.code>=1 && current.gesture_event.code<=9) {
    lastCode.value=current.gesture_event.code
  }
  previous=current
},{immediate:true})
</script>

<style scoped>
.fd2-gesture h3{font-size:16px;font-weight:400;color:#C7CBBE;margin:0 0 16px}
.recognition-display{display:flex;align-items:center;gap:22px;min-height:132px;padding:22px 18px;border:1px solid #555B4E;border-radius:6px;background:#292D26}
.recognition-display>.gesture-icon{width:56px;height:56px;color:#A6AA9B;flex:none}
.recognition-display>div{display:flex;flex-direction:column;gap:8px;min-width:0}
.recognition-display span{font-size:14px;color:#A6AA9B}
.recognition-display strong{font-size:30px;line-height:1.3;font-weight:400;overflow-wrap:anywhere}
.recognition-display.recognized{background:#655034;border-color:#DBAA64;animation:recognition-arrival .32s ease-out}
.recognized>.gesture-icon,.recognized span,.recognized strong{color:#FFE0AE}
.last-gesture{font-size:15px;color:#A6AA9B;margin:16px 0 28px;min-height:22px}
.gesture-instructions{font-size:15px;color:#A6AA9B;line-height:1.6}
.gesture-instructions summary{cursor:pointer;width:fit-content}
.gesture-reference-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px 10px;margin:22px 0}
.gesture-reference-grid>div{display:flex;align-items:center;gap:8px}
.gesture-reference-grid .gesture-icon{width:22px;height:22px}
.gesture-instructions p{font-size:14px}
@keyframes recognition-arrival{from{box-shadow:inset 0 0 0 2px #F0C486;background:#86633B}to{box-shadow:inset 0 0 0 0 transparent;background:#655034}}
@media (min-width:2200px){.recognition-display{min-height:156px}.recognition-display strong{font-size:36px}.recognition-display>.gesture-icon{width:66px;height:66px}.fd2-gesture h3,.last-gesture{font-size:17px}}
@media (prefers-reduced-motion:reduce){.recognition-display.recognized{animation:none}}
</style>
