<template>
  <section class="ds13-panel" aria-label="DS13 手绘与实际跟随">
    <header class="draw-heading"><h2>手绘与实际跟随</h2><span>{{ enabled ? '按住左侧绘制，松开结束' : state.draw?.stroke?.active && state.draw.stroke.source === 'native' ? 'Host 窗口正在绘制' : '连接并进入平衡后可绘制' }}</span></header>
    <div class="charts">
      <section class="chart-column" aria-label="手绘目标">
        <header><h3>手绘目标</h3><span>相对笔画原点 · 计数</span></header>
        <div class="draw-area" :class="{disabled:!enabled, held:inputActive}" role="application" aria-label="单轴手绘区域，上正下负，范围正负 192 Forest 计数"
          @pointerdown="down" @pointermove="move" @pointerup="stop" @pointercancel="stop" @pointerleave="stop" @lostpointercapture="stop">
          <svg viewBox="0 0 1000 400" preserveAspectRatio="none" aria-hidden="true">
            <line x1="0" y1="200" x2="1000" y2="200" class="zero-line" />
            <polyline v-for="(points,i) in targetPaths" :key="i" :points="points" fill="none" stroke="#dbaa64" stroke-width="2" vector-effect="non-scaling-stroke" />
          </svg>
          <span class="limit top">+192</span><span class="limit middle">0</span><span class="limit bottom">−192</span>
          <div class="crosshair" :style="{top:`${(192-target)/384*100}%`}"></div>
          <strong v-if="inputActive">目标 {{ target }}</strong>
          <strong v-else-if="!hasRows">{{ enabled ? '按住并上下移动' : '等待平衡' }}</strong>
        </div>
        <footer><span>0 s</span><span>{{ timeExtent }} s</span></footer>
      </section>
      <section class="chart-column" aria-label="实际编码器跟随">
        <header><h3>实际跟随</h3><span>相对原点 · 计数 <i class="target-key"></i>目标 <i class="actual-key"></i>编码器实际</span></header>
        <div class="actual-area">
          <svg viewBox="0 0 1000 400" preserveAspectRatio="none" aria-label="相对笔画原点的目标和实际位置">
            <line x1="0" y1="200" x2="1000" y2="200" class="zero-line" />
            <polyline v-for="(points,i) in actualTargetPaths" :key="`target-${i}`" :points="points" fill="none" stroke="#dbaa64" stroke-width="1.5" stroke-dasharray="5 4" vector-effect="non-scaling-stroke" />
            <polyline v-for="(points,i) in actualPaths" :key="`actual-${i}`" :points="points" fill="none" stroke="#a1bb90" stroke-width="2" vector-effect="non-scaling-stroke" />
          </svg>
          <span class="limit top">+{{ actualExtent }}</span><span class="limit middle">0</span><span class="limit bottom">−{{ actualExtent }}</span>
          <strong v-if="!hasRows">等待真实编码器数据</strong>
        </div>
        <footer><span>0 s</span><span>{{ timeExtent }} s</span></footer>
      </section>
    </div>
    <p v-if="error || state.draw?.stroke?.error" class="error" role="alert">{{ error || state.draw?.stroke?.error }}</p>
  </section>
</template>
<script setup>
import {computed, onMounted, onBeforeUnmount, ref, watch} from 'vue'
import {canDraw, createStrokeController, createDrawingTimeline, drawingSegments, targetFromY} from './ds13Draw.js'
const props=defineProps({state:{type:Object,default:()=>({})},samples:{type:Array,default:()=>[]},active:{type:Boolean,default:false},now:{type:Number,default:0}})
const emit=defineEmits(['drawing-change'])
const clock=ref(Date.now()), target=ref(0), inputActive=ref(false), error=ref('')
const enabled=computed(()=>canDraw(props.state,props.active,clock.value))
const timeline=createDrawingTimeline(), revision=ref(0)
let pointer=null, timer
let observedSession=null, scanned=0, lastSampleId=null
function consumeSamples(){
  const session=props.state.session,rows=props.samples || []
  if(session!==observedSession){timeline.reset();observedSession=session;scanned=0;lastSampleId=null;revision.value++}
  if(!session || !rows.length){scanned=0;lastSampleId=null;return}
  if(lastSampleId && rows[scanned-1]?.sample_id!==lastSampleId){
    let index=rows.length-1
    while(index>=0 && rows[index]?.sample_id!==lastSampleId)index--
    if(index<0){timeline.reset();scanned=0;revision.value++}else scanned=index+1
  }
  let consumed=false
  for(let i=scanned;i<rows.length;i++){
    const row=rows[i]
    if(typeof row?.session_epoch==='string' && row.session_epoch.startsWith(`${session}:`)){
      timeline.ingest(row);consumed=true
    }
  }
  scanned=rows.length;lastSampleId=rows[rows.length-1]?.sample_id || null
  if(consumed)revision.value++
}
watch([()=>props.state.session,()=>props.samples],consumeSamples,{immediate:true})
const hasRows=computed(()=>{revision.value;return timeline.rows.length>0})
const timeExtent=computed(()=>{revision.value;return Math.max(5,Math.ceil((timeline.rows.at(-1)?.time || 0)+1))})
const actualExtent=computed(()=>{revision.value;let max=192;for(const row of timeline.rows)max=Math.max(max,Math.abs(row.actual),Math.abs(row.target));return Math.ceil(max/64)*64})
function thin(segment){
  if(segment.length<=1200)return segment
  const stride=Math.ceil(segment.length/1200),out=[]
  for(let start=0;start<segment.length;start+=stride){
    const end=Math.min(segment.length,start+stride);let min=start,max=start
    for(let i=start+1;i<end;i++){if(segment[i].value<segment[min].value)min=i;if(segment[i].value>segment[max].value)max=i}
    for(const index of [...new Set([start,min,max,end-1])].sort((a,b)=>a-b))out.push(segment[index])
  }
  return out
}
const targetSegments=computed(()=>{revision.value;return drawingSegments(timeline.rows,'target').map(thin)})
const actualSegments=computed(()=>{revision.value;return drawingSegments(timeline.rows,'actual').map(thin)})
function paths(segments,extent){return segments.map(segment=>segment.map(p=>`${1000*p.time/timeExtent.value},${200-200*p.value/extent}`).join(' '))}
const targetPaths=computed(()=>paths(targetSegments.value,192))
const actualTargetPaths=computed(()=>paths(targetSegments.value,actualExtent.value))
const actualPaths=computed(()=>paths(actualSegments.value,actualExtent.value))
function id(){return globalThis.crypto?.randomUUID?.() || `ds13-${Date.now()}-${Math.random().toString(36).slice(2)}`}
async function send(body){
  const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),800)
  try {const response=await fetch('http://127.0.0.1:8000/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal}); const result=await response.json(); if(!response.ok){const e=Error(result.detail?.message||'命令被拒绝');e.code=result.detail?.code;throw e}return result} finally {clearTimeout(timeout)}
}
const stroke=createStrokeController({send,id,onChange:s=>{if(inputActive.value!==s.active)emit('drawing-change',s.active);inputActive.value=s.active;error.value=s.error;if(!s.active)pointer=null}})
function stop(){pointer=null;stroke.end()}
function down(event){if(!enabled.value || event.button!==0 || pointer!==null)return; event.preventDefault(); target.value=targetFromY(event.clientY,event.currentTarget.getBoundingClientRect().top,event.currentTarget.getBoundingClientRect().height);if(stroke.begin(props.state.session,target.value)){pointer=event.pointerId;event.currentTarget.setPointerCapture(event.pointerId)}}
function move(event){if(event.pointerId!==pointer || !enabled.value)return;const box=event.currentTarget.getBoundingClientRect();const next=targetFromY(event.clientY,box.top,box.height);if(next!==target.value){target.value=next;stroke.move(next)}}
function hide(){if(document.hidden)stop()}
watch(()=>[enabled.value,props.state.session],([ok,session],old)=>{if(!ok || (old && session!==old[1]))stop()},{flush:'sync'})
onMounted(()=>{window.addEventListener('blur',stop);document.addEventListener('visibilitychange',hide);timer=setInterval(()=>{clock.value=Date.now();if(enabled.value)stroke.hold();else stop()},50)})
onBeforeUnmount(()=>{stop();clearInterval(timer);window.removeEventListener('blur',stop);document.removeEventListener('visibilitychange',hide)})
</script>
<style scoped>
.ds13-panel{height:clamp(250px,30vh,400px);min-width:0;min-height:0;display:flex;flex-direction:column;border-top:1px solid var(--line);padding:12px 0 2px;color:#c7cbbe}
.draw-heading,.chart-column header{display:flex;align-items:center;justify-content:space-between;gap:12px}
.draw-heading{margin:0 0 12px}.draw-heading h2{margin:0;font-size:16px;font-weight:500}
.draw-heading span,.chart-column header span,.chart-column footer{font-size:13px;color:#aeb2a3}
.charts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:28px;flex:1;min-height:0}
.chart-column{display:grid;grid-template-rows:26px minmax(0,1fr) 20px;min-width:0;min-height:0}
.chart-column h3{margin:0;font-size:15px;font-weight:500}.chart-column footer{display:flex;justify-content:space-between;padding-top:3px}
.draw-area,.actual-area{position:relative;min-height:0;border:1px solid #62685d;background:#252a28;display:grid;place-items:center;overflow:hidden}
.draw-area{touch-action:none;user-select:none;cursor:crosshair}.draw-area.disabled{opacity:.5;cursor:not-allowed}
svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}.zero-line{stroke:#62675d;stroke-width:1;vector-effect:non-scaling-stroke}
.draw-area strong,.actual-area strong{font-weight:400;font-size:14px;color:#aeb2a3;z-index:1;pointer-events:none}
.limit{position:absolute;left:8px;font-size:11px;color:#aeb2a3;pointer-events:none}.top{top:5px}.middle{top:calc(50% - 7px)}.bottom{bottom:5px}
.crosshair{position:absolute;left:0;right:0;border-top:1px solid #dbaa64;opacity:0;pointer-events:none}.held .crosshair{opacity:1}
.chart-column i{display:inline-block;width:15px;border-top:2px solid;margin:0 5px 3px 8px}.target-key{border-color:#dbaa64!important;border-top-style:dashed!important}.actual-key{border-color:#a1bb90!important}
.error{margin:2px 0 0;color:#e8a193;font-size:13px}
@media(max-width:950px){.ds13-panel{height:auto;min-height:520px}.charts{grid-template-columns:1fr;gap:18px}.chart-column{min-height:220px}.draw-heading{flex-wrap:wrap}}
</style>

