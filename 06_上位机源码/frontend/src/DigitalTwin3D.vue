<template>
  <div ref="host" class="twin3d-host">
    <div class="twin3d-tools">
      <button type="button" title="左键旋转 · 滚轮缩放 · 右键平移" @click="resetView">重置视角</button>
      <button type="button" :disabled="!canAlignDown" title="实物自然下垂并静止时点击，仅调整 3D 显示" @click="alignDown">下垂对齐</button>
      <button v-if="calibrationOpen" type="button" @click="calibrationOpen = false">关闭标定</button>
    </div>
    <section v-if="calibrationOpen" class="twin3d-calibration" aria-label="姿态标定" @change="normalizeCalibration">
      <div class="calibration-heading"><b>姿态标定</b><span v-if="!isFd2(calibrationProfile)">仅调整 3D 映射</span></div>
      <label>θ 方向
        <select v-model.number="calibration.thetaDirection"><option :value="1">+1</option><option :value="-1">−1</option></select>
      </label>
      <label>θ 偏移 (°)<input v-model.number="calibration.thetaOffsetDeg" type="number" step="0.1" /></label>
      <label>α 方向
        <select v-model.number="calibration.alphaDirection"><option :value="1">+1</option><option :value="-1">−1</option></select>
      </label>
      <label>α 偏移 (°)<input v-model.number="calibration.alphaOffsetDeg" type="number" step="0.1" /></label>
      <label class="calibration-toggle">α 两点显示标定<input v-model="calibration.alphaTwoPointEnabled" type="checkbox" /></label>
      <template v-if="calibration.alphaTwoPointEnabled">
        <label>竖直向上读数 (°)<input v-model.number="calibration.alphaUprightDeg" type="number" step="0.001" /></label>
        <label>自然下垂读数 (°)<input v-model.number="calibration.alphaDownDeg" type="number" step="0.001" /></label>
        <p v-if="!alphaReferencesValid(calibration)" role="status">{{ isFd2(calibrationProfile) ? '参考点夹角须至少为 90°，两点标定未生效。' : '两参考点的最短夹角须至少为 90°；当前暂用原始角度映射。' }}</p>
        <p v-if="!isFd2(calibrationProfile)">两点校正后再应用 α direction / offset。参考读数可微调；关闭后恢复原始角度映射。</p>
      </template>
      <label>显示平滑 (秒)<input v-model.number="calibration.smoothing" type="number" min="0" max="0.05" step="0.005" /></label>
      <template v-if="!isFd2(calibrationProfile)">
        <p>0 = 关闭插值；两轴异常样本检查仍开启。参数自动保存在当前浏览器。</p>
        <p>BALANCE 微动轻量平滑。没有角速度支持的突跳需连续样本确认，不因等待超时而放行。</p>
        <p>模型零点：θ 沿 +X，α 竖直向上。</p>
      </template>
      <button type="button" @click="restoreCalibration">恢复默认标定</button>
    </section>
    <p v-if="modelError" class="twin3d-model-error" role="status">{{ modelError }}</p>
    <p v-if="modelPaused" class="twin3d-display-note" role="status">手绘中，模型暂停</p>
    <p v-if="calibrationNotice && !isFd2(calibrationProfile)" class="twin3d-display-note" role="status">{{ calibrationNotice }}</p>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { isFd2 } from './fd2Presentation.js'
import { createDrawRenderBudget } from './drawRenderBudget.js'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { disposePendulumModel } from './pendulumModel.js'
import { mountAssembly } from './pendulumAssembly.js'
import { createPendulumLoad } from './pendulumLoad.js'
import { createBenchSupport, setSceneMode } from './pendulumScene.js'
import { calibrationDefaults, calibrationStorageKey, normalizeCalibration as normalizeDisplayCalibration, alphaReferencesValid, displayAlphaDeg, calibrateDownPose } from './pendulumCalibration.js'
import pendulumAssetMetadata from './assets/models/ip570.meta.json' with { type:'json' }

const pendulumAssetUrl = new URL('./assets/models/ip570.glb',import.meta.url).href

const props = defineProps({
  sceneMode: { type:String, default:'bench' },
  firmware: { type:String, default:'' },
  session: { type:String, default:'' },
  armAngle: { type:Number, default:0 },
  pendulumAngle: { type:Number, default:0 },
  mode: { type:String, default:'WAITING' },
  sampleTime: { type:Number, default:0 }, // Existing received_at; repeated equal samples can confirm real motion.
  sampleTick: { type:Number, default:null }, // Existing FPGA timestamp (ms), independent of arrival bursts.
  armVelocity: { type:Number, default:null }, // Degrees/s, only used to validate the displayed pose.
  pendulumVelocity: { type:Number, default:null },
  pwmPercent: { type:Number, default:null },
  leftWheelCount: { type:Number, default:0 },
  rightWheelCount: { type:Number, default:0 },
  steeringRequest: { type:Number, default:750 },
  loaded: { type:Boolean, default:false }, // 人工选择的 3D 外观，不代表实际负载检测。
  drawingActive: { type:Boolean, default:false },
})

const host = ref(null)
const calibrationOpen = ref(false)
const modelError = ref('')
const modelPaused = ref(false)
const renderBudget = createDrawRenderBudget()
const calibrationNotice = ref('')
const calibrationProfile = ref(props.firmware)
const canAlignDown = computed(() => Number.isFinite(props.pendulumAngle) &&
  (['IDLE','ZERO','ARMED'].includes(props.mode) || (props.mode==='FAULT' && props.pwmPercent===0)))
let destroyed = false
function openCalibration(){ calibrationOpen.value = true }
defineExpose({ openCalibration })
const calibration = reactive(loadCalibration())
// Display-only sample conditioning, before calibration; never modifies telemetry.
const displayTuning = Object.freeze({
  jumpToleranceDeg: 2,
  velocityAllowance: 2,
  confirmationSamples: 4,
  confirmationSpanMs: 100,
  balanceSmoothing: .06,
  microMotionDeg: .25,
  fullSpeedDeg: 1,
})
const displayFloorY = -1
let renderer, scene, camera, controls, base, armPivot, pendulumPivot, resizeObserver
let environmentTarget
let pendulumLoad
let raf = 0
let lastFrameTime = null
let currentArm = 0
let currentPendulum = 0
let targetArm = 0
let targetPendulum = 0
const armSamples = createAngleSamples()
const pendulumSamples = createAngleSamples()
let lastSampleTick = null
const carWheels = []

function normalizedCalibration(value = {}){
  return normalizeDisplayCalibration(value, calibrationProfile.value)
}
function loadCalibration(){
  try {
    return normalizedCalibration(JSON.parse(window.localStorage.getItem(calibrationStorageKey(calibrationProfile.value))) || {})
  } catch {
    return calibrationDefaults(calibrationProfile.value)
  }
}
function restoreCalibration(){
  Object.assign(calibration, calibrationDefaults(calibrationProfile.value))
  calibrationNotice.value = '已恢复当前固件的显示参考'
}
function alignDown(){
  if(!canAlignDown.value)return
  Object.assign(calibration,calibrateDownPose(normalizedCalibration(calibration),props.pendulumAngle))
  calibrationNotice.value = `已按 ${props.pendulumAngle.toFixed(3)}° 对齐模型下垂；原始读数不变`
}
watch([()=>props.firmware,()=>props.session], ([firmware])=>{
  if((isFd2(firmware) || ['SG21','SA21','SP24'].includes(firmware)) && firmware!==calibrationProfile.value){
    calibrationProfile.value=firmware
    Object.assign(calibration,loadCalibration())
    calibrationNotice.value=''
  }
  // A new device/session can reuse its predecessor's board tick. It is a new pose sample.
  for(const samples of [armSamples,pendulumSamples])Object.assign(samples,createAngleSamples())
  lastSampleTick=null
})
function normalizeCalibration(){
  // Commit validation on change, allowing intermediate input such as '-' or '0.'.
  Object.assign(calibration, normalizedCalibration(calibration))
}
// Validate the two telemetry axes before applying calibration. Raw readouts,
// charts and control data remain untouched. A repeated packet is not new evidence.
watch(
  () => [props.firmware,props.session,props.armAngle, props.pendulumAngle, props.armVelocity, props.pendulumVelocity, props.sampleTime, props.sampleTick],
  () => {
    const tick = Number.isFinite(props.sampleTick) ? props.sampleTick
      : props.sampleTime > 0 ? props.sampleTime * 1000 : null
    if (tick !== null && tick === lastSampleTick) return
    const firstTimedSample = tick !== null && lastSampleTick === null
    const sampleClock = tick ?? performance.now()
    if (tick !== null && lastSampleTick !== null && tick < lastSampleTick){
      // A reboot OR a truncated timestamp can move the clock backward. Reset
      // timing only: neither case is evidence that both mechanical angles are zero.
      for (const samples of [armSamples, pendulumSamples]){
        samples.acceptedAt = sampleClock
        samples.candidate = null
        samples.confirmations = 0
      }
    }
    lastSampleTick = tick
    // FD2 positions come from fresh coherent T frames. D-frame speed has its
    // own timestamp and must not veto a newer encoder position during motion.
    const armValidationVelocity = isFd2(props.firmware) ? null : props.armVelocity
    acceptAngleSample(armSamples, props.armAngle, armValidationVelocity, sampleClock, firstTimedSample)
    acceptAngleSample(pendulumSamples, props.pendulumAngle, props.pendulumVelocity, sampleClock, firstTimedSample)
    mapPoseTargets()
  },
  { immediate:true },
)
watch(calibration, () => {
  const config = normalizedCalibration(calibration)
  mapPoseTargets()
  try {
    window.localStorage.setItem(calibrationStorageKey(calibrationProfile.value), JSON.stringify(config))
  } catch { /* Rendering still works when browser storage is unavailable. */ }
})
watch(() => props.loaded, loaded => {
  if (pendulumLoad) pendulumLoad.visible = loaded
})
watch(() => props.sceneMode, mode => { setSceneMode(base, mode); resize() })

function createAngleSamples(){
  return { initialized:false, value:0, acceptedAt:0, candidate:null, candidateSince:0, confirmations:0 }
}
function acceptAngleSample(samples, value, velocity, sampleClock, initialize){
  if (!Number.isFinite(value)) return
  const delta = THREE.MathUtils.radToDeg(angleDelta(
    THREE.MathUtils.degToRad(samples.value), THREE.MathUtils.degToRad(value),
  ))
  const tolerance = displayTuning.jumpToleranceDeg
  // Pair displacement with the time since THAT accepted position. Using only
  // the latest packet interval would freeze real motion after dropped samples.
  const dt = Math.max(.001, (sampleClock - samples.acceptedAt) / 1000)
  // Use the CURRENT velocity, not a stale or possibly truncated previous value.
  // Missing velocity props preserve compatibility for standalone angle-only use.
  const movingInDirection = Number.isFinite(velocity) && Math.sign(velocity) === Math.sign(delta)
  const allowed = tolerance + (movingInDirection ? Math.abs(velocity) * dt * displayTuning.velocityAllowance : 0)
  if (initialize || !samples.initialized || !Number.isFinite(velocity) || Math.abs(delta) <= allowed){
    samples.initialized = true
    samples.value = value
    samples.acceptedAt = sampleClock
    samples.candidate = null
    samples.confirmations = 0
    return
  }
  const candidateDelta = samples.candidate === null ? Infinity : Math.abs(THREE.MathUtils.radToDeg(angleDelta(
    THREE.MathUtils.degToRad(samples.candidate), THREE.MathUtils.degToRad(value),
  )))
  if (candidateDelta > tolerance){
    samples.candidateSince = sampleClock
    samples.confirmations = 0
  }
  samples.candidate = value
  samples.confirmations += 1
  // Reacquire a genuine new steady pose even when its velocity sample was missed.
  // Only fresh consistent samples can confirm it; animation time cannot do so.
  if (samples.confirmations >= displayTuning.confirmationSamples &&
      sampleClock - samples.candidateSince >= displayTuning.confirmationSpanMs){
    samples.value = value
    samples.acceptedAt = sampleClock
    samples.candidate = null
    samples.confirmations = 0
  }
}
function mapPoseTargets(){
  const config = normalizedCalibration(calibration)
  if (armSamples.initialized && Number.isFinite(calibration.thetaOffsetDeg)){
    targetArm = THREE.MathUtils.degToRad(config.thetaDirection * armSamples.value + config.thetaOffsetDeg)
  }
  if (pendulumSamples.initialized && Number.isFinite(calibration.alphaOffsetDeg)){
    const mapping = config.alphaTwoPointEnabled && !alphaReferencesValid(calibration) ? {...config,alphaTwoPointEnabled:false} : config
    targetPendulum = THREE.MathUtils.degToRad(displayAlphaDeg(pendulumSamples.value,mapping))
  }
}

function metal(color, roughness=.3, metalness=.85){
  return new THREE.MeshStandardMaterial({ color, roughness, metalness })
}
function addBox(parent, size, pos, material){
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(...size), material)
  mesh.position.set(...pos)
  mesh.castShadow = true
  mesh.receiveShadow = true
  parent.add(mesh)
  return mesh
}
function addCylinder(parent, radiusTop, radiusBottom, height, pos, material, radial=48){
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radiusTop, radiusBottom, height, radial), material)
  mesh.position.set(...pos)
  mesh.castShadow = true
  mesh.receiveShadow = true
  parent.add(mesh)
  return mesh
}
function initScene(){
  scene = new THREE.Scene()
  // Match the surrounding neutral surface so the mechanism stands on an open stage.
  // 透明背景让模型跨过标题底线，顶部文字和操作按钮由页面层显示。

  // Preserve the original 3/4 position/target; leave headroom for the upright rod.
  camera = new THREE.PerspectiveCamera(44, 1, .1, 100)
  camera.position.set(8.0, 6.2, 9.8)
  camera.lookAt(.45, 2.35, 0)

  renderer = new THREE.WebGLRenderer({ antialias:true, alpha:true, powerPreference:'high-performance' })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.setClearColor(0x000000, 0)
  renderer.shadowMap.enabled = true
  renderer.shadowMap.type = THREE.PCFSoftShadowMap
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.0
  host.value.prepend(renderer.domElement)

  // Broad reflections make metal readable on its shaded faces. Generate once
  // locally, without an HDR download or a visible room behind the mechanism.
  const environment = new RoomEnvironment()
  const pmrem = new THREE.PMREMGenerator(renderer)
  environmentTarget = pmrem.fromScene(environment, .03)
  scene.environment = environmentTarget.texture
  scene.environmentIntensity = .85
  environment.dispose()
  pmrem.dispose()

  controls = new OrbitControls(camera, renderer.domElement)
  controls.target.set(.45, 2.35, 0)
  controls.enableDamping = true
  controls.dampingFactor = .08
  controls.enableRotate = true
  controls.enableZoom = true
  controls.enablePan = true
  controls.mouseButtons = { LEFT:THREE.MOUSE.ROTATE, MIDDLE:THREE.MOUSE.DOLLY, RIGHT:THREE.MOUSE.PAN }
  controls.minDistance = 2.5
  controls.maxDistance = 18
  controls.maxPolarAngle = Math.PI / 2 - .06
  controls.cursor.copy(controls.target)
  controls.maxTargetRadius = 6
  controls.update()
  controls.saveState()

  scene.add(new THREE.HemisphereLight(0xf5f3e8, 0x5c6054, 1.6))
  const key = new THREE.DirectionalLight(0xfffaf3, 2.8)
  key.position.set(4.3, 7.5, 4.2)
  key.castShadow = true
  key.shadow.mapSize.set(2048, 2048)
  key.shadow.normalBias = .025
  key.shadow.camera.left = -6
  key.shadow.camera.right = 6
  key.shadow.camera.top = 6
  key.shadow.camera.bottom = -6
  scene.add(key)

  const rim = new THREE.PointLight(0xffffff, 10, 10, 2)
  rim.position.set(-3.6, 3.0, -3.6)
  scene.add(rim)
  const fill = new THREE.PointLight(0xf2eee2, 6, 9, 2)
  fill.position.set(3.6, 2.0, 2.8)
  scene.add(fill)

  // Catch contact shadows without lighting a huge opaque plane into a gray veil.
  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(200, 200),
    new THREE.ShadowMaterial({ color:0x080a06, opacity:.35, depthWrite:false })
  )
  floor.rotation.x = -Math.PI / 2
  floor.name = 'displayFloor'
  // The STEP part retains the former 2.21 display reach and stays above this floor.
  floor.position.y = displayFloorY
  floor.receiveShadow = true
  scene.add(floor)

  const grid = new THREE.GridHelper(24, 32, 0x8d9480, 0xa7ad98)
  grid.name = 'displayGrid'
  grid.position.y = displayFloorY + .006
  grid.material.transparent = true
  grid.material.opacity = .10
  grid.material.depthWrite = false
  grid.material.onBeforeCompile = shader => {
    // Interpolate coordinates, not endpoint distances: each grid line spans the
    // whole floor and would disappear if both endpoint fade values were zero.
    shader.vertexShader = 'varying vec2 vGridPosition;\n' + shader.vertexShader
    shader.vertexShader = shader.vertexShader.replace('#include <begin_vertex>',
      '#include <begin_vertex>\nvGridPosition = position.xz;')
    shader.fragmentShader = 'varying vec2 vGridPosition;\n' + shader.fragmentShader
    shader.fragmentShader = shader.fragmentShader.replace('#include <color_fragment>',
      '#include <color_fragment>\ndiffuseColor.a *= 1.0 - smoothstep(1.8, 5.5, length(vGridPosition));')
  }
  scene.add(grid)

  const baseSteel = metal(0xb8bbbf,.34,.70)
  const darkSteel = metal(0x36383b,.38,.72)
  const silver = metal(0xd9dbde,.24,.85)
  const accent = new THREE.MeshStandardMaterial({
    color:0xcba365, emissive:0x382e1c, emissiveIntensity:.12,
    metalness:.68, roughness:.18,
  })

  // Fixed frame: base -> armPivot (local Y / world vertical) -> pendulumPivot (local X).
  base = new THREE.Group()
  base.name = 'base'
  scene.add(base)
  base.add(createBenchSupport())

  // Display chassis carries the original instrument without changing its axes,
  // hinge height, arm length, calibration, or incoming pendulum telemetry.
  const chassis = new THREE.Group()
  chassis.name = 'carChassis'
  base.add(chassis)
  const chassisMaterial = metal(0x747b6b, .48, .45)
  const tireMaterial = new THREE.MeshStandardMaterial({ color:0x191c17, roughness:.92 })
  const wheelMetal = metal(0xc0c5b8, .38, .65)
  addBox(chassis, [2.38,.15,3.2], [0,-.48,0], chassisMaterial)
  addBox(chassis, [2.16,.12,3.04], [0,-.16,0], darkSteel)
  addBox(chassis, [1.82,.15,2.6], [0,-.015,0], baseSteel)
  for (const x of [-.86,.86]) for (const z of [-1.1,1.1]) {
    addCylinder(chassis,.04,.04,.28,[x,-.31,z],silver,16)
  }
  for (const side of [-1,1]) for (const z of [-1.07,1.07]) {
    const steeringPivot = new THREE.Group()
    steeringPivot.position.set(side * 1.33, -.58, z)
    chassis.add(steeringPivot)
    const wheel = new THREE.Group()
    steeringPivot.add(wheel)
    const tire = new THREE.Mesh(new THREE.CylinderGeometry(.42,.42,.31,48), tireMaterial)
    tire.rotation.z = Math.PI / 2
    tire.castShadow = true
    wheel.add(tire)
    const rim = new THREE.Mesh(new THREE.CylinderGeometry(.30,.30,.325,36), darkSteel)
    rim.rotation.z = Math.PI / 2
    wheel.add(rim)
    for (const face of [-1,1]) {
      const ring = new THREE.Mesh(new THREE.TorusGeometry(.285,.017,8,48), accent)
      ring.rotation.y = Math.PI / 2
      ring.position.x = face * .168
      wheel.add(ring)
      const hub = new THREE.Mesh(new THREE.CylinderGeometry(.075,.075,.02,20), wheelMetal)
      hub.rotation.z = Math.PI / 2
      hub.position.x = face * .175
      wheel.add(hub)
      for (let spokeIndex=0;spokeIndex<8;spokeIndex++) {
        const angle = spokeIndex * Math.PI / 4
        const spoke = addBox(wheel,[.018,.035,.22],[face*.173,Math.sin(angle)*.15,Math.cos(angle)*.15],wheelMetal)
        spoke.rotation.x = -angle
      }
    }
    for (let treadIndex=0;treadIndex<30;treadIndex++) {
      const angle = treadIndex * Math.PI / 15
      const tread = addBox(wheel,[.28,.026,.055],[0,Math.cos(angle)*.419,Math.sin(angle)*.419],tireMaterial)
      tread.rotation.x = angle
    }
    carWheels.push({ wheel, steeringPivot, side, front:z>0 })
  }
  addBox(chassis,[.8,.14,.10],[0,-.21,1.57],chassisMaterial)
  for (const x of [-.24,.24]) {
    const sensor = addCylinder(chassis,.12,.12,.12,[x,-.21,1.63],silver,24)
    sensor.rotation.x = Math.PI / 2
    const sensorFace = addCylinder(chassis,.075,.075,.13,[x,-.21,1.65],darkSteel,24)
    sensorFace.rotation.x = Math.PI / 2
  }

  setSceneMode(base, props.sceneMode)

  // 原 STEP 装配拆分为固定部分、旋臂和摆杆，沿真实轴心联动。
  new GLTFLoader().loadAsync(pendulumAssetUrl).then(gltf => {
    const model = gltf.scene
    if (destroyed) { disposePendulumModel(model); return }
    try {
      const assembly = mountAssembly(model)
      base.add(assembly.root)
      armPivot = assembly.armPivot
      pendulumPivot = assembly.pendulumPivot
      pendulumLoad = createPendulumLoad(assembly.pendulumModel, pendulumAssetMetadata)
      pendulumLoad.visible = props.loaded
      pendulumPivot.add(pendulumLoad)
    } catch (error) {
      disposePendulumModel(model)
      throw error
    }
  }).catch(error => {
    if (destroyed) return
    modelError.value = '倒立摆装配模型加载失败，请刷新页面'
    console.error('IP570 装配资产加载失败', error)
  })

  resizeObserver = new ResizeObserver(resize)
  resizeObserver.observe(host.value)
  resize()
  currentArm = targetArm
  currentPendulum = targetPendulum
  animate(performance.now())
}

function resetView(){
  if (!controls) return
  // Flush residual orbit/pan damping before reset, so an immediate reset cannot drift.
  const damping = controls.enableDamping
  controls.enableDamping = false
  controls.update()
  controls.reset()
  resize()
  controls.enableDamping = damping
}
function resize(){
  if (!renderer || !host.value) return
  const width = Math.max(10, host.value.clientWidth)
  const height = Math.max(10, host.value.clientHeight)
  const stageWidth = Math.max(10, host.value.parentElement.clientWidth)
  const stageHeight = Math.max(10, host.value.parentElement.clientHeight)
  const leftOverflow = Math.max(0, -host.value.offsetLeft)
  const topOverflow = Math.max(0, height - stageHeight)
  renderer.setSize(width, height, false)
  // Keep display magnification independent of the assembly's mechanical dimensions.
  const displayStyle = getComputedStyle(host.value)
  const displayZoom = Number.parseFloat(displayStyle.getPropertyValue('--model-view-zoom'))
  const displayLift = Number.parseFloat(displayStyle.getPropertyValue('--model-view-lift'))
  camera.zoom = Number.isFinite(displayZoom) && displayZoom > 0 ? displayZoom : 1
  // 扩展画布保留跨列绘制，显示倍率独立于机械尺寸和旋转中心。
  camera.setViewOffset(stageWidth, stageHeight, -leftOverflow,
    -topOverflow + (Number.isFinite(displayLift) ? displayLift : 0), width, height)
  camera.updateProjectionMatrix()
}
function angleDelta(a, b){
  return THREE.MathUtils.euclideanModulo(b - a + Math.PI, Math.PI * 2) - Math.PI
}
function angleLerp(a, b, t){
  // Keep a continuous local angle across +/-180, including multi-turn telemetry.
  return a + angleDelta(a, b) * t
}
function pendulumSmoothing(smoothing){
  if (smoothing === 0 || props.mode !== 'BALANCE') return smoothing
  const errorDeg = Math.abs(THREE.MathUtils.radToDeg(angleDelta(currentPendulum, targetPendulum)))
  const motion = THREE.MathUtils.smoothstep(errorDeg, displayTuning.microMotionDeg, displayTuning.fullSpeedDeg)
  // Filter tiny rapid fluctuations without a dead zone, zero clamp, or angle gain.
  // Genuine motion uses the original fast response and steady angles remain exact.
  return THREE.MathUtils.lerp(Math.max(smoothing, displayTuning.balanceSmoothing), smoothing, motion)
}
function animate(now){
  raf = requestAnimationFrame(animate)
  if(!renderBudget.shouldRender(now,props.drawingActive)){
    modelPaused.value=renderBudget.paused(props.drawingActive)
    return
  }
  modelPaused.value=false
  const previousFrameTime = lastFrameTime ?? now
  const dt = Math.max(0, (now - previousFrameTime) / 1000)
  lastFrameTime = now
  const smoothing = normalizedCalibration(calibration).smoothing
  const blend = smoothing === 0 ? 1 : -Math.expm1(-dt / smoothing)
  const alphaSmoothing = pendulumSmoothing(smoothing)
  const alphaBlend = alphaSmoothing === 0 ? 1 : -Math.expm1(-dt / alphaSmoothing)
  currentArm = angleLerp(currentArm, targetArm, blend)
  currentPendulum = angleLerp(currentPendulum, targetPendulum, alphaBlend)
  if (armPivot) armPivot.rotation.y = currentArm
  if (pendulumPivot) pendulumPivot.rotation.x = currentPendulum
  // 轮计数、舵机比较值尚无机械标定；底盘保持示意，不从指令推算运动。

  controls.update()
  // Orbit remains above the floor even after a downward right-button pan.
  const lift = Math.max(0, displayFloorY + .2 - controls.target.y)
  controls.target.y += lift
  camera.position.y += lift
  const renderStart=performance.now()
  renderer.render(scene, camera)
  renderBudget.record(now,performance.now()-renderStart,props.drawingActive,now-previousFrameTime)
}

onMounted(initScene)
onBeforeUnmount(() => {
  destroyed = true
  cancelAnimationFrame(raf)
  carWheels.length = 0
  resizeObserver?.disconnect()
  controls?.dispose()
  environmentTarget?.dispose()
  if (renderer){ renderer.dispose(); renderer.domElement.remove() }
  scene?.traverse(object => {
    object.geometry?.dispose?.()
    if (object.material){
      const materials = Array.isArray(object.material) ? object.material : [object.material]
      materials.forEach(material => material.dispose?.())
    }
  })
})
</script>

<style scoped>
.twin3d-tools{position:absolute;z-index:4;left:50%;bottom:9px;transform:translateX(-50%);display:flex;gap:8px}
.twin3d-model-error{position:absolute;z-index:4;top:calc(var(--topbar-height) + 18px);left:50%;transform:translateX(-50%);margin:0;padding:8px 12px;color:#e4b97c;background:#30352b;border:1px solid #756141;border-radius:5px;font-size:12px}
.twin3d-display-note{position:absolute;z-index:4;top:48px;right:14px;max-width:320px;margin:0;color:#c7d0b7;font-size:11px;line-height:1.6;pointer-events:none}
.twin3d-tools button,.twin3d-calibration button{border:1px solid #636c52;border-radius:5px;background:#30352b;color:#c7d0b7;padding:6px 11px;font-size:11px;cursor:pointer}
.twin3d-tools button:hover,.twin3d-calibration button:hover{border-color:#b9a171;color:#f1f2e9}
.twin3d-calibration{position:absolute;z-index:5;right:14px;top:calc(var(--topbar-height) + 20px);width:min(276px,calc(100% - 28px));max-height:calc(100% - var(--topbar-height) - 40px);overflow:auto;box-sizing:border-box;padding:15px;border:1px solid #737c60;border-radius:8px;background:#30362a;color:#c4cdb5;box-shadow:0 4px 25px #0005;font-size:11px}
.calibration-heading{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}
.calibration-heading b{color:#e7eddb}.calibration-heading span{font-size:9px;color:#b0bc9c}
.twin3d-calibration label{display:grid;grid-template-columns:1fr 90px;gap:10px;align-items:center;margin:8px 0}
.twin3d-calibration input,.twin3d-calibration select{min-width:0;width:100%;box-sizing:border-box;border:1px solid #6d7b55;border-radius:4px;background:#252e1c;color:#e4edd6;padding:5px;font:inherit;color-scheme:dark}
.twin3d-calibration .calibration-toggle{border-top:1px solid #566544;padding-top:10px;margin-top:12px}
.twin3d-calibration input[type=checkbox]{width:16px;height:16px;justify-self:end;accent-color:#dbaa64}
.twin3d-calibration p{color:#b2c19c;font-size:10px;line-height:1.6;margin:9px 0}
.twin3d-tools button:focus-visible,.twin3d-calibration :is(input,select,button):focus-visible{outline:2px solid #dbaa64;outline-offset:2px}
</style>
