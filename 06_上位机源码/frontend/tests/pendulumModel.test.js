import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import crypto from 'node:crypto'
import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'

let mountPendulumModel, PENDULUM_DISPLAY
try { ({ mountPendulumModel, PENDULUM_DISPLAY } = await import('../src/pendulumModel.js')) }
catch (error) { if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error }

let createPendulumLoad
try { ({ createPendulumLoad } = await import('../src/pendulumLoad.js')) }
catch (error) { if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error }

async function model() {
  assert.equal(typeof mountPendulumModel, 'function', '需要 STEP 摆杆显示适配')
  const bytes = await fs.readFile(new URL('../src/assets/models/l-pendulum.glb', import.meta.url))
  const gltf = await new GLTFLoader().parseAsync(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset+bytes.byteLength), '')
  return gltf.scene
}

test('网页网格保留 STEP 实物尺寸、36 个面以及源文件追溯', async () => {
  const scene = await model()
  const metadata = JSON.parse(await fs.readFile(new URL('../src/assets/models/l-pendulum.meta.json', import.meta.url),'utf8'))
  const source = await fs.readFile(new URL('../model-sources/l-arm.step',import.meta.url))
  assert.equal(crypto.createHash('sha256').update(source).digest('hex'),metadata.sourceSha256)
  assert.equal(metadata.sourceFaces,36)
  const bounds = new THREE.Box3().setFromObject(scene), size=bounds.getSize(new THREE.Vector3())
  assert.ok(Math.abs(size.x-.04)<1e-6, '原 40 mm 折边尺寸')
  assert.ok(Math.abs(size.y-.152)<1e-6, '原 152 mm 总长')
  assert.ok(Math.abs(size.z-.036)<1e-6, '原 36 mm 宽度')
  assert.ok(Math.abs(bounds.max.y-.134)<1e-6, '安装轴到远端 134 mm')
  let triangles=0
  scene.traverse(object=>{if(object.isMesh) triangles+=object.geometry.index.count/3})
  assert.equal(triangles,metadata.triangles)
})

test('等比例显示且轴端向上、向下时均完整处于地面上方', async () => {
  const part=await model()
  mountPendulumModel(part, new THREE.MeshStandardMaterial())
  assert.equal(part.scale.x,part.scale.y)
  assert.equal(part.scale.y,part.scale.z)
  const arm=new THREE.Group(), pendulum=new THREE.Group()
  arm.position.y=1.70
  pendulum.position.x=2.23
  arm.add(pendulum)
  pendulum.add(part)
  const upright=new THREE.Box3().setFromObject(arm)
  assert.ok(Math.abs(upright.max.y-(1.70+2.21))<1e-5,'保留原场景摆杆显示高度')
  pendulum.rotation.x=Math.PI
  arm.updateMatrixWorld(true)
  const down=new THREE.Box3().setFromObject(arm)
  assert.ok(down.min.y > -1, '下垂模型没有穿过地面')
})

test('摆杆始终跟随旋臂局部轴运动，正负摆角保持相反方向', async () => {
  const part=await model()
  mountPendulumModel(part,new THREE.MeshStandardMaterial())
  const arm=new THREE.Group(), pivot=new THREE.Group()
  pivot.position.set(2.23,1.70,0)
  pivot.add(part)
  arm.add(pivot)
  const point=new THREE.Vector3(0,.134,0)
  pivot.rotation.x=Math.PI/2
  arm.updateMatrixWorld(true)
  const positive=part.localToWorld(point.clone())
  assert.ok(positive.z>2,'正摆角沿局部 +Z')
  pivot.rotation.x=-Math.PI/2
  arm.updateMatrixWorld(true)
  const negative=part.localToWorld(point.clone())
  assert.ok(negative.z<-2,'负摆角沿局部 -Z')
  pivot.rotation.x=Math.PI/2
  arm.rotation.y=Math.PI/2
  arm.updateMatrixWorld(true)
  const turned=part.localToWorld(point.clone())
  assert.ok(Math.abs(turned.x-positive.z)<1e-5,'摆角跟随旋臂转到新的方向')
  assert.ok(Math.abs(turned.z+positive.x)<1e-5,'沿旋臂旋转后的铰链轴摆动')
  assert.equal(PENDULUM_DISPLAY.hingeOffset,part.position.x)
})

test('挂载组件安装在 STEP 自由端孔位，切换可见性不改变摆杆', async () => {
  assert.equal(typeof createPendulumLoad, 'function', '需要挂钩和重物显示模型')
  const part = await model()
  mountPendulumModel(part, new THREE.MeshStandardMaterial())
  const metadata = JSON.parse(await fs.readFile(new URL('../src/assets/models/l-pendulum.meta.json', import.meta.url), 'utf8'))
  const pivot = new THREE.Group()
  pivot.add(part)
  const before = new THREE.Box3().setFromObject(part)
  const load = createPendulumLoad(part, metadata)
  pivot.add(load)
  const hole = part.localToWorld(new THREE.Vector3(...metadata.freeEndHoleCenterMeters))
  assert.ok(load.getWorldPosition(new THREE.Vector3()).distanceTo(hole) < 1e-6, '挂钩紧固件对齐端部安装孔')
  assert.ok(load.getObjectByName('load-hook'), '包含金属挂钩')
  assert.ok(!load.getObjectByName('hanging-weight'), '挂钩下不显示悬挂物')
  for (const visible of [false, true, false, true]) {
    load.visible = visible
    assert.ok(new THREE.Box3().setFromObject(part).equals(before), '切换不改变原摆杆位置和尺寸')
  }
})

test('挂钩固定盘跨过端部大孔并贴紧 STEP 实体，不能悬在孔内', async () => {
  const part = await model()
  mountPendulumModel(part, new THREE.MeshStandardMaterial())
  const metadata = JSON.parse(await fs.readFile(new URL('../src/assets/models/l-pendulum.meta.json', import.meta.url), 'utf8'))
  const pivot = new THREE.Group()
  pivot.add(part)
  const load = createPendulumLoad(part, metadata)
  pivot.add(load)
  pivot.updateMatrixWorld(true)
  const hook = load.getObjectByName('load-hook')
  const ray = new THREE.Raycaster()
  let contacts = 0
  // 14 mm 半径落在原 11 mm 中央孔和 18 mm 外缘之间，必须有实体支撑。
  for (let index = 0; index < 16; index++) {
    const angle = index * Math.PI / 8
    const origin = load.position.clone().add(new THREE.Vector3(
      .014 * part.scale.x * Math.cos(angle), .2, .014 * part.scale.z * Math.sin(angle),
    ))
    ray.set(origin, new THREE.Vector3(0, -1, 0))
    ray.far = .4
    const flange = ray.intersectObject(part, true).find(hit => hit.face.normal.y > .99)
    if (!flange) continue // 避开原件上的小安装孔。
    contacts++
    const clamp = ray.intersectObject(hook, true)[0]
    assert.ok(clamp, '固定盘必须覆盖端部实体，原小垫片完全悬在大孔中')
    const bounds = new THREE.Box3().setFromObject(clamp.object)
    assert.ok(Math.abs(bounds.min.y - flange.point.y) < .001, '上固定盘的底面贴紧原件上表面')
  }
  assert.ok(contacts >= 8, '至少八处环形支撑被检查')
})
