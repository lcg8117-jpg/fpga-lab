<template>
  <div class="car-direction-pad" aria-label="小车方向控制">
    <button v-for="key in keys" :key="key.action" :class="['button','car-hold-button',key.action,{held:held.includes(key.action)}]" :aria-label="key.label" :title="key.key"
      :disabled="!enabled" :aria-pressed="held.includes(key.action)"
      @pointerdown.prevent="press($event,key.action)" @pointerup="release" @pointercancel="cancel" @lostpointercapture="release"
      @keydown="buttonKeyDown($event,key.action)" @keyup="buttonKeyUp" @blur="buttonBlur">
      <svg class="direction-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 19V5m-6 6 6-6 6 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg><span>{{ key.short }}</span>
    </button>
    <button class="button stop car-pad-stop" :disabled="!connected" title="停止小车并回正 · Esc" aria-label="停止小车并回正" @click="$emit('stop')"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1" /></svg><span>停止小车</span></button>
  </div>
</template>
<script setup>
defineProps({enabled:Boolean,connected:Boolean,held:{type:Array,default:()=>[]}})
const emit=defineEmits(['press','release','stop'])
const keys=[{action:'forward',label:'前进',short:'前进',key:'W / ↑'},{action:'left',label:'向左打方向',short:'左转',key:'A / ←'},
  {action:'right',label:'向右打方向',short:'右转',key:'D / →'},{action:'reverse',label:'后退',short:'后退',key:'S / ↓'}]
function press(event,action){if(event.button!==0)return;event.currentTarget.setPointerCapture(event.pointerId);emit('press',`pointer:${event.pointerId}`,action)}
function release(event){emit('release',`pointer:${event.pointerId}`)}
function cancel(event){release(event);emit('stop')}
function buttonKeyDown(event,action){if(event.code==='Enter'){event.preventDefault();if(!event.repeat)emit('press','button:Enter',action)}}
function buttonKeyUp(event){if(event.code==='Enter'){event.preventDefault();emit('release','button:Enter')}}
function buttonBlur(){emit('release','button:Enter')}
</script>
