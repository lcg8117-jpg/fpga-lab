import test from 'node:test'
import assert from 'node:assert/strict'
import {appendMappedPage, mappedSegments} from '../src/g3Mapping.js'

test('mapped points retain breaks and reject invalid coordinates', () => {
  const page={stream:'s',cursor:3,overrun:false,samples:[
    {cursor:1,point:{x_display:1,y_display:2,break_before:true}},
    {cursor:2,point:{x_display:2,y_display:3,break_before:false}},
    {cursor:3,point:{x_display:4,y_display:5,break_before:true}},
  ]}
  const state=appendMappedPage(null,page)
  assert.deepEqual(mappedSegments(state.points).map(x=>x.length),[2,1])
  assert.throws(()=>appendMappedPage(state,{stream:'s',cursor:4,overrun:false,samples:[{cursor:4,point:{x_display:NaN,y_display:1}}]}))
})

test('stream changes and overruns reset old mapping', () => {
  const old={stream:'old',cursor:3,points:[{x_display:1,y_display:1,break_before:false}]}
  const next=appendMappedPage(old,{stream:'new',cursor:5,overrun:true,samples:[{cursor:5,point:{x_display:3,y_display:4,break_before:false}}]})
  assert.equal(next.points.length,1)
  assert.equal(next.points[0].break_before,true)
})
