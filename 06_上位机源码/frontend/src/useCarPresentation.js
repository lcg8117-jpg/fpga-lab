import { onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { createDemoSequence } from './carSimulation.js'

export function useCarPresentation() {
  const demo = createDemoSequence(performance.now())
  const telemetry = shallowRef(demo.sample(performance.now()))
  const running = ref(false)
  const phase = ref(demo.label)
  let timer = null
  const sync = () => {
    const now = performance.now()
    demo.tick(now)
    telemetry.value = demo.sample(now)
    running.value = demo.running
    phase.value = demo.label
  }
  function start() {
    demo.start(performance.now())
    sync()
  }
  function stop(message = '待机 / 回正') {
    demo.stop(performance.now(), message)
    sync()
  }
  const cancel = () => { if (demo.running) stop('演示已暂停 / 回正') }
  const visibility = () => { if (document.hidden) cancel() }
  onMounted(() => {
    timer = setInterval(sync, 50)
    window.addEventListener('blur', cancel)
    document.addEventListener('visibilitychange', visibility)
  })
  onBeforeUnmount(() => {
    clearInterval(timer)
    window.removeEventListener('blur', cancel)
    document.removeEventListener('visibilitychange', visibility)
    demo.stop(performance.now())
  })
  return { telemetry, running, phase, start, stop }
}
