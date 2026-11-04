<template>
  <section class="g3-panel" aria-label="GOWIN 映射显示">
    <div class="g3-heading">
      <h3>GOWIN</h3>
      <span class="g3-status" :title="state.gowin?.status || ''">{{ statusLabel }}</span>
    </div>
    <div class="g3-chart">
      <span class="y-label">速度</span>
      <svg viewBox="-500 -110 1000 220" preserveAspectRatio="none" role="img" aria-label="真实编码器采样与预定字形形成的 GOWIN 映射图，不表示二维实物轨迹">
        <polyline v-for="(segment,i) in segments" :key="i" :points="segment.map(p=>`${p.x_display},${-p.y_display}`).join(' ')" fill="none" stroke="#dbaa64" stroke-width="3" vector-effect="non-scaling-stroke" />
      </svg>
      <span class="x-label">位置</span>
    </div>
    <div class="actions">
      <button class="button compact" :disabled="!controls?.canSend('play-c1') || state.gowin?.busy" @click="play">准备并播放 C1</button>
      <button class="button compact" :disabled="!controls?.canSend('cancel-c1') || !state.gowin?.busy" @click="controls?.send('cancel-c1')">取消轨迹</button>
    </div>
    <p v-if="error" class="g3-error" role="status">{{ error }}</p>
  </section>
</template>
<script setup>
import {computed,onBeforeUnmount,onMounted,ref,watch} from 'vue'
import {appendMappedPage,mappedSegments} from './g3Mapping.js'
const props=defineProps({state:{type:Object,required:true},controls:{type:Object,default:null}})
const mapping=ref(null),error=ref('')
const segments=computed(()=>mappedSegments(mapping.value?.points || []))
const statusLabel=computed(()=>{
  if(props.state.replay?.active)return '离线回放不显示映射'
  const status=props.state.gowin?.status || '等待 G3 连接'
  if(status.startsWith('本次GOWIN显示完成'))return '显示完成，摆控继续'
  if(status.startsWith('正在逐字绘制'))return '正在绘制'
  if(status.startsWith('就绪：'))return '准备就绪'
  return status
})
let timer=null,pending=false,generation=0,waitingForTask=false
function clear(){generation++;mapping.value=null;error.value='';waitingForTask=false}
async function poll(){
  if(pending || props.state.firmware!=='G3_C1' || !props.state.connected || !props.state.synced || props.state.replay?.active)return
  if(waitingForTask){if(!props.state.gowin?.busy)return;waitingForTask=false}
  const expected=props.state.session, generationAtStart=generation
  if(!expected)return
  pending=true
  try{
    const params=new URLSearchParams({after:String(mapping.value?.cursor || 0)})
    if(mapping.value?.stream)params.set('stream',mapping.value.stream)
    const response=await fetch(`http://127.0.0.1:8000/gowin?${params}`)
    if(!response.ok)throw Error('映射数据读取失败')
    const page=await response.json()
    if(generationAtStart!==generation || props.state.session!==expected)return
    if(page.stream!==expected)throw Error('映射会话已变化')
    mapping.value=appendMappedPage(mapping.value,page);error.value=''
  }catch(exc){if(generationAtStart===generation){clear();error.value=exc.message || '映射数据不可用'}}
  finally{pending=false}
}
function play(){clear();waitingForTask=true;props.controls?.send('play-c1')}
watch([()=>props.state.session,()=>props.state.connected,()=>props.state.firmware,()=>props.state.replay?.active],clear)
onMounted(()=>{timer=setInterval(poll,150);poll()})
onBeforeUnmount(()=>{clearInterval(timer);clear()})
</script>
<style scoped>
.g3-panel{color:#c7cbbe;margin-top:clamp(12px,3.5vh,48px)}
.g3-heading{display:flex;align-items:baseline;justify-content:space-between;gap:16px;margin:0 0 14px}
.g3-heading h3{margin:0;font-size:clamp(20px,1.3vw,28px);font-weight:500;letter-spacing:.02em}
.g3-status{color:#b3b9aa;font-size:14px;line-height:1.45;text-align:right}
.g3-chart{display:grid;grid-template-columns:1.5em minmax(0,1fr);grid-template-rows:auto auto;column-gap:8px}
svg{display:block;grid-column:2;width:100%;height:auto;aspect-ratio:1000 / 365;background:#252a28;border:1px solid #51574e}
.x-label,.y-label{color:#aeb2a3;font-size:13px;line-height:1.5}
.y-label{grid-column:1;grid-row:1;align-self:center;justify-self:center;writing-mode:vertical-rl;letter-spacing:.1em}
.x-label{grid-column:2;text-align:center;padding-top:6px}
.actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:16px 0 0}
.g3-error{color:#e8a193;font-size:14px;line-height:1.5}
@media(max-width:950px){.g3-panel{margin-top:12px}}
@media(max-width:520px){.g3-heading{align-items:flex-start;flex-direction:column;gap:5px}.g3-status{text-align:left}}
</style>
