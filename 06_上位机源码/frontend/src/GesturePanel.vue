<template>
  <section :class="['gesture-panel',{sp24:state.firmware === 'SP24'}]" aria-label="主页面手势控制">
    <header class="gesture-heading">
      <h2>手势控制</h2>
      <button class="gesture-toggle" :data-state="view.enabled" :disabled="!canToggle" :aria-pressed="view.enabled === null ? undefined : view.enabled" :aria-label="view.enabled ? '关闭手势' : '开启手势'" :title="!canToggle ? '停止摆控并取得参数读回后可修改' : '手势开关'" @click="$emit('toggle', view.enabled ? 0 : 1)"><span>{{ view.enabled === null ? '未就绪' : view.enabled ? '已开启' : '已关闭' }}</span><i aria-hidden="true"></i></button>
    </header>
    <p v-if="state.firmware === 'SP24' && view.handoff.startsWith('手势已屏蔽')" class="gesture-warning" role="status">起摆期间手势暂停，请使用停止摆控。</p>
    <div class="gesture-recognition" aria-label="最近识别" aria-live="polite">
      <GestureIcon :kind="recognitionIcon" />
      <div><span>最近识别</span><strong :title="view.recognition">{{ recognitionHeadline }}</strong></div>
    </div>
    <dl class="gesture-results" aria-live="polite">
      <div><dt>请求</dt><dd :title="view.request">{{ view.request.startsWith('未报告接受结果') ? '—' : view.request }}</dd></div>
      <div><dt>摆控</dt><dd :title="view.actual">{{ view.actual === '无新状态读回' ? '—' : view.actual }}</dd></div>
    </dl>
    <p v-if="command" class="gesture-command" role="status">开关写入：{{ requestLabel(command) }}<small>{{ command.message }}</small></p>
    <button class="button stop gesture-stop" @click="$emit('stop')"><svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><rect x="4" y="4" width="8" height="8" rx="1" /></svg>停止摆控</button>
    <div class="gesture-guide" aria-label="手势操作提示">
      <p v-for="item in guideItems" :key="item.label" :title="item.hint"><GestureIcon :kind="item.icon" /><b>{{ item.label }}</b><span>{{ item.short }}</span></p>
    </div>
    <details class="operator-help gesture-detail"><summary>操作说明</summary><div class="help-content"><p>{{ guide.note }}</p><dl class="gesture-reference"><div v-for="item in guide.items" :key="item.label"><dt>{{ item.label }}</dt><dd>{{ item.hint }}</dd></div></dl><p>识别记录：{{ view.recognition }}</p><p>请求结果：{{ view.request }}</p><p>实际摆控：{{ view.actual }}</p><template v-if="state.firmware === 'SP24'"><p>传感器当前：{{ view.sensor }}</p><p>接管与屏蔽：{{ view.handoff }}</p></template><p>接受请求不代表实物动作完成；修改开关后以实际读回为准。</p></div></details>
  </section>
</template>
<script setup>
import {computed} from 'vue'
import {canConfigure, requestLabel} from './hostPresentation.js'
import {gesturePresentation, gestureGuide} from './gesturePresentation.js'
import GestureIcon from './GestureIcon.vue'
const props = defineProps({state:{type:Object,required:true},busy:Boolean,command:{type:Object,default:null}})
defineEmits(['toggle','stop'])
const view = computed(() => gesturePresentation(props.state))
const recognitionHeadline = computed(() => view.value.recognition.startsWith('尚无') ? '尚未识别' : view.value.recognition.startsWith('识别异常') ? view.value.recognition : view.value.recognition.split(' · ')[0])
const guide = computed(() => gestureGuide(props.state.firmware))
const iconForLabel={'右滑':'right','左滑':'left','上滑':'up','下滑':'down','左／右滑':'horizontal','顺／逆时针':'turn','顺时针':'turn','逆时针':'reverse-turn','挥手':'hand'}
const recognitionIcon = computed(()=>iconForLabel[recognitionHeadline.value] || 'hand')
const guideItems = computed(()=>guide.value.items.map(item=>({...item,icon:iconForLabel[item.label] || 'hand',short:item.label==='顺／逆时针'?'平衡后转圈':item.label==='左／右滑'?'平衡后定位':item.label==='挥手'?(props.state.firmware==='SP24'?'平衡后停止':'停止摆控'):item.label==='左滑'?'取消并保持':'起摆'})))
const configurable = computed(() => canConfigure(props.state))
const canToggle = computed(() => configurable.value && view.value.enabled !== null && !props.busy)
</script>
