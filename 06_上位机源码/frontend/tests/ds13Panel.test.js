// Exercise the real DS13 component watchers with Vue, without a device or DOM.
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import * as vue from 'vue'
import * as drawing from '../src/ds13Draw.js'

const script=fs.readFileSync(new URL('../src/Ds13DrawPanel.vue',import.meta.url),'utf8')
  .match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .+\r?\n/gm,'')
const setup=new Function('deps',`const {computed,onMounted,onBeforeUnmount,ref,watch,
  canDraw,createStrokeController,createDrawingTimeline,drawingSegments,targetFromY,defineProps,defineEmits}=deps;
  ${script}
  return {hasRows,targetPaths,actualPaths,down,move,stop,stroke,target,inputActive};`)

test('bottom drawing panels render observed target and encoder and clear after session changes',async()=>{
 const props=vue.reactive({active:true,state:{firmware:'DS13',session:'s'},samples:[]})
 const scope=vue.effectScope()
 const panel=scope.run(()=>setup({...vue,...drawing,defineProps:()=>props,defineEmits:()=>()=>{},onMounted:()=>{},onBeforeUnmount:()=>{}}))
 const row=(n,encoder)=>({sample_id:`s:1:${n}`,session_epoch:'s:1',gap_before:false,channels:['arm_angle_deg'],raw:{
   coherent:true,mode:3,fault:0,job_id:7,play_state:6,play_time_ms:n*5,board_ms:1000+n*5,
   origin:'100',wide_encoder:String(encoder),wide_target:'120',live_offset:20}})
 try {
  props.samples=[row(1,110),row(2,114)];await vue.nextTick()
  assert.equal(panel.hasRows.value,true)
  assert.equal(panel.targetPaths.value.length,1)
  assert.equal(panel.actualPaths.value.length,1)
  assert.notEqual(panel.targetPaths.value[0],panel.actualPaths.value[0],'actual trace must use the encoder, not the target')
  props.state={...props.state,session:'new'};await vue.nextTick()
  assert.equal(panel.hasRows.value,false)
 } finally {scope.stop()}
})

test('panel pointer press, vertical movement and release send one live stroke',async()=>{
 const now=Date.now()/1000
 const props=vue.reactive({active:true,samples:[],state:{firmware:'DS13',session:'s',host_online:true,
   connected:true,synced:true,status_fresh:true,status_age_s:0,observed_at:now,display_fresh:true,
   fd2:{control_age_s:0},mode_name:'BALANCE',fault:0}})
 const scope=vue.effectScope(),sent=[],drawingState=[];let captured=null
 const originalFetch=globalThis.fetch
 globalThis.fetch=async (_url,options)=>{sent.push(JSON.parse(options.body));return {ok:true,json:async()=>({state:'accepted'})}}
 const panel=scope.run(()=>setup({...vue,...drawing,defineProps:()=>props,defineEmits:()=>(_event,value)=>drawingState.push(value),onMounted:()=>{},onBeforeUnmount:()=>{}}))
 const area={getBoundingClientRect:()=>({top:0,height:100}),setPointerCapture:id=>{captured=id}}
 try {
  panel.down({button:0,pointerId:4,clientY:50,currentTarget:area,preventDefault(){}})
  assert.equal(captured,4);assert.equal(panel.inputActive.value,true)
  panel.move({pointerId:4,clientY:25,currentTarget:area});panel.stroke.hold()
  panel.stop();await panel.stroke.settled()
  assert.deepEqual(sent.map(row=>row.action),['stroke-begin','stroke-move','stroke-end'])
  assert.equal(sent[1].target,96);assert.equal(panel.inputActive.value,false)
  assert.deepEqual(drawingState,[true,false])
 } finally {scope.stop();globalThis.fetch=originalFetch}
})
