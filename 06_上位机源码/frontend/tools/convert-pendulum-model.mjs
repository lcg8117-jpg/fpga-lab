// 本文件只用于离线资产转换，不参与页面运行。
import fs from 'node:fs/promises'
import crypto from 'node:crypto'
import { createRequire } from 'node:module'
import * as THREE from 'three'
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js'

const require = createRequire(import.meta.url)
const converter = require(process.env.OCCT_IMPORT_JS || 'occt-import-js')
const sourceUrl = new URL('../model-sources/l-arm.step',import.meta.url)
const assetDirectory = new URL('../src/assets/models/',import.meta.url)

// GLTFExporter 的浏览器 FileReader 在本地 Node 中只用于读取生成的 Blob。
globalThis.FileReader = class {
  readAsArrayBuffer(blob) {
    blob.arrayBuffer().then(buffer=>{this.result=buffer;this.onloadend?.()})
  }
}

const source = await fs.readFile(sourceUrl)
const occt = await converter()
const result = occt.ReadStepFile(source, {
  linearUnit:'millimeter',linearDeflectionType:'absolute_value',linearDeflection:.02,angularDeflection:.15,
})
if (!result.success || result.meshes.length !== 1) throw new Error('当前资产需要单个 L 形实体')

// STEP 安装轴中心：直径 7 mm 孔，板厚中面，轴向为 CAD Y。
// 转换为显示坐标：(CAD x,y,z) -> (-y,-z,x)，让自由端沿本地 +Y。
const cadPivot = new THREE.Vector3(0,-2,67.5)
const basis = new THREE.Matrix4().makeBasis(new THREE.Vector3(0,0,1),new THREE.Vector3(-1,0,0),new THREE.Vector3(0,-1,0))
const group = new THREE.Group()
group.name = 'L-shaped-pendulum'
const sourceMesh = result.meshes[0]
const geometry = new THREE.BufferGeometry()
geometry.setAttribute('position',new THREE.Float32BufferAttribute(sourceMesh.attributes.position.array,3))
geometry.setAttribute('normal',new THREE.Float32BufferAttribute(sourceMesh.attributes.normal.array,3))
geometry.setIndex(sourceMesh.index.array)
geometry.translate(-cadPivot.x,-cadPivot.y,-cadPivot.z)
geometry.applyMatrix4(basis)
geometry.scale(.001,.001,.001) // GLB 使用米，保留实物比例。
geometry.computeBoundingBox()
geometry.computeBoundingSphere()
const material = new THREE.MeshStandardMaterial({name:'CAD silver',color:0xe5e7eb,metalness:.85,roughness:.22})
const mesh = new THREE.Mesh(geometry,material)
mesh.name = 'L-shaped-pendulum-body'
group.add(mesh)
group.userData = { source:'L型摆臂(1).STEP',units:'meter',cadPivotMillimeters:cadPivot.toArray() }
const exporter = new GLTFExporter()
const glb = await exporter.parseAsync(group,{binary:true,onlyVisible:true})
const bounds = geometry.boundingBox
const metadata = {
  source:'../../../model-sources/l-arm.step',sourceName:'L型摆臂(1).STEP',
  sourceSha256:crypto.createHash('sha256').update(source).digest('hex'),
  sourceUnit:'millimeter',assetUnit:'meter',converter:'occt-import-js 0.0.23',
  sourceFaces:sourceMesh.brep_faces.length,vertices:geometry.attributes.position.count,
  triangles:geometry.index.count/3,cadPivotMillimeters:cadPivot.toArray(),
  cadToDisplayBasis:basis.toArray(),boundsMeters:{min:bounds.min.toArray(),max:bounds.max.toArray()},
  freeEndHoleCenterMeters:[.021,.133,0],
  triangulation:{linearDeflectionMillimeters:.02,angularDeflection:.15},
}
await fs.mkdir(assetDirectory,{recursive:true})
await fs.writeFile(new URL('l-pendulum.glb',assetDirectory),new Uint8Array(glb))
await fs.writeFile(new URL('l-pendulum.meta.json',assetDirectory),JSON.stringify(metadata,null,2)+'\n')
console.log(JSON.stringify({faces:metadata.sourceFaces,triangles:metadata.triangles,bytes:glb.byteLength,bounds:metadata.boundsMeters}))
