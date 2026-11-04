import * as THREE from 'three'

// 仅适配显示尺寸和轴端安装位置；不参与遥测换算或控制。
export const PENDULUM_DISPLAY = Object.freeze({ reach:2.21, hingeOffset:.18 })

export function mountPendulumModel(model, material) {
  const bounds = new THREE.Box3().setFromObject(model)
  if (!Number.isFinite(bounds.max.y) || bounds.max.y <= 0) throw new Error('摆杆资产尺寸无效')
  model.name = 'pendulum-step-model'
  model.scale.setScalar(PENDULUM_DISPLAY.reach / bounds.max.y)
  // 板厚中面放在现有编码器端盖之外，安装孔仍与局部 X 轴同轴。
  model.position.set(PENDULUM_DISPLAY.hingeOffset,0,0)
  const originalMaterials = new Set()
  model.traverse(object=>{
    if (!object.isMesh) return
    for(const old of Array.isArray(object.material)?object.material:[object.material]) originalMaterials.add(old)
    object.material = material
    object.castShadow = true
    object.receiveShadow = true
  })
  for(const old of originalMaterials) if(old !== material) old.dispose()
  return model
}

export function disposePendulumModel(model) {
  const geometries = new Set(), materials = new Set()
  model.traverse(object=>{
    if(object.geometry) geometries.add(object.geometry)
    if(object.material) for(const material of Array.isArray(object.material)?object.material:[object.material]) materials.add(material)
  })
  for(const geometry of geometries) geometry.dispose()
  for(const material of materials) material.dispose()
}
