import * as THREE from 'three'

export function createBenchSupport() {
  const support = new THREE.Group()
  support.name = 'displaySupport'
  const material = new THREE.MeshStandardMaterial({color:0x656c5e,roughness:.65,metalness:.35})
  // 历史固定台架原尺寸；不改变上部轴系、STEP 或挂点坐标。
  for (const [size, position] of [
    [[2.16,.12,1.58],[0,-.94,0]],
    [[1.38,.80,1.06],[0,-.48,0]],
    [[1.92,.08,1.38],[0,-.04,0]],
  ]) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(...size), material)
    mesh.position.set(...position); mesh.castShadow = true; mesh.receiveShadow = true
    support.add(mesh)
  }
  return support
}

export function setSceneMode(base, mode) {
  const chassis = base?.getObjectByName('carChassis'), support = base?.getObjectByName('displaySupport')
  if (chassis) chassis.visible = mode === 'car'
  if (support) support.visible = mode !== 'car'
}
