import * as THREE from 'three'

// 挂载示意只用于 3D 展示，孔位来自现有 STEP；不模拟负载动力学。
export function createPendulumLoad(model, metadata) {
  const load = new THREE.Group()
  load.name = 'pendulum-load'
  load.position.copy(new THREE.Vector3(...metadata.freeEndHoleCenterMeters)
    .multiply(model.scale).applyQuaternion(model.quaternion).add(model.position))

  const steel = new THREE.MeshStandardMaterial({ color:0xdde0e2, metalness:.88, roughness:.23 })
  function mesh(parent, geometry, material, position = [0, 0, 0]) {
    const object = new THREE.Mesh(geometry, material)
    object.position.set(...position)
    object.castShadow = true
    object.receiveShadow = true
    parent.add(object)
    return object
  }
  function tube(parent, points, radius, material) {
    return mesh(parent, new THREE.TubeGeometry(
      new THREE.CatmullRomCurve3(points.map(point => new THREE.Vector3(...point))),
      48, radius, 12, false,
    ), material)
  }

  const hook = new THREE.Group()
  hook.name = 'load-hook'
  load.add(hook)
  // STEP 折边中央孔直径 22 mm；直径 30 mm 的上下固定盘跨过孔边，夹紧 2 mm 板厚。
  // 原小垫片小于孔径，无法接触零件。盘的表面位置按资产比例计算。
  const flangeHalfThickness = (metadata.boundsMeters.max[1] - metadata.freeEndHoleCenterMeters[1]) * model.scale.y
  const clampRadius = .015 * model.scale.x
  const clampThickness = .002 * model.scale.y
  const clampSteel = new THREE.MeshStandardMaterial({ color:0xa5abb0, metalness:.8, roughness:.38 })
  for (const side of [1, -1]) {
    const plate = mesh(hook, new THREE.CylinderGeometry(clampRadius, clampRadius, clampThickness, 64),
      clampSteel, [0, side * (flangeHalfThickness + clampThickness / 2), 0])
    plate.name = side === 1 ? 'load-mount-top' : 'load-mount-bottom'
    mesh(hook, new THREE.CylinderGeometry(.065, .065, .05, 6), steel,
      [0, side * (flangeHalfThickness + clampThickness + .025), 0])
  }
  mesh(hook, new THREE.CylinderGeometry(.035, .035, .32, 20), steel, [0, -.035, 0])
  tube(hook, [
    [0, -.17, 0], [0, -.27, .03], [0, -.38, .11],
    [0, -.50, .08], [0, -.535, -.025], [0, -.49, -.13], [0, -.40, -.17],
  ], .032, steel)

  return load
}
