<template>
  <section class="car-control-settings">
    <div class="car-control-heading"><span class="car-waiting-state" :class="{live:control.apiOnline && control.status.connected}" title="BLE 蓝牙 · WH-BLE102"><i></i>{{ control.apiOnline ? control.status.connected ? '小车已连接' : '小车未连接' : control.apiError ? '小车服务不可用' : '连接服务中' }}</span></div>
    <div class="car-connect-row">
      <button class="button compact" :disabled="!control.apiOnline || control.busy || control.status.connected" @click="control.scan()">{{ control.busy ? '处理中…' : '搜索小车' }}</button>
      <button class="button primary compact" :disabled="!control.apiOnline || control.busy || !control.selected || control.status.connected" @click="control.connect()">连接小车</button>
      <button v-if="control.status.connected" class="button compact" :disabled="control.busy" @click="control.disconnect()">断开</button>
    </div>
    <label class="car-device-select"><span class="sr-only">小车设备</span><select v-model="control.selected" aria-label="小车设备" :disabled="control.busy || control.status.connected"><option value="">选择小车</option><option v-for="device in control.devices" :key="device.id" :value="device.id">{{ device.name || '未命名设备' }} · {{ device.id }}</option></select></label>
    <div class="car-v83-speed"><label for="car-speed">速度 <output>{{ (control.speed_q8 / 256).toFixed(2) }} 计数/5ms</output></label><input id="car-speed" v-model.number="control.speed_q8" aria-label="小车速度" type="range" min="128" max="4096" step="128" @input="control.sendIntent()" /></div>
    <details class="car-v83-options"><summary>档位与轨迹</summary>
      <div class="car-profile"><span>反馈档位：{{ control.status.profile === 1 ? '快速' : control.status.profile === 0 ? '普通' : '--' }}</span><button class="button compact" :disabled="!control.canEnable || !!control.held.length || !!control.feedback?.active" @click="control.profile(control.status.profile === 1 ? 0 : 1)">切换{{ control.status.profile === 1 ? '普通' : '快速' }}档</button></div>
      <label>急加速峰值 <output>{{ (control.peak_q8 / 256).toFixed(2) }} 计数/5ms</output><input v-model.number="control.peak_q8" aria-label="急加速峰值" type="range" min="1024" max="6144" step="128" /></label>
      <label>手动转向幅度 <output>{{ control.steer_percent }}%</output><input v-model.number="control.steer_percent" aria-label="手动转向幅度" type="range" min="10" max="100" step="5" @input="control.sendIntent()" /></label>
      <label>轨迹<select :value="control.route" aria-label="小车轨迹" @change="control.changeRoute($event.target.value)"><option v-for="(name,index) in routes" :key="index" :value="index+1">{{ index+1 }} · {{ name }}</option></select></label>
      <label>慢段比例<select v-model.number="control.slow_percent" aria-label="慢段比例" @change="control.sendIntent()"><option :value="25">25%</option><option :value="50">50%</option><option :value="75">75%</option></select></label>
      <button class="button primary car-route-hold" aria-label="按住运行轨迹" :aria-pressed="control.held.includes('route')" :disabled="!control.enabled" @pointerdown.prevent="routePress" @pointerup="routeRelease" @pointercancel="routeCancel" @lostpointercapture="routeRelease" @keydown.enter.prevent="!$event.repeat && control.press('route:Enter','route')" @keyup.enter.prevent="control.release('route:Enter')" @blur="control.release('route:Enter')">按住运行轨迹 · F8</button>
    </details>
  </section>
</template>
<script setup>
const props = defineProps({control:{type:Object,required:true}})
const routes=['左圆形绕行','右圆形绕行','8 字示意','S 形绕行','走走停停','慢—快—慢直线扰动','直线加速—末端急刹','柔和正弦转向扰动']
function routePress(event){if(event.button!==0)return;event.currentTarget.setPointerCapture(event.pointerId);props.control.press(`route:pointer:${event.pointerId}`,'route')}
function routeRelease(event){props.control.release(`route:pointer:${event.pointerId}`)}
function routeCancel(event){routeRelease(event);void props.control.deactivate('轨迹指针已取消')}
</script>
