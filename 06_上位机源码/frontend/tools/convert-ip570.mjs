import fs from 'node:fs/promises'
import crypto from 'node:crypto'
import { createRequire } from 'node:module'
import * as THREE from 'three'
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js'
const require = createRequire(import.meta.url)
const load = require(process.env.OCCT_IMPORT_JS || 'occt-import-js')
globalThis.FileReader = class { readAsArrayBuffer(blob) { blob.arrayBuffer().then(b=>{this.result=b;this.onloadend?.()}) } }
const source = await fs.readFile(new URL('../model-sources/ip570.step', import.meta.url))
const occt = await load()
const result = occt.ReadStepFile(source,{linearUnit:'millimeter',linearDeflectionType:'absolute_value',linearDeflection:.05,angularDeflection:.2})
if (!result.success || result.meshes.length !== 28) throw new Error('IP570 STEP 结构不符合预期')
const base = new THREE.Vector3(-4.860826429492,-7.431691499701,0)
const motor = new THREE.Vector3(base.x,base.y,192.8)
const hinge = new THREE.Vector3(base.x,145.1683085003,215.8)
const root = new THREE.Group()
const fixed = new THREE.Group(); fixed.name='fixed-parts'; root.add(fixed)
const arm = new THREE.Group(); arm.name='arm-parts'; root.add(arm)
const pendulum = new THREE.Group(); pendulum.name='pendulum-parts'; root.add(pendulum)
const material = new THREE.MeshStandardMaterial({name:'CAD silver',color:0xd2d5d7,metalness:.8,roughness:.3})
const basis = new THREE.Matrix4().makeBasis(new THREE.Vector3(0,0,1),new THREE.Vector3(1,0,0),new THREE.Vector3(0,1,0))
for (const [i,m] of result.meshes.entries()) {
  const parent = i===26 || i===27 ? pendulum : i===22 || i===23 || i===24 || i===25 ? arm : fixed
  const origin = parent===pendulum ? hinge : parent===arm ? motor : base
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position',new THREE.Float32BufferAttribute(m.attributes.position.array,3))
  geometry.setAttribute('normal',new THREE.Float32BufferAttribute(m.attributes.normal.array,3))
  geometry.setIndex(m.index.array)
  if(i===26) {
    // 前一交付的最终装配：竖直臂绕轴心竖直线旋转 180 度。
    geometry.translate(-hinge.x,-hinge.y,-hinge.z)
    geometry.applyMatrix4(new THREE.Matrix4().makeRotationZ(Math.PI))
    geometry.translate(hinge.x,hinge.y,hinge.z)
  }
  if(i===27) {
    // 四孔对齐：法兰只绕自身 Y 轴旋转，轴心和安装面保持不变。
    geometry.translate(-hinge.x,-hinge.y,-hinge.z)
    geometry.applyMatrix4(new THREE.Matrix4().makeRotationY(-THREE.MathUtils.degToRad(41.1092176599315)))
    geometry.translate(hinge.x,hinge.y,hinge.z)
  }
  geometry.translate(-origin.x,-origin.y,-origin.z)
  geometry.applyMatrix4(basis); geometry.scale(.001,.001,.001)
  const mesh = new THREE.Mesh(geometry,material)
  mesh.name=i===26?'vertical-arm':`part-${i}`
  parent.add(mesh)
}
const data = await new GLTFExporter().parseAsync(root,{binary:true})
const metadata={source:'../../../model-sources/ip570.step', sourceSha256:crypto.createHash('sha256').update(source).digest('hex'),sourceUnit:'millimeter',assetUnit:'meter',meshCount:28, flangePhaseDegrees:41.1092176599315, motorPivotMillimeters:motor.toArray(),hingePivotMillimeters:hinge.toArray(),freeEndHoleCenterMeters:[.0205,.134,0],boundsMeters:{max:[.0395,.135,.018]}}
await fs.writeFile(new URL('../src/assets/models/ip570.glb',import.meta.url),new Uint8Array(data))
await fs.writeFile(new URL('../src/assets/models/ip570.meta.json',import.meta.url),JSON.stringify(metadata,null,2)+'\n')
console.log('converted',data.byteLength,metadata.sourceSha256)
