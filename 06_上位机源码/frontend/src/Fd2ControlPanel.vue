<template>
 <section class="host-control-panel fd2-controls">
  <form class="connection-form" @submit.prevent="saveConnection">
<label>Host 桥端口 <input v-model.number="connection.bridge_port" type="number" min="1024" max="65535" required>
</label>
<label>固件 <select v-model="connection.firmware_variant">
<option value="auto">自动识别最新固件</option>
<option v-for="v in versions" :key="v" :value="v">{{ versionLabels[v] }}</option>
</select>
</label>
<button class="button compact" :disabled="state.replay?.active">保存连接设置</button>
</form>
  <p v-if="state.firmware==='V4_USB_BLE'">连接由配套 Host 手动操作；USB 与蓝牙共用同一会话，位置优先走已就绪的蓝牙。</p>
  <p v-else>串口由配套 Host 显式连接；USB 与 BT10 命令链路独立。</p>
  <p v-if="['ADB1','ADB1_FASTP','ADB1_07'].includes(state.firmware)">ADB1 串口由配套窗口负责；网页请求经同一串口所有者发送。</p>
  <p v-if="state.replay?.active" class="host-error">{{ state.replay.label || '离线回放' }} · {{ state.replay.partial ? '不完整记录 · ' : '' }}全部硬件操作已禁用<span v-if="state.replay.crc_errors"> · CRC 错误 {{ state.replay.crc_errors }}</span><span v-if="state.replay.frame_errors"> · 无效帧 {{ state.replay.frame_errors }}</span></p>
  <p v-if="state.replay?.warning || state.replay?.error" class="host-error" role="status">{{ state.replay.warning || state.replay.error }}</p>
  <div class="debug-actions">
<button v-for="action in actions.filter(a=>!['position','trajectory','read-gains','set-gains','play-c1','cancel-c1'].includes(a))" :key="action" class="button compact" :class="{'stop':['stop','estop'].includes(action)}" :disabled="!canSend(action)" @click="send(action)">{{ labels[action] }}</button>
</div>
  <details v-if="actions.includes('set-gains')" class="pid-editor">
    <summary>PID 六参数与独立读回</summary>
    <p v-if="['ADB1_FASTP','ADB1_07'].includes(state.firmware)">输入框是草稿，S1 不会自动写入。原固件断电后需重新加载并写入；自适应由按钮显式开关，状态以设备读回为准。</p>
    <p v-else>R28 固件默认使用 S1；当前设备的参数以读回为准。两个积分固定为零，写入须先停止。</p>
    <div class="gain-grid"><label v-for="(name,i) in gainNames" :key="name">{{ name }}<input v-model.number="gainDraft[i]" type="number" min="0" max="10000" step="any"></label></div>
    <div class="debug-actions"><button class="button compact" :disabled="!canSend('read-gains')" @click="send('read-gains')">独立读回</button><button class="button compact" :disabled="!validGains || !canSend('set-gains') || (['V4_R28','V4_USB_BLE'].includes(state.firmware) && !state.status?.stopped_config_allowed)" @click="send('set-gains',gainDraft.slice())">写入六参数</button></div>
    <p>独立读回：{{ formatGains(state.pid_gains?.independent) }}<br>实际采用：{{ formatGains(state.pid_gains?.active) }}</p>
  </details>
  <div v-if="actions.includes('position')" class="debug-actions">
<label>位置／轨迹编号 <select v-model.number="target">
<option v-for="n in 6" :value="n" :key="n">{{ n }}</option>
</select>
</label>
<button class="button compact" :disabled="!canSend('position')" @click="send('position',target)">相对定位</button>
<button v-if="actions.includes('trajectory')" class="button compact" :disabled="!canSend('trajectory')" @click="send('trajectory',target)">有限轨迹</button>
</div>
  <p>ACK 只表示请求已接受；完成与到位以独立遥测为准。超时不自动重发。</p>
  <p v-if="notice" role="status">{{ notice }}</p>
<ol class="request-list">
<li v-for="r in records" :key="r.request_id">{{ labels[r.action] || r.action }} · {{ requestLabel(r) }} <small>{{ r.request_id }}</small> <button class="text-button" @click="query(r)">查询</button>
</li>
</ol>
 </section>
</template>
<script setup>
import {computed,onMounted,reactive,ref,watch} from 'vue'
import {fd2Actions,canDispatchFd2,commandFailure,swingLabel} from './fd2Presentation.js'
import {requestLabel,reconcileRequest} from './hostPresentation.js'
const props=defineProps({state:{type:Object,required:true}})
const versions=['ADB1_07','V4_USB_BLE','G3_C1','DS13'],API='http://127.0.0.1:8000'
const versionLabels={ADB1_07:'ADB1 07 / S1',V4_USB_BLE:'V4 USB + 蓝牙',G3_C1:'G3 C1',DS13:'DS13 现场手绘'}
const connection=reactive({bridge_port:8371,firmware_variant:'auto'}),target=ref(1),notice=ref(''),records=ref([]),inflight=ref(0),busy=computed(()=>inflight.value>0)
const labels={'stop':'STOP 停止','estop':'ESTOP 急停','zero':'显式抓零','balance':'手动平衡','clear-fault':'清除故障','start-swing':'按当前方案起摆','adapt-enable':'启用自适应','adapt-disable':'关闭自适应','hold':'保持当前位置','position':'相对定位','trajectory':'有限轨迹','play-c1':'播放 C1','cancel-c1':'取消 C1','set-gains':'写入参数','read-gains':'独立读回'}
const gainNames=['角 P','角 I','角 D','位置 P','位置 I','位置 D'],gainDraft=ref([53.78,0,210.14,17.67,0,616.61])
const validGains=computed(()=>gainDraft.value.length===6 && gainDraft.value.every(v=>typeof v==='number' && Number.isFinite(v) && v>=0 && v<=10000) && (!['V4_R28','V4_USB_BLE'].includes(props.state.firmware) || (gainDraft.value[1]===0 && gainDraft.value[4]===0)))
function formatGains(values){return Array.isArray(values)&&values.length===6 ? values.map(v=>Number.isFinite(v)?Number(v).toPrecision(6):'—').join(' / ') : '未读回'}
const actions=computed(()=>fd2Actions(props.state.firmware))
const runAction=computed(()=>actions.value.includes('start-swing')?'start-swing':'balance')
const canRun=computed(()=>canSend(runAction.value))
const runLabel=computed(()=>runAction.value==='start-swing'?`${swingLabel(props.state.swing?.profile)} 起摆`:'手动平衡')
function canSend(action){return canDispatchFd2(props.state,action,busy.value,props.state.presentation_now_ms??Date.now())}

async function request(path,body){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),5000);
try{const r=await fetch(API+path,{signal:controller.signal,...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
const d=await r.json();
if(!r.ok){const e=new Error(typeof d.detail==='string'?d.detail:d.detail?.message||'请求被拒绝');
e.httpStatus=r.status;
e.result=d.detail;
throw e;
}return d}
finally{clearTimeout(timer)}}
async function send(action,value){if(!actions.value.includes(action)||!canSend(action))return;
const record={request_id:crypto.randomUUID(),action,value,host_session:props.state.session,host_firmware:props.state.firmware,state:'pending'};
records.value=[record,...records.value].slice(0,12);
inflight.value++;
try{Object.assign(record,await request('/command',{request_id:record.request_id,action,...(value===undefined?{}:{value}),...(record.host_firmware==='V4_USB_BLE' && !['stop','estop'].includes(action)?{session:record.host_session}:{})}))}
catch(error){Object.assign(record,commandFailure(error));
notice.value=record.message}
finally{inflight.value--;
reconcileRecords(props.state)}}
async function query(record){try{Object.assign(record,await request('/requests/'+encodeURIComponent(record.request_id)));
reconcileRecords(props.state)}
catch(e){notice.value=e.message}}
async function saveConnection(){try{await request('/config',{...connection});
notice.value='连接设置已保存'}
catch(e){notice.value=e.message}}
function reconcileRecords(state){for(const r of records.value)Object.assign(r,reconcileRequest(r,state));records.value=[...records.value]}
watch(()=>props.state,state=>reconcileRecords(state))
onMounted(async()=>{try{const c=await request('/config');
connection.bridge_port=c.bridge_port;
if(['auto',...versions].includes(c.firmware_variant))connection.firmware_variant=c.firmware_variant
else notice.value=`原连接设置为 ${c.firmware_variant}；请保存自动识别或选择新版固件。`}
catch(e){notice.value=e.message}})
function run(){return send(actions.value.includes('start-swing')?'start-swing':'balance')}
function stop(){return send('stop')}
const runRequest=computed(()=>records.value.find(r=>['start-swing','balance'].includes(r.action)))
defineExpose({run,stop,canRun,runLabel,runRequest,busy,send,canSend,records,query})
</script>
<style scoped>
.connection-form,.debug-actions{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:16px 0}
.gain-grid{display:flex;flex-wrap:wrap;gap:8px}.gain-grid input{width:90px}
label{display:flex;gap:8px;align-items:center}
input,select{background:#292c28;color:#d7d8cd;border:1px solid #55594d;padding:7px}
p,li{color:#aeb2a3;line-height:1.7;font-size:13px}
small{font-size:11px;overflow-wrap:anywhere}
.request-list{padding-left:20px}

</style>
