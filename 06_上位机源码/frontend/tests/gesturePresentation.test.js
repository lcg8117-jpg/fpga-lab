import test from 'node:test'
import assert from 'node:assert/strict'
let gesturePresentation, pageFresh, gestureGuide
try { ({ gesturePresentation, pageFresh, gestureGuide } = await import('../src/gesturePresentation.js')) }
catch (error) { if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error }

test('手势识别、请求接受与真实动作分别呈现', () => {
  assert.equal(typeof gesturePresentation, 'function')
  const state = {status_fresh:true,mode_name:'IDLE',parameters:{gesture_enable:{current:true,value:1}},
    status:{pwm:0,pwm_zero:true},gesture:{code:7,sequence:9,error:0,accepted:true,rejected:false}}
  const view = gesturePresentation(state)
  assert.equal(view.enabled, true)
  assert.match(view.recognition, /顺时针/)
  assert.match(view.request, /已接受/)
  assert.match(view.actual, /停止/)
  assert.doesNotMatch(view.actual, /完成|转圈/)
  assert.match(gesturePresentation({...state, mode_name:'BALANCE',status:{run_session:true}}).actual, /平衡/)
})

test('无参数读回不猜手势开关，断流不保留实际动作结论', () => {
  assert.equal(typeof gesturePresentation, 'function')
  const view = gesturePresentation({status_fresh:false,mode_name:'BALANCE',parameters:{gesture_enable:{current:true,value:1}},
    gesture:{code:1,sequence:2,error:0,accepted:true}})
  assert.equal(view.enabled, null)
  assert.match(view.actual, /无新状态/)
  assert.match(view.request, /历史/)
  assert.equal(gesturePresentation({status_fresh:true,parameters:{}}).enabled, null)
})

test('识别错误、拒绝以及无效的事件不显示成功', () => {
  assert.equal(typeof gesturePresentation, 'function')
  const state = {status_fresh:true,gesture:{code:7,sequence:4,error:1,rejected:true}}
  assert.match(gesturePresentation(state).recognition, /异常/)
  assert.match(gesturePresentation(state).request, /拒绝/)
  assert.match(gesturePresentation({gesture:{code:0,sequence:0}}).recognition, /尚无/)
})

test('摆角与 PWM 独立判断到达时间，未来时间不能当作有效读数', () => {
  assert.equal(typeof pageFresh, 'function')
  assert.equal(pageFresh(100, 101000, true), true)
  assert.equal(pageFresh(98, 101000, true), false)
  assert.equal(pageFresh(102, 101000, true), false)
  assert.equal(pageFresh(100, 101000, false), false)
  assert.equal(pageFresh(null, 101000, true), false)
})

test('SP24 使用保留事件及实际拒绝原因，不把查询次数当新手势或完成动作', () => {
  const state={firmware:'SP24',observed_at:100,status_fresh:true,mode_name:'SWING',status:{swing_active:true},
    gesture_event:{event_count:4,event_ms:1230,code:3,action:1,reason:3},
    gesture_runtime:{received_at:99.9,poll_sequence:10000,ready:true,enabled:true,blocked:true,fresh_balance_ready:false},
    parameters:{gesture_enable:{current:true,value:1}}}
  const view=gesturePresentation(state)
  assert.match(view.recognition,/上滑.*事件 4.*1230.*保留/)
  assert.doesNotMatch(view.recognition,/10000|新收到/)
  assert.match(view.request,/上滑起摆.*起摆期间已忽略/)
  assert.match(view.actual,/起摆/)
  assert.match(view.sensor,/就绪/)
  assert.match(view.handoff,/手势已屏蔽/)
  assert.doesNotMatch(view.actual,/完成|稳定/)
  assert.match(gesturePresentation({...state,gesture_event:{event_count:0,code:0}}).recognition,/尚无/)
})

test('SP24 历史事件和当前传感器状态分别判断新鲜度，不混为原子快照',()=>{
  const state={firmware:'SP24',status_fresh:true,observed_at:100,mode_name:'BALANCE',
    gesture_event:{event_count:2,event_ms:800,code:1,action:2,reason:7},
    gesture_runtime:{received_at:96,ready:true,fresh_balance_ready:true,hold_initialized:true}}
  const view=gesturePresentation(state)
  assert.match(view.request,/定位.*已接受/)
  assert.match(view.sensor,/无新状态|陈旧|未读回/)
  assert.doesNotMatch(view.actual,/HOLD 就绪/)
  assert.match(gesturePresentation({...state,status_fresh:false}).request,/历史/)
  const expiredAfterTransportStopped={...state,presentation_now_ms:101000,gesture_runtime:{...state.gesture_runtime,received_at:98.2}}
  assert.match(gesturePresentation(expiredAfterTransportStopped).sensor,/无新状态|陈旧|未读回/)
})

test('SP24 提示采用上滑起摆，起摆期间挥手无效；旧版语义保留',()=>{
  assert.equal(typeof gestureGuide,'function')
  const guide=gestureGuide('SP24')
  assert.equal(guide.items[0].label,'上滑')
  assert.match(guide.note,/起摆.*挥手.*忽略/)
  assert.doesNotMatch(guide.note,/先 STOP 再重新/)
  assert.equal(gestureGuide('SG21').items[0].label,'右滑')
})
