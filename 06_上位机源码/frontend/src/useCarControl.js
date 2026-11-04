import {computed, onBeforeUnmount, onMounted, reactive} from 'vue'
import {createCarInput, keyAction} from './carInput.js'
const API = 'http://127.0.0.1:8000/car'
async function httpRequest(path, body, timeout = 5000, keepalive = false) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), timeout)
  try {
    const response = await fetch(API+path, {signal:controller.signal,keepalive,
      ...(body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})})
    const result = await response.json()
    if (!response.ok) throw new Error(response.status === 404
      ? '当前后台未加载小车接口（404）。请关闭旧窗口后重新运行 START_SHOWCASE.bat。'
      : typeof result.detail === 'string' ? result.detail : result.detail?.message || `小车请求被拒绝（${response.status}）`)
    return result
  } finally { clearTimeout(timer) }
}
// The controller is also exercised with an explicit offline HTTP fixture in Node.
export function createCarControl({request = httpRequest, isActive = () => !document.hidden && document.hasFocus(), isAllowed = () => true} = {}) {
  const input = createCarInput(), hotkeys = new Set()
  let session = null, sequence = 0, generation = 0, intentBusy = false, stopping = null, destroyed = false
  let heartbeatTimer, pollTimer, routeRunning = false, parking = null, activating = false, connecting = false
  const model = reactive({status:{connected:false,identified:false,feedback_fresh:false,telemetry:null,received_at:0,session:null,phase:'未连接'},
    devices:[],selected:'',enabled:false,held:[],busy:false,error:'',notice:'需要 RC83 配套固件；连接与启用须手动操作',apiOnline:false,apiError:'',
    speed_q8:1024,slow_percent:50,steer_percent:100,route:1,peak_q8:2048,
    feedback:computed(() => model.apiOnline && model.status.feedback_fresh && model.status.telemetry &&
      Date.now()/1000-model.status.received_at>=0 && Date.now()/1000-model.status.received_at<1 ? model.status.telemetry : null),
    canEnable:computed(() => model.apiOnline && model.status.connected && model.status.identified && Boolean(model.status.session) && !model.busy),
    canBoost:computed(() => model.held.length > 0 && model.enabled && model.status.profile === 1 && model.feedback?.active &&
      model.status.motion === 'manual' && input.intent().direction === 'FWD' && input.intent().steering === 'CENTER' &&
      !input.intent().route && model.peak_q8 > model.speed_q8),
  })
  function syncHeld() { model.held = input.held() }
  function clearInput() { input.cancel(); syncHeld(); model.enabled = false; routeRunning = false; generation++ }
  function parameters() { return {speed_q8:Number(model.speed_q8),route:0,slow_percent:Number(model.slow_percent),steer_percent:Number(model.steer_percent)} }
  function updateStatus(result) {
    model.status = result
    if (routeRunning && result.feedback_fresh && !result.preparing && result.motion === null && !result.telemetry?.active) {
      input.completeRoute(); routeRunning = false
    }
    if (result.motion === 'route') routeRunning = true
  }
  async function stop(reason = '遥控已取消', keepEnabled = false, path = '/stop') {
    clearInput()
    const stopGeneration = generation
    if (stopping) {
      // One explicit emergency can upgrade a pending normal STOP. The service
      // joins both requests before rotating their shared parking session.
      if (path === '/brake' && !parking.brake) {
        const transaction = parking
        transaction.brake = true
        model.notice = '急刹已请求，等待制动停车读回'
        transaction.upgrade = (async () => {
          try {
            const result = await request('/brake', {session:session || transaction.token}, 5000, true)
            if (!destroyed) { updateStatus(result); session = result.session; sequence = 0; model.notice = '急刹已确认；重新操作前请启用遥控' }
          } catch (error) { model.error = `急刹结果未知：${error.message}；遥控已取消（未重试）` }
        })()
      }
      return parking.upgrade || stopping
    }
    const token = session || model.status.session
    session = null
    model.notice = `${reason}，等待停车读回`
    if (!token) return
    const transaction = parking = {token,brake:path === '/brake',upgrade:null}
    stopping = (async () => {
      try {
        const result = await request(path, {session:token}, 5000, true)
        if (transaction.upgrade) { await transaction.upgrade; return }
        if (!destroyed) {
          updateStatus(result); session = result.session; sequence = 0
          if (keepEnabled && !transaction.brake && generation === stopGeneration && isActive() && isAllowed()) {
            const resumeGeneration = generation
            if (await sendIntent(true, true) && generation === resumeGeneration) model.enabled = true
          }
          model.notice = model.enabled ? '已停车，再次按住可操作' : '遥控已取消；重新操作前请启用'
        }
      } catch (error) { if (!transaction.upgrade) model.error = `停车结果未知：${error.message}；遥控已取消` }
      finally { if (transaction.upgrade) await transaction.upgrade; stopping = null; parking = null }
    })()
    return stopping
  }
  async function sendIntent(neutral = false, duringStop = false) {
    if ((!model.enabled && !neutral) || !session || intentBusy || (stopping && !duringStop) || !isActive() || !isAllowed()) return false
    const token = session, currentGeneration = generation
    intentBusy = true
    try {
      input.setRoute(model.route)
      await request('/intent', {session:token,sequence:++sequence,...parameters(),...(neutral ? {direction:null,steering:'CENTER'} : input.intent())}, 800)
      return currentGeneration === generation && session === token
    } catch (error) {
      if (currentGeneration === generation && session === token) { model.error = `遥控输入中断：${error.message}`; void stop('输入中断') }
      return false
    } finally { intentBusy = false }
  }
  async function enable() {
    if (!model.canEnable || stopping || !isActive() || !isAllowed()) return
    clearInput(); model.error = ''; model.busy = true; activating = true
    const currentGeneration = generation
    try {
      const result = await request('/stop', {session:model.status.session})
      if (destroyed || generation !== currentGeneration) return
      updateStatus(result); session = result.session; sequence = 0
      if (await sendIntent(true) && generation === currentGeneration && isActive() && isAllowed()) {
        model.enabled = true; model.notice = '遥控已启用；按住行驶，松开停车'
      }
    } catch (error) { session = null; model.error = `遥控接管结果未知：${error.message}；尚未启用` }
    finally { activating = false; model.busy = false }
  }
  function press(token, action) {
    if (model.enabled && model.canEnable && isActive() && isAllowed() && !stopping && input.press(token, action)) { syncHeld(); void sendIntent() }
  }
  function release(token) {
    const action = input.release(token); syncHeld()
    // Multiple route sources share a hold; releasing one must preserve the other.
    if ((action === 'route' && !model.held.includes('route')) || action === 'forward' || action === 'reverse') void stop('行驶输入已释放', true)
    else if (action) void sendIntent()
  }
  async function boost() {
    if (!model.enabled || stopping || !isActive() || !isAllowed()) return
    const intent = input.intent()
    if (!model.feedback?.active || model.status.motion !== 'manual' || model.status.profile !== 1 || intent.direction !== 'FWD' || intent.steering !== 'CENTER' || intent.route || model.peak_q8 <= model.speed_q8) return
    try { await request('/boost',{session,sequence:++sequence,peak_q8:Number(model.peak_q8)},800) }
    catch (error) { model.error = `急加速结果未知（不重试）：${error.message}` }
  }
  function brake() { if (model.apiOnline && model.status.connected && isActive() && isAllowed()) return stop('急刹',false,'/brake') }
  async function profile(value) {
    if (!model.canEnable || model.held.length || model.feedback?.active || stopping) return
    clearInput(); model.busy = true
    const currentGeneration = generation
    try {
      const result = await request('/profile',{session:session || model.status.session,profile:Number(value)})
      if (!destroyed && generation === currentGeneration) updateStatus(result)
      model.notice = '档位以车辆新反馈为准；请重新启用遥控'
    } catch (error) { model.error = error.message }
    finally { model.busy = false }
  }
  function changeRoute(value) { model.route = Number(value); void stop('轨迹已切换') }
  async function scan() {
    if (!model.apiOnline || model.busy || model.status.connected) return
    model.busy = true; model.error = ''; model.devices = []; model.selected = ''
    try { const result = await request('/scan', {}, 12000); model.devices = result.devices; model.notice = result.devices.length ? '请手动选择小车' : '未找到设备，请确认供电与蓝牙' }
    catch (error) { model.error = error.message }
    finally { model.busy = false }
  }
  async function connect() {
    if (!model.apiOnline || model.busy || !model.selected || model.status.connected) return
    clearInput(); model.busy = true; model.error = ''; connecting = true
    const currentGeneration = generation
    try {
      const result = await request('/connect', {device_id:model.selected}, 20000)
      if (!destroyed) {
        updateStatus(result); model.apiOnline = true; session = result.session; sequence = 0
        model.notice = 'RC83 连接已验证，遥控尚未启用'
        if (generation !== currentGeneration) void stop('连接期间页面状态已变化')
      }
    } catch (error) { model.error = error.message; session = null }
    finally { connecting = false; model.busy = false }
  }
  async function disconnect() {
    clearInput()
    if (stopping) await stopping
    if ((!session && !model.status.session) || model.busy) return
    const token = session || model.status.session
    session = null; model.busy = true; model.error = ''
    try { updateStatus(await request('/disconnect', {session:token}, 5000)); model.notice = '已断开，重新连接需手动选择' }
    catch (error) { model.error = `断开结果未知：${error.message}` }
    finally { model.busy = false }
  }
  async function poll() {
    if (destroyed) return
    const currentGeneration = generation
    try {
      const result = await request('/status', undefined, 1500)
      if (!destroyed) {
        model.apiOnline = true; model.apiError = ''
        if (!stopping && !model.busy && generation === currentGeneration) {
          updateStatus(result)
          if (!result.connected || (session && result.session !== session)) { clearInput(); session = null }
        }
      }
    } catch (error) {
      if (!destroyed) {
        model.apiOnline = false; model.apiError = error.message || '无法连接小车后台（127.0.0.1:8000）'
        if (model.enabled) void stop('后端连接中断')
      }
    } finally { if (!destroyed) pollTimer = setTimeout(poll, 150) }
  }
  function keydown(event) {
    if (!isActive() || !isAllowed()) return
    if (event.code === 'Space' || event.code === 'Escape') {
      if (!model.apiOnline || !model.status.connected) return
      event.preventDefault()
      if (!event.repeat && !hotkeys.has(event.code)) { hotkeys.add(event.code); void (event.code === 'Space' ? brake() : stop('键盘停止')) }
      return
    }
    if (!model.enabled) return
    if (event.code === 'ShiftLeft' || event.code === 'ShiftRight') {
      event.preventDefault(); if (!event.repeat && !hotkeys.has(event.code)) { hotkeys.add(event.code); void boost() }; return
    }
    if (event.target?.closest?.('input,select,textarea,[contenteditable="true"]') && event.code !== 'F8') return
    const action = keyAction(event.code)
    if (action) { event.preventDefault(); if (!event.repeat) press(`key:${event.code}`, action) }
  }
  function keyup(event) { hotkeys.delete(event.code); if (keyAction(event.code)) release(`key:${event.code}`) }
  function deactivate(reason) {
    // A retained connection token does not mean this page still owns motion.
    // Always invalidate asynchronous activation, but park only active or pending
    // activation; a cancelled late connect parks when its new token arrives.
    if ((model.enabled || activating || connecting) && (session || model.status.session)) return stop(reason)
    clearInput()
    return stopping || undefined
  }
  function loseFocus() { void deactivate('页面已失去焦点') }
  function visibility() { if (document.hidden) loseFocus() }
  function start() {
    void poll(); heartbeatTimer = setInterval(() => { if (model.enabled && isActive()) void sendIntent() }, 70)
    window.addEventListener('keydown', keydown); window.addEventListener('keyup', keyup)
    window.addEventListener('blur', loseFocus); window.addEventListener('pagehide', loseFocus)
    document.addEventListener('visibilitychange', visibility)
  }
  function dispose() {
    void deactivate('页面已关闭'); destroyed = true; clearInterval(heartbeatTimer); clearTimeout(pollTimer)
    window.removeEventListener('keydown', keydown); window.removeEventListener('keyup', keyup)
    window.removeEventListener('blur', loseFocus); window.removeEventListener('pagehide', loseFocus)
    document.removeEventListener('visibilitychange', visibility)
  }
  return Object.assign(model, {scan,connect,disconnect,enable,stop,brake,boost,profile,changeRoute,deactivate,press,release,sendIntent,keydown,keyup,start,dispose})
}
export function useCarControl(options) {
  const control = createCarControl(options)
  onMounted(control.start); onBeforeUnmount(control.dispose)
  return control
}
