import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import crypto from 'node:crypto'
import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { mountAssembly } from '../src/pendulumAssembly.js'
import { createPendulumLoad } from '../src/pendulumLoad.js'

async function asset() {
  const bytes = await fs.readFile(new URL('../src/assets/models/ip570.glb', import.meta.url))
  return (await new GLTFLoader().parseAsync(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), '')).scene
}

test('电机深色和外侧铜色仅应用于对应零件', async () => {
  const scene = await asset()
  const {root} = mountAssembly(scene)
  const motor = root.getObjectByName('part-17')
  const housing = root.getObjectByName('part-6')
  const sensor = root.getObjectByName('part-25')
  assert.notEqual(motor.material, housing.material, '电机颜色不能改动共用的外壳材质')
  assert.ok(motor.material.color.getHSL({}).l < housing.material.color.getHSL({}).l)
  const colors = sensor.geometry.getAttribute('color')
  assert.ok(colors && sensor.material.vertexColors, '铜色应限于圆筒表面')
  let copper = 0, silver = 0
  for (let i=0; i<colors.count; i++) {
    if (colors.getX(i) > colors.getY(i) * 1.5) copper++
    else silver++
  }
  assert.ok(copper > 0 && silver > 0, '圆筒和端面各保留不同颜色')
})

test('隐藏原装配的夹板与锁紧螺杆，保留电机及运动部件', async () => {
  const scene = await asset()
  const { root, armPivot, pendulumPivot } = mountAssembly(scene)
  for (const name of ['part-0','part-1','part-2']) assert.equal(root.getObjectByName(name).visible, false, name)
  for (const name of ['part-6','part-17','part-18']) assert.equal(root.getObjectByName(name).visible, true, name)
  root.updateMatrixWorld(true)
  assert.ok(Math.abs(new THREE.Box3().setFromObject(root.getObjectByName('part-6')).min.y) < 1e-5, '电机机壳直接落在原底座顶面')
  assert.ok(armPivot.visible && pendulumPivot.visible)
})

test('完整装配保留来源、全部网格及折边朝外的摆臂', async () => {
  const scene = await asset()
  const meta = JSON.parse(await fs.readFile(new URL('../src/assets/models/ip570.meta.json', import.meta.url)))
  const source = await fs.readFile(new URL('../model-sources/ip570.step', import.meta.url))
  assert.equal(crypto.createHash('sha256').update(source).digest('hex'), meta.sourceSha256)
  let meshes = 0; scene.traverse(o => { if (o.isMesh) meshes++ })
  assert.equal(meshes, 28)
  const bounds = new THREE.Box3().setFromObject(scene.getObjectByName('vertical-arm'))
  assert.ok(bounds.max.x > .039 && bounds.min.x > -.003, '折边位于旋臂外侧')
  assert.ok(Math.abs(bounds.max.y - .135) < .002)
  assert.equal(meta.flangePhaseDegrees, 41.1092176599315)
})

test('真实装配围绕两条轴联动，挂钩对齐外侧孔且没有悬挂物', async () => {
  const scene = await asset()
  const meta = JSON.parse(await fs.readFile(new URL('../src/assets/models/ip570.meta.json', import.meta.url)))
  const { root, armPivot, pendulumPivot, pendulumModel } = mountAssembly(scene)
  const load = createPendulumLoad(pendulumModel, meta)
  pendulumPivot.add(load)
  assert.ok(load.getObjectByName('load-hook'))
  assert.ok(!load.getObjectByName('hanging-weight'))
  const hole = pendulumModel.localToWorld(new THREE.Vector3(...meta.freeEndHoleCenterMeters))
  assert.ok(load.getWorldPosition(new THREE.Vector3()).distanceTo(hole) < 1e-6)
  const before = pendulumModel.localToWorld(new THREE.Vector3(0,.1,0))
  pendulumPivot.rotation.x = Math.PI / 2
  root.updateMatrixWorld(true)
  const bent = pendulumModel.localToWorld(new THREE.Vector3(0,.1,0))
  assert.ok(bent.z > before.z + 1)
  armPivot.rotation.y = Math.PI / 2
  root.updateMatrixWorld(true)
  const turned = pendulumModel.localToWorld(new THREE.Vector3(0,.1,0))
  assert.ok(Math.abs(turned.x - bent.z) < 1e-5)
  assert.ok(Math.abs(turned.z + bent.x) < 1e-5)
})

test('挂钩固定盘贴合新装配的端部折边', async () => {
  const scene = await asset()
  const meta = JSON.parse(await fs.readFile(new URL('../src/assets/models/ip570.meta.json', import.meta.url)))
  const { root, pendulumPivot, pendulumModel } = mountAssembly(scene)
  const load = createPendulumLoad(pendulumModel, meta)
  pendulumPivot.add(load)
  root.updateMatrixWorld(true)
  const center = load.getWorldPosition(new THREE.Vector3())
  const ray = new THREE.Raycaster()
  let contacts = 0
  for (let i = 0; i < 16; i++) {
    const a = i * Math.PI / 8
    const origin = center.clone().add(new THREE.Vector3(.014 * pendulumModel.scale.x * Math.cos(a), .2, .014 * pendulumModel.scale.z * Math.sin(a)))
    ray.set(origin, new THREE.Vector3(0,-1,0)); ray.far = .4
    const flange = ray.intersectObject(pendulumModel, true).find(hit => hit.face.normal.y > .99)
    if (!flange) continue
    const clamp = ray.intersectObject(load.getObjectByName('load-hook'), true)[0]
    assert.ok(clamp)
    assert.ok(Math.abs(new THREE.Box3().setFromObject(clamp.object).min.y - flange.point.y) < .001, `clamp=${new THREE.Box3().setFromObject(clamp.object).min.y}, flange=${flange.point.y}, name=${clamp.object.name}`)
    contacts++
  }
  assert.ok(contacts >= 8, '固定盘应跨过大孔并接触折边')
})
