import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import * as vue from 'vue'
import {renderToString} from 'vue/server-renderer'
import * as presentation from '../src/fd2Presentation.js'
import * as hostPresentation from '../src/hostPresentation.js'
import {calibrationStorageKey} from '../src/pendulumCalibration.js'

const liveState = () => ({firmware:'ADB1_07',session:'adb1-07',host_online:true,
  observed_at:100,presentation_now_ms:100000,status_fresh:true,status_age_s:0,
  display_fresh:true,fd2:{control_age_s:0},swing:{profile:0},mode_name:'BALANCE'})

test('ADB1 07 keeps explicit ADB1 control capabilities and replay/stale guards', () => {
  assert.equal(presentation.isFd2('ADB1_07'),true)
  assert.deepEqual(presentation.fd2Actions('ADB1_07'),presentation.fd2Actions('ADB1_FASTP'))
  for (const action of ['start-swing','balance','adapt-enable','adapt-disable','read-gains','set-gains']) {
    assert.equal(presentation.canFd2Command(liveState(),action,100000),true,action)
    assert.equal(presentation.canFd2Command(liveState(),action,100600),false,`${action} requires fresh status`)
  }
  for (const action of presentation.fd2Actions('ADB1_07')) {
    assert.equal(presentation.canFd2Command({...liveState(),replay:{active:true}},action,100000),false)
  }
  assert.equal(presentation.canDispatchFd2(liveState(),'stop',true,100600),true)
  assert.equal(presentation.canDispatchFd2(liveState(),'adapt-enable',true,100000),false)
})

test('ADB1 07 gestures retain DownStop and require a new event in the same session and firmware', () => {
  assert.equal(presentation.gestureEventLabel('ADB1_07',4),'下滑STOP')
  assert.equal(presentation.gestureEventLabel('ADB1_07',9),'挥手（无停止动作）')
  const previous={...liveState(),gesture_event:{sequence:10,code:4}}
  const next={...previous,gesture_event:{sequence:11,code:4}}
  assert.equal(presentation.isNewFd2Gesture(previous,next,true),true)
  assert.equal(presentation.isNewFd2Gesture(undefined,next,true),false)
  assert.equal(presentation.isNewFd2Gesture(next,next,true),false)
  assert.equal(presentation.isNewFd2Gesture(previous,{...next,session:'reconnected'},true),false)
  assert.equal(presentation.isNewFd2Gesture({...previous,firmware:'ADB1_FASTP'},next,true),false)
  assert.equal(presentation.isNewFd2Gesture(previous,next,false),false)
})

test('ADB1 07 display calibration has an independent FD2 v3 key', () => {
  assert.match(calibrationStorageKey('ADB1_07'),/:v3:ADB1_07$/)
  const profiles=['ADB1','ADB1_FASTP','ADB1_07','V1','V4_USB_BLE','unknown']
  assert.equal(new Set(profiles.map(calibrationStorageKey)).size,profiles.length)
})

function componentHarness(file,props,names) {
  const source=fs.readFileSync(new URL(`../src/${file}.vue`,import.meta.url),'utf8')
  const script=source.match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .+\r?\n/gm,'')
  const mounted=[],scope=vue.effectScope()
  const setup=new Function('deps',`with(deps){${script};return {${names.join(',')}}}`)
  const stub={render:()=>null}
  const values=scope.run(()=>setup({...vue,...presentation,...hostPresentation,
    Fd2GesturePanel:stub,Fd2MotionPanel:stub,G3C1Panel:stub,
    defineProps:()=>props,defineExpose:()=>{},onMounted:callback=>mounted.push(callback)}))
  return {values,mounted,close:()=>scope.stop(),render:()=>renderToString(vue.createSSRApp({
    components:{Fd2GesturePanel:stub,Fd2MotionPanel:stub,G3C1Panel:stub},
    setup:()=>values,render:vue.compile(source.match(/<template>([\s\S]*?)<\/template>\s*<script/)[1]),
  }))}
}

test('ADB1 07 daily selector, S1 draft and independent readback remain explicit',async () => {
  const props=vue.reactive({state:{...liveState(),pid_gains:{independent:[1,2,3,4,5,6],active:[7,8,9,10,11,12]}}})
  const calls=[],originalFetch=globalThis.fetch
  globalThis.fetch=async (url,options)=>{
    calls.push({url,method:options?.method||'GET',body:options?.body&&JSON.parse(options.body)})
    return {ok:true,json:async()=>url.endsWith('/config')?{bridge_port:8371,firmware_variant:'ADB1_07'}:{state:'accepted'}}
  }
  const h=componentHarness('Fd2ControlPanel',props,['props','state:props.state','versions','versionLabels',
    'connection','saveConnection','actions','labels','canSend','send','gainNames','gainDraft','validGains','formatGains',
    'target','notice','records','requestLabel','query'])
  try {
    for (const callback of h.mounted) await callback()
    assert.deepEqual(h.values.versions,['ADB1_07','V4_USB_BLE','G3_C1','DS13'])
    assert.equal(h.values.versionLabels.ADB1_07,'ADB1 07 / S1')
    assert.equal(h.values.connection.firmware_variant,'ADB1_07')
    assert.deepEqual(h.values.gainDraft.value,[53.78,0,210.14,17.67,0,616.61])
    assert.equal(calls.filter(c=>c.method==='POST').length,0,'loading must not connect, write or enable')
    const html=await h.render()
    for (const text of ['ADB1 07 / S1','启用自适应','关闭自适应','S1 不会自动写入','独立读回：1.00000','实际采用：7.00000']) assert(html.includes(text),text)
    assert(!html.includes('快P'))
    await h.values.send('adapt-enable')
    await h.values.send('adapt-disable')
    await h.values.send('read-gains')
    assert.deepEqual(calls.filter(c=>c.method==='POST').map(c=>c.body.action),['adapt-enable','adapt-disable','read-gains'])
    props.state={...props.state,replay:{active:true}};await vue.nextTick()
    await h.values.send('adapt-enable');await h.values.send('set-gains',h.values.gainDraft.value)
    assert.equal(calls.filter(c=>c.method==='POST').length,3,'replay cannot write or enable')
  } finally {h.close();globalThis.fetch=originalFetch}
})

test('ADB1 07 feature panel exposes manual adaptive controls with real gain readback',async () => {
  const props=vue.reactive({state:{...liveState(),adaptive:{enabled:false,used:false,active_gains:[53.78,0,210.14,0,0,0]}},samples:[],now:100000,controls:{canSend:()=>true,send:()=>{}}})
  const h=componentHarness('Fd2FeaturePanel',props,['state:props.state','samples:props.samples','now:props.now','controls:props.controls',
    'fresh','statusFresh','diagnosticFresh','runtimeLabel','read','fmt','gainPair','trend','average','gains','gestureEventLabel',
    'motionFields','readMotion','readDiagnostic','swingLabel','swingStageLabel','fd2FaultLabel','Fd2GesturePanel','Fd2MotionPanel','G3C1Panel'])
  try {
    const html=await h.render()
    for (const text of ['启用自适应','关闭自适应','53.780','210.140','旋臂实际','识别事件','光学下滑 STOP']) assert(html.includes(text),text)
    assert(html.includes('光学下滑 STOP 始终优先，不受运动指令间隔屏蔽。'))
    assert(!html.includes('光学下滑 STOP 也会被屏蔽'))
    assert(!html.includes('快P'))
  } finally {h.close()}
})

test('historical FASTP retains its own optical DownStop interval warning',async () => {
  const props=vue.reactive({state:{...liveState(),firmware:'ADB1_FASTP'},samples:[],now:100000,controls:{canSend:()=>true,send:()=>{}}})
  const h=componentHarness('Fd2FeaturePanel',props,['state:props.state','samples:props.samples','now:props.now','controls:props.controls',
    'fresh','statusFresh','diagnosticFresh','runtimeLabel','read','fmt','gainPair','trend','average','gains','gestureEventLabel',
    'motionFields','readMotion','readDiagnostic','swingLabel','swingStageLabel','fd2FaultLabel','Fd2GesturePanel','Fd2MotionPanel','G3C1Panel'])
  try {
    const html=await h.render()
    assert(html.includes('运动指令间隔期间，光学下滑 STOP 也会被屏蔽。'))
    assert(!html.includes('光学下滑 STOP 始终优先'))
  } finally {h.close()}
})
