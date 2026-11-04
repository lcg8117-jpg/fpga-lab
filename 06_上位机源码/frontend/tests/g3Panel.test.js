// Exercise the actual SFC setup and Vue watchers without mounting a browser or contacting a device.
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import * as vue from 'vue'
import * as mapping from '../src/g3Mapping.js'

const script=fs.readFileSync(new URL('../src/G3C1Panel.vue',import.meta.url),'utf8')
  .match(/<script setup>([\s\S]*?)<\/script>/)[1]
  .replace(/^import .+\r?\n/gm,'')
const setup=new Function('deps',`const {computed,onBeforeUnmount,onMounted,ref,watch,
  appendMappedPage,mappedSegments,defineProps,fetch}=deps;
  ${script}
  return {mapping,segments,poll};`)

function harness(fetch) {
  const props=vue.reactive({state:{firmware:'G3_C1',session:'session-1',connected:true,synced:true,
    replay:{active:false},gowin:{busy:true,status:'正在绘制'}},controls:null})
  const scope=vue.effectScope()
  const panel=scope.run(()=>setup({...vue,...mapping,fetch,defineProps:()=>props,
    onMounted:()=>{},onBeforeUnmount:()=>{}}))
  return {...panel,props,close:()=>scope.stop(),update:async change=>{
    props.state={...props.state,...change};await vue.nextTick()
  }}
}
const page={stream:'session-1',cursor:2,overrun:false,samples:[
  {cursor:1,point:{source_index:100,x_display:1,y_display:2,break_before:true}},
  {cursor:2,point:{source_index:101,x_display:2,y_display:3,break_before:false}},
]}

test('G3 samples survive ordinary live status updates, including while a page is in flight',async()=>{
  let respond
  const h=harness(()=>new Promise(resolve=>{respond=()=>resolve({ok:true,json:async()=>page})}))
  try {
    const pending=h.poll()
    await h.update({gowin:{busy:true,status:'正在绘制',relative_ms:66500}})
    respond();await pending
    assert.equal(h.mapping.value?.points.length,2,'a same-session refresh must not discard the response')
    await h.update({gowin:{busy:true,status:'正在绘制',relative_ms:66533}})
    assert.equal(h.mapping.value?.points.length,2,'a same-session refresh must retain plotted points')
    assert.equal(h.segments.value[0].length,2)
  } finally {h.close()}
})

test('G3 samples clear when the session changes and late old-session pages stay discarded',async()=>{
  let respond
  const h=harness(()=>new Promise(resolve=>{respond=()=>resolve({ok:true,json:async()=>page})}))
  try {
    const pending=h.poll()
    await h.update({session:'session-2'})
    respond();await pending
    assert.equal(h.mapping.value,null)
  } finally {h.close()}
})

test('G3 clears displayed samples on a real disconnect, profile switch, or replay transition',async()=>{
  for (const change of [{connected:false},{firmware:'DS13'},{replay:{active:true}},{session:'session-2'}]) {
    const h=harness(async()=>({ok:true,json:async()=>page}))
    try {
      await h.poll()
      assert.equal(h.mapping.value?.points.length,2)
      await h.update(change)
      assert.equal(h.mapping.value,null)
    } finally {h.close()}
  }
})
