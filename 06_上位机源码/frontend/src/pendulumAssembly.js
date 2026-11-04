import * as THREE from 'three'

// 模型以米为单位；统一缩放只改变显示尺寸，不参与遥测或控制。
export const ASSEMBLY_SCALE = 2.21 / .135

// The source CAD shares one silver material across all parts. Keep the housing
// silver while distinguishing the motor and the orange cylinder in the photo.
function applyDisplayColors(fixed, arm) {
  for (const name of ['part-17', 'part-18']) {
    const motor = fixed.getObjectByName(name)
    if (!motor?.isMesh) continue
    motor.material = motor.material.clone()
    motor.material.color.set(0x292b2d)
    motor.material.roughness = .45
  }
  const sensor = arm.getObjectByName('part-25')
  if (!sensor?.isMesh) return
  sensor.geometry = sensor.geometry.clone()
  sensor.material = sensor.material.clone()
  const silver = sensor.material.color.clone()
  const copper = new THREE.Color(0xb44d16)
  const vertices = sensor.geometry.getAttribute('position')
  const colors = new Float32Array(vertices.count * 3)
  for (let i = 0; i < vertices.count; i++) {
    const x = vertices.getX(i)
    const radius = Math.hypot(vertices.getY(i) - .023, vertices.getZ(i))
    const color = x >= .114 && x <= .132 && radius >= .0165 && radius <= .020 ? copper : silver
    colors[i * 3] = color.r; colors[i * 3 + 1] = color.g; colors[i * 3 + 2] = color.b
  }
  sensor.geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3))
  sensor.material.color.set(0xffffff)
  sensor.material.vertexColors = true
}

export function mountAssembly(scene) {
  const fixed = scene.getObjectByName('fixed-parts')
  const arm = scene.getObjectByName('arm-parts')
  const pendulumModel = scene.getObjectByName('pendulum-parts')
  if (!fixed || !arm || !pendulumModel) throw new Error('装配体缺少运动部件')
  applyDisplayColors(fixed, arm)
  const root = new THREE.Group()
  root.name = 'ip570-assembly'
  // The retained motor housing begins 55 mm above the original clamp origin.
  // Lower the complete assembly onto the existing support; pivot offsets and
  // the physical relationship between both moving axes stay unchanged.
  root.position.y = -.055 * ASSEMBLY_SCALE
  // The STEP's independent clamp screw, pad and C bracket belong to the
  // photographed desk mount; the showcase uses its own support instead.
  for (const name of ['part-0', 'part-1', 'part-2']) {
    const clampPart = fixed.getObjectByName(name)
    if (clampPart) clampPart.visible = false
  }
  fixed.scale.setScalar(ASSEMBLY_SCALE)
  root.add(fixed)
  const armPivot = new THREE.Group()
  armPivot.name = 'armPivot'
  armPivot.position.y = .1928 * ASSEMBLY_SCALE
  root.add(armPivot)
  arm.scale.setScalar(ASSEMBLY_SCALE)
  armPivot.add(arm)
  const pendulumPivot = new THREE.Group()
  pendulumPivot.name = 'pendulumPivot'
  pendulumPivot.position.set(.1526 * ASSEMBLY_SCALE, .023 * ASSEMBLY_SCALE, 0)
  armPivot.add(pendulumPivot)
  pendulumModel.scale.setScalar(ASSEMBLY_SCALE)
  pendulumPivot.add(pendulumModel)
  root.traverse(object => {
    if (object.isMesh) { object.castShadow = true; object.receiveShadow = true }
  })
  return { root, armPivot, pendulumPivot, pendulumModel }
}
