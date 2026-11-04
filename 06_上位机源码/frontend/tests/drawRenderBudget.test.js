import test from 'node:test'
import assert from 'node:assert/strict'
import {createDrawRenderBudget} from '../src/drawRenderBudget.js'

test('fast 3D rendering remains live at a reduced frame rate while drawing',()=>{
 const b=createDrawRenderBudget()
 assert.equal(b.shouldRender(0,false),true);b.record(0,8,false)
 assert.equal(b.shouldRender(30,true),false)
 assert.equal(b.shouldRender(100,true),true);b.record(100,8,true)
 assert.equal(b.paused(true),false)
})

test('slow 3D rendering pauses during drawing and recovers when fast again',()=>{
 const b=createDrawRenderBudget()
 b.record(0,90,false);assert.equal(b.paused(true),true)
 assert.equal(b.shouldRender(200,true),false)
 for(let i=1;i<=3;i++){assert.equal(b.shouldRender(i*20,false),true);b.record(i*20,5,false)}
 assert.equal(b.paused(true),false);assert.equal(b.shouldRender(200,true),true)
})
