<template>
  <div class="waveform-panel" @pointerenter="enterHistory" @pointermove="trackPointer" @pointerleave="leaveHistory">
    <div ref="chartRef" class="waveform-chart" role="img" :aria-label="(firmware === 'SP24' || isFd2(firmware)) ? `最近${windowMs / 1000}秒摆角与参考、旋臂与参考及目标、实际PWM板端时间波形；局部原点计数${originCount || '未知'}` : `最近${windowMs / 1000}秒摆杆偏角、摆杆角速度与PWM波形`"></div>
    <div v-if="!hasData" class="waveform-empty" role="status">暂无数据</div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { createWaveformOption, updateWaveformChart } from './waveform.js'
import { createFd2WaveformOption } from './fd2Waveform.js'
import { isFd2 } from './fd2Presentation.js'
import { createPreciseWaveformOption } from './preciseWaveform.js'

const props = defineProps({
  samples: { type: Array, default: () => [] },
  now: { type: Number, required: true },
  events: { type: Array, default: () => [] },
  layout: { type: String, default: 'stacked' },
  firmware: { type: String, default: '' },
  originCount: { type: String, default: '' },
  session: { type: String, default: '' },
  angleExtent: { type: Number, default: 60 },
})
const emit = defineEmits(['window-change'])
const windowMs = ref(10000)
const chartRef = ref(null)
const hasData = ref(false)
let chart = null
let observer = null
let frame = null
let pointer = null
let preciseMode = null
let fd2Axes = {}

function resetScale() {
  fd2Axes = {}
  scheduleRender()
}
defineExpose({resetScale})

function trackPointer(event) {
  pointer = { clientX:event.clientX, clientY:event.clientY }
}
function enterHistory(event) {
  trackPointer(event)
  setWindow(isFd2(props.firmware) ? 120000 : 30000)
}
function leaveHistory() {
  pointer = null
  chart?.dispatchAction({ type:'updateAxisPointer', currTrigger:'leave' })
  chart?.dispatchAction({ type:'hideTip' })
  setWindow(10000)
}

function setWindow(value) {
  if (windowMs.value === value) return
  windowMs.value = value
  emit('window-change', value)
}

function render() {
  frame = null
  if (!chart || !chartRef.value) return
  const precise = props.firmware === 'SP24'
  const fd2 = isFd2(props.firmware)
  // Different firmware has a different series set. Clear once at a mode change,
  // then preserve tooltip/cursor models during normal live updates as before.
  if (preciseMode !== null && preciseMode !== precise) chart.clear()
  preciseMode = precise
  const createOption = fd2 ? createFd2WaveformOption : precise ? createPreciseWaveformOption : createWaveformOption
  const option = createOption(props.samples, props.now, chart.getHeight(), props.events, chart.getWidth(), windowMs.value, props.layout,
    {previousAxes:fd2Axes[windowMs.value] || [], angleExtent:props.angleExtent})
  if (fd2) fd2Axes[windowMs.value] = option.yAxis.map((axis,i) =>
    option.series.some(line => line.yAxisIndex === i && line.data.some(point => Number.isFinite(point[1])))
      ? axis : fd2Axes[windowMs.value]?.[i])
  const originCount = props.originCount
  if (precise && /^-?\d+$/.test(originCount)) {
    const formatter = option.tooltip.formatter
    option.tooltip.formatter = params => `局部原点计数：${originCount}<br/>${formatter(params)}`
  }
  hasData.value = option.series.some(series => series.data.some(point => point[1] !== null))
  updateWaveformChart(chart, option)
  if (pointer) {
    // Rolling/expanding the domain changes sample indices beneath a stationary cursor.
    // Re-resolve its pixel position so the tooltip and all linked axes use the same time.
    const bounds = chartRef.value.getBoundingClientRect()
    chart.dispatchAction({ type:'updateAxisPointer', x:pointer.clientX - bounds.left, y:pointer.clientY - bounds.top })
  }
}

function scheduleRender() {
  if (chart && frame === null) frame = requestAnimationFrame(render)
}

// The parent supplies fresh snapshots, so a shallow watch avoids traversing all telemetry every update.
watch(() => [props.samples, props.now, props.events, props.layout, props.firmware, props.originCount, windowMs.value], scheduleRender)
watch(() => [props.firmware, props.session], resetScale)
watch(() => props.angleExtent, () => {
  for (const axes of Object.values(fd2Axes)) axes[0] = undefined
  scheduleRender()
})

onMounted(() => {
  chart = echarts.init(chartRef.value)
  render()
  observer = new ResizeObserver(() => {
    if (!chart || !chartRef.value) return
    const { clientWidth, clientHeight } = chartRef.value
    if (clientWidth > 0 && clientHeight > 0) {
      chart.resize({ width: clientWidth, height: clientHeight, silent: true })
      scheduleRender()
    }
  })
  observer.observe(chartRef.value)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  if (frame !== null) cancelAnimationFrame(frame)
  frame = null
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.waveform-panel {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
  min-width: 0;
}

.waveform-chart {
  position: absolute;
  inset: 0;
}

.waveform-empty {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  padding: 6px 10px;
  background: #242622;
  color: #A6AA9B;
  font-size: 12px;
  pointer-events: none;
  white-space: nowrap;
}
</style>
