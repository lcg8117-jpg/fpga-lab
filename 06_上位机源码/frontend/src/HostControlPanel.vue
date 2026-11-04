<template>
  <section class="host-control-settings" aria-label="SG21、SA21 和 SP24 摆控设置">
    <h3>实机摆控 · {{ state.firmware || 'SG21 / SA21 / SP24' }}</h3>
    <p class="host-link-status" role="status">{{ connectionLabel }}</p>
    <p v-if="state.error" class="host-error">{{ state.error }}</p>
    <form class="host-connection" @submit.prevent="saveConnection">
      <label>本机 Host 端口<input v-model.number="connection.bridge_port" type="number" min="1024" max="65535" required /></label>
      <label>固件版本<select v-model="connection.firmware_variant"><option value="auto">自动识别</option><option>SG21</option><option>SA21</option><option>SP24</option></select></label>
      <button class="button compact" type="submit">应用连接设置</button>
    </form>
    <p>先启动对应版本的配套 Host，在 Host 中确认固件并连接串口。网页启动不会连接串口、恢复参数或起摆。</p>
    <div class="debug-actions">
      <button class="button stop compact" @click="stop">STOP 停止摆控</button>
      <button class="button primary compact" :disabled="!canRun" @click="run">RUN 自动起摆</button>
      <button class="button compact" :disabled="!configurable || busy" @click="send('balance')">仅平衡启动</button>
      <button class="button compact" :disabled="!configurable || busy" @click="send('zero')">标定硬件零点</button>
      <button class="button compact" :disabled="!state.status_fresh || busy || ![1, 7].includes(state.fault)" @click="send('clear-fault')">清除 F01 / F07</button>
    </div>
    <p>修改参数前须 STOP 并读回 PWM=0、无运行会话。启动仍需有效零点和完整 PID 读回；Host 会独立核对条件。</p>
    <dl class="engineering-readouts host-readouts">
      <div><dt>零点</dt><dd>{{ state.status_fresh ? (state.status?.zero_valid ? '有效' : '未标定') : '--' }}</dd></div>
      <div><dt>实际 PWM</dt><dd>{{ finite(state.duty_actual_percent, 2) }} %</dd></div>
      <div><dt>故障码</dt><dd :class="{ 'fault-text':state.fault }">{{ Number.isInteger(state.fault) ? `0x${state.fault.toString(16).padStart(2,'0').toUpperCase()}` : '--' }}</dd></div>
      <div><dt>编码器</dt><dd>{{ state.encoder ?? '--' }}</dd></div>
    </dl>

    <details v-if="isSp24" class="host-parameter-section sp24-presets">
      <summary>SP24 明确选择参数预设</summary>
      <p>选择只在本地预览。提交后由 Host 核对停止状态、只读前提，并逐项写入与独立读回；不会自动标零、起摆或开启自适应。</p>
      <label>预设<select v-model="presetId" aria-label="SP24 参数预设"><option value="">请选择</option><option v-for="preset in presets" :key="preset.id" :value="preset.id">{{ preset.label }}</option></select></label>
      <template v-if="selectedPreset">
        <p>{{ selectedPreset.id === 'field_baseline' ? '用户提供的现场回读基线。' : selectedPreset.id === 'field_swing70_230' ? '现场起摆设置，独立提交。' : '独立工程候选，尚未实物验收；不会自动与其他候选组合。' }}</p>
        <dl class="preset-preview"><div v-for="(value,name) in selectedPreset.parameters" :key="name"><dt>{{ parameterLabel(name) }}</dt><dd>{{ value }}</dd></div></dl>
        <p v-if="Object.keys(selectedPreset.expected_readonly || {}).length">只读前提：{{ Object.entries(selectedPreset.expected_readonly).map(([name,value]) => `${parameterLabel(name)}=${value}`).join('，') }}</p>
      </template>
      <p v-if="presetsError" class="host-error">{{ presetsError }}</p>
      <button class="button primary compact" :disabled="!configurable || busy || !selectedPreset" @click="applyPreset">应用所选预设并逐项读回</button>
    </details>

    <form class="host-parameter-section" @submit.prevent="send('restore-pid', { value:{ ...pidDraft } })">
      <h3>PID 与滤波 · 九项整组恢复</h3>
      <p>输入框是待提交值；下方另列实际读回。不会自动填入历史参考参数。</p>
      <div class="pid-fields">
        <label v-for="field in pidFields" :key="field.name">{{ field.label }}
          <input v-model.number="pidDraft[field.name]" type="number" :min="field.minimum" :max="field.maximum" step="1" required />
          <small>实读 {{ actual(field.name) }}</small>
        </label>
      </div>
      <div class="debug-actions">
        <button class="button compact" type="button" :disabled="!pidReadable" @click="copyPid">实读值填表</button>
        <button class="button primary compact" type="submit" :disabled="!configurable || busy || !pidValid">提交九项并读回</button>
      </div>
    </form>

    <details class="host-parameter-section">
      <summary>两段起摆参数</summary>
      <p>各项独立提交并读回；前向和反向的计时均为命令阶段时间。</p>
      <div class="debug-actions"><button class="button compact" :disabled="!state.status_fresh" @click="copySwing">实读值填表</button></div>
      <form v-for="field in swingFields" :key="field.name" class="host-parameter-row" @submit.prevent="send('set', {name:field.name,value:swingDraft[field.name]})">
        <label>{{ field.label }} <small>{{ field.unit }}</small></label>
        <input v-model.number="swingDraft[field.name]" type="number" :min="field.minimum" :max="field.maximum" step="1" required />
        <span>实读 {{ actual(field.name) }}</span>
        <button class="button compact" :disabled="!configurable || busy" type="submit">写入</button>
      </form>
    </details>

    <section class="host-parameter-section">
      <h3>手势{{ hasAdaptive ? '与平衡后自适应' : '' }}</h3>
      <form v-for="field in integrationFields" :key="field.name" class="host-parameter-row" @submit.prevent="send('set',{name:field.name,value:integrationDraft[field.name]})">
        <label>{{ field.label }}</label>
        <select v-model.number="integrationDraft[field.name]" required>
          <option value="" disabled>请选择</option><option :value="0">0 关闭</option><option :value="1">1 启用</option>
          <option v-if="field.name === 'adaptive_mode'" :value="2">2 影子计算</option>
        </select>
        <span>实读 {{ actual(field.name) }}</span>
        <button class="button compact" :disabled="!configurable || busy" type="submit">写入</button>
      </form>
      <form v-for="field in positionFields" :key="field.name" class="host-parameter-row" @submit.prevent="send('set',{name:field.name,value:integrationDraft[field.name]})">
        <label>{{ field.label }}</label>
        <input v-if="field.name === 'move_degrees'" v-model.number="integrationDraft[field.name]" type="number" :min="field.minimum" :max="field.maximum" step="1" required />
        <select v-else v-model.number="integrationDraft[field.name]" required><option value="" disabled>请选择</option><option :value="1">左滑为负方向</option><option :value="0">左滑为正方向</option></select>
        <span>实读 {{ actual(field.name) }}</span>
        <button class="button compact" :disabled="!configurable || busy" type="submit">写入</button>
      </form>
      <p>{{ guide.items.map(item => `${item.label}：${item.hint}`).join('；') }}。</p>
      <p>{{ guide.note }}</p>
      <template v-if="isSp24">
        <p>{{ gestureView.recognition }}；{{ gestureView.request }}</p>
        <p>传感器：{{ gestureView.sensor }}；接管与屏蔽：{{ gestureView.handoff }}。事件和当前状态来自独立读回。</p>
        <details v-if="state.gesture_runtime" class="gesture-runtime-details"><summary>传感器与接管诊断</summary>
          <p>查询序号 {{ state.gesture_runtime.poll_sequence }}，板时 {{ state.gesture_runtime.timestamp_ms }} ms；此处为该页保留读回，是否新鲜以当前状态提示为准。</p>
          <dl class="preset-preview"><div v-for="field in runtimeFields" :key="field.name"><dt>{{ field.label }}</dt><dd>{{ runtimeValue(field.name) }}</dd></div></dl>
        </details>
      </template>
      <p v-else-if="state.gesture">最近手势：{{ gestureNames[state.gesture.code] || state.gesture.code }} · 事件 {{ state.gesture.sequence }} · 错误 {{ state.gesture.error }} · {{ state.gesture.busy ? '转圈忙' : '转圈空闲' }}。此页不单独证明传感器已就绪。</p>
      <template v-if="hasAdaptive">
        <dl class="engineering-readouts host-readouts">
          <div><dt>选择 / 生效模式</dt><dd>{{ state.adaptive?.selected ?? '--' }} / {{ state.adaptive?.effective ?? '--' }}</dd></div>
          <div><dt>配置状态</dt><dd>{{ profileLabel }}</dd></div>
          <div><dt>实际 Kp / Ki / Kd</dt><dd>{{ gain('kp_q24') }} / {{ gain('ki_q24') }} / {{ gain('kd_q24') }}</dd></div>
          <div><dt>状态 / 增益时刻</dt><dd>{{ state.adaptive?.sample_ms ?? '--' }} / {{ state.adaptive_gains?.sample_ms ?? '--' }} ms</dd></div>
        </dl>
        <p>仅在实际 BALANCE 后生效。状态页和增益页分别读取，时刻不同表示不同采样；此功能只调整位置外环。</p>
        <button v-if="isSp24" class="button compact" :disabled="!engineeringProfile" @click="loadEngineering">载入 E 工程候选（仅影子）</button>
        <details class="host-profile-import">
          <summary>导入自适应配置 JSON</summary>
          <p>{{ profileIsEngineering ? '该配置不具备实机资格，只能用于影子观测。导入后仍为关闭，需停止时另行显式选择模式 2。' : '配置须含真实 Ku / Tu 和误差尺度；空模板不能提交。导入后仍保持关闭，不会自动 RUN。' }}</p>
          <a :href="`${API}/profile-template`" download="machine_profile.template.json">下载配置模板</a>
          <input type="file" accept="application/json,.json" aria-label="自适应配置 JSON" @change="readProfile" />
          <p v-if="profileName">已选择：{{ profileName }}</p>
          <label class="host-checkbox"><input v-model="controlQualified" type="checkbox" :disabled="profileIsEngineering" />声明该配置已通过实机资格筛选（仿真配置禁止勾选）</label>
          <button class="button compact" :disabled="!configurable || busy || !profile" @click="send('load-profile',{value:{profile,control:controlQualified}})">导入、校验并逐项读回</button>
        </details>
      </template>
    </section>

    <section class="host-requests" aria-label="操作结果" aria-live="polite">
      <h3>操作结果</h3><p v-if="!records.length">尚未从网页提交操作。</p>
      <article v-for="record in records" :key="record.request_id" :class="['host-request',record.state]">
        <strong>{{ actionNames[record.action] || record.action }} · {{ requestLabel(record) }}</strong>
        <span>{{ record.message }}</span><code>{{ record.request_id }}</code>
        <button class="text-button" @click="query(record)">按 ID 查询结果</button>
      </article>
      <p v-if="notice" role="status">{{ notice }}</p>
    </section>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { canConfigure, requestLabel, reconcileRequest } from './hostPresentation.js'
import { gesturePresentation, gestureGuide } from './gesturePresentation.js'
const props=defineProps({state:{type:Object,required:true}})
const emit=defineEmits(['event'])
const API='http://127.0.0.1:8000'
const connection=reactive({bridge_port:8371,firmware_variant:'auto'})
const pidDraft=reactive({}),swingDraft=reactive({}),integrationDraft=reactive({})
const records=ref([]),notice=ref(''),inflight=ref(0),profile=ref(null),profileName=ref(''),controlQualified=ref(false)
const presets=ref([]),presetId=ref(''),presetsError=ref(''),engineeringProfile=ref(null),profileIsEngineering=ref(false)
let presetGeneration=0
const isSp24=computed(()=>props.state.firmware === 'SP24')
const hasAdaptive=computed(()=>['SA21','SP24'].includes(props.state.firmware))
const selectedPreset=computed(()=>presets.value.find(preset=>preset.id === presetId.value))
const guide=computed(()=>gestureGuide(props.state.firmware))
const gestureView=computed(()=>gesturePresentation(props.state))
const runtimeFields=[{name:'ready',label:'传感器 ready'},{name:'enabled',label:'手势开启'},{name:'blocked',label:'手势屏蔽'},
  {name:'fresh_balance_ready',label:'实际接管许可'},{name:'motor',label:'电机许可'},{name:'zero',label:'零点有效'},
  {name:'estop',label:'急停'},{name:'fault',label:'故障'},{name:'swing_state',label:'起摆阶段码'},
  {name:'handoff',label:'交接请求'},{name:'auto_armed',label:'自动会话'},{name:'manual_run',label:'手动会话'},
  {name:'deadman',label:'平衡许可'},{name:'hold_initialized',label:'HOLD 初始化'},{name:'rotation_active',label:'轨迹运行'},{name:'busy',label:'轨迹忙'}]
const pidNames=['angle_kp','angle_ki','angle_kd','position_kp','position_ki','position_kd','ki_enable','angle_d_filter','angle_window']
const pidFields=computed(()=>pidNames.map(name=>props.state.parameter_specs?.find(spec=>spec.name===name)).filter(Boolean))
const swingFields=computed(()=>(props.state.parameter_specs||[]).filter(spec=>spec.id<0x20))
const integrationFields=computed(()=>(props.state.parameter_specs||[]).filter(spec=>['gesture_enable','adaptive_mode','adaptive_integral'].includes(spec.name)))
const positionFields=computed(()=>isSp24.value ? (props.state.parameter_specs||[]).filter(spec=>['move_degrees','left_negative'].includes(spec.name)) : [])
const configurable=computed(()=>canConfigure(props.state))
const busy=computed(()=>inflight.value>0 || records.value.some(record=>['queued','pending','accepted','stopping'].includes(record.state)))
const pidReadable=computed(()=>pidNames.every(name=>props.state.parameters?.[name]?.current))
const pidValid=computed(()=>pidFields.value.length===9 && pidFields.value.every(field=>Number.isInteger(pidDraft[field.name]) && pidDraft[field.name]>=field.minimum && pidDraft[field.name]<=field.maximum))
const connectionLabel=computed(()=>!props.state.host_online ? '等待配套 Host' : !props.state.connected ? `${props.state.firmware} Host 已启动 · 串口未连接` : !props.state.status_fresh ? '串口已连接 · 等待新状态读回' : `${props.state.firmware} · 真实状态已读回`)
const profileLabel=computed(()=>props.state.adaptive == null ? '--' : (props.state.adaptive.profile_flags&1) ? ((props.state.adaptive.profile_flags&2) ? '有效 / 已声明实机资格' : '有效 / 影子配置') : '无效 / 未配置')
const actionNames={run:'自动起摆',stop:'停止摆控',zero:'零点标定',balance:'仅平衡启动','clear-fault':'清除故障','restore-pid':'恢复九项 PID',set:'参数写入','load-profile':'导入自适应配置','apply-preset':'应用所选预设'}
const gestureNames={0:'无',1:'右滑',2:'左滑',3:'上滑',4:'下滑',5:'靠近',6:'远离',7:'顺时针',8:'逆时针',9:'挥手'}
const eventIds=new Set()
function finite(value,digits=3){return Number.isFinite(value)?value.toFixed(digits):'--'}
function gain(name){const value=props.state.adaptive_gains?.[name];return Number.isFinite(value)?finite(value/2**24,5):'--'}
function actual(name){const row=props.state.parameters?.[name];return row?.current ? row.value : '--'}
function parameterLabel(name){return props.state.parameter_specs?.find(field=>field.name === name)?.label || ({encoder_mode:'编码器模式',position_gamma:'位置 D 滤波'}[name]) || name}
function runtimeValue(name){const value=props.state.gesture_runtime?.[name];return typeof value === 'boolean' ? value ? '1' : '0' : value ?? '--'}
function applyPreset(){if(isSp24.value && configurable.value && !busy.value && selectedPreset.value)return send('apply-preset',{value:selectedPreset.value})}
function loadEngineering(){if(!isSp24.value || !engineeringProfile.value)return;profile.value=engineeringProfile.value;profileName.value='本包 E 工程候选（仅影子资格，尚未发送）';controlQualified.value=false;profileIsEngineering.value=true;notice.value='工程候选已载入本地，尚未发送；不具备实机资格'}
function copyPid(){for(const name of pidNames)pidDraft[name]=props.state.parameters[name].value}
function copySwing(){for(const field of swingFields.value){const row=props.state.parameters?.[field.name];if(row?.current)swingDraft[field.name]=row.value}}
function update(record,result){
  Object.assign(record,result)
  if(['verified','balance','stopped'].includes(record.state) && !eventIds.has(record.request_id)){
    eventIds.add(record.request_id);emit('event',`${actionNames[record.action]||record.action} · 读回确认`)
  }
}
async function jsonRequest(path,body){
  const controller=new AbortController()
  const timer=setTimeout(()=>controller.abort(),5000)
  try{
    const response=await fetch(API+path,{signal:controller.signal,...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})})
    const data=await response.json()
    if(!response.ok){const detail=data.detail;const error=new Error(typeof detail==='object'?(detail?.message||JSON.stringify(detail)):(detail||'请求失败'));error.result=detail;throw error}
    return data
  }finally{clearTimeout(timer)}
}
async function send(action,extra={}){
  const record=reactive({request_id:crypto.randomUUID(),action,...extra,state:'pending',message:'正在提交给配套 Host',host_session:props.state.session,host_firmware:props.state.firmware})
  records.value=[record,...records.value].slice(0,12);inflight.value++
  try{update(record,await jsonRequest('/command',{action,request_id:record.request_id,...extra}))}
  catch(error){update(record,{state:'indeterminate',message:`${error.message}；未自动重发，请保留请求 ID`,...(typeof error.result==='object'&&!Array.isArray(error.result)?error.result:{})})}
  finally{inflight.value--}
}
function stop(){return send('stop')}
const canRun=computed(()=>configurable.value && !busy.value)
function run(){if(canRun.value)return send('run')}
const runRequest=computed(()=>records.value.find(record=>record.action==='run') ?? null)
const gestureRequest=computed(()=>records.value.find(record=>record.action==='set' && record.name==='gesture_enable') ?? null)
function setGesture(value){if(configurable.value && !busy.value && [0,1].includes(value))return send('set',{name:'gesture_enable',value})}
defineExpose({stop,run,canRun,runRequest,setGesture,busy,gestureRequest})
async function query(record){try{update(record,await jsonRequest(`/requests/${encodeURIComponent(record.request_id)}`))}catch(error){notice.value=`查询失败：${error.message}。未重发操作。`}}
async function saveConnection(){try{await jsonRequest('/config',{...connection});notice.value='连接设置已保存，等待对应 Host'}catch(error){notice.value=error.message}}
async function readProfile(event){
  profile.value=null;profileName.value='';controlQualified.value=false;profileIsEngineering.value=false
  const file=event.target.files?.[0];if(!file)return
  try{
    if(file.size>4096)throw new Error('配置文件过大')
    const value=JSON.parse(await file.text())
    if(!value||Array.isArray(value)||typeof value!=='object')throw new Error('需要 JSON 对象')
    profile.value=value;profileName.value=file.name
    profileIsEngineering.value=value.provenance?.kind === 'synthetic' || value.provenance?.control_qualified === false
    notice.value=profileIsEngineering.value ? '文件已载入，仅影子资格，尚未发送' : '文件已载入，尚未发送'
  }catch(error){notice.value=`配置读取失败：${error.message}`}
}
watch(()=>props.state,state=>{for(const record of records.value)update(record,reconcileRequest(record,state))})
watch(()=>props.state.firmware,async firmware=>{
  const current=++presetGeneration
  presets.value=[];presetId.value='';presetsError.value='';engineeringProfile.value=null
  profile.value=null;profileName.value='';controlQualified.value=false;profileIsEngineering.value=false
  if(firmware !== 'SP24')return
  try{const result=await jsonRequest('/presets');if(current===presetGeneration){presets.value=result.presets || [];engineeringProfile.value=result.engineering_profile || null}}
  catch(error){if(current===presetGeneration)presetsError.value=`预设读取失败：${error.message}，请确认后台已更新。`}
},{immediate:true})
onMounted(async()=>{try{Object.assign(connection,await jsonRequest('/config'))}catch(error){notice.value=`尚未读取连接设置：${error.message}`}})
</script>
