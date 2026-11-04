import test from 'node:test'
import assert from 'node:assert/strict'
import {appendFd2Samples} from '../src/fd2Presentation.js'
import {createDs13SampleCache} from '../src/ds13SampleCache.js'

test('DS13 live cache preserves 120-second retention, ordering, deduplication and reset',()=>{
 const cache=createDs13SampleCache();let expected=[]
 const a=(id,ms)=>({sample_id:id,received_at:ms/1000})
 const steps=[
  {now:120000,rows:[a('a',1),a('b',100000),a('b',100001),a('future',120001)]},
  {now:120010,rows:[a('c',120005),a('a',120006)]},
  {now:120020,rows:[a('out-of-order',110000),a('d',120010)]},
  {now:120030,rows:[a('d',120010),a('e',120020)]},
  {now:119000,rows:[a('rollback',118900)]},
  {now:125000,rows:[a('new',124000)],reset:true},
 ]
 for(const {now,rows,reset=false} of steps){
  expected=appendFd2Samples(expected,rows,now,reset)
  assert.deepEqual(cache.append(rows,now,reset),expected)
 }
 assert.deepEqual(cache.prune(250000),appendFd2Samples(expected,[],250000))
})

test('a 120-second cache adds new samples without rescanning the history',()=>{
 let reads=0;const now=1_000_000
 const rows=Array.from({length:24000},(_,i)=>({sample_id:`s${i}`,get received_at(){reads++;return (now-119_995+i*5)/1000}}))
 const cache=createDs13SampleCache();const before=cache.append(rows,now);reads=0
 const after=cache.append([{sample_id:'next',received_at:(now+5)/1000}],now+5)
 assert.equal(before.length,24000);assert.equal(after.length,24001)
 assert.ok(reads<100,`old timestamps read ${reads} times`)
 assert.equal(cache.prune(now+5),after,'an unchanged cache keeps its snapshot')
})

test('incremental cache matches ordinary retention under mixed packets and clock changes',()=>{
 const cache=createDs13SampleCache();let expected=[],now=1_000_000,seed=0x12345678
 const rand=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/2**32}
 for(let n=0;n<1200;n++){
  now+=rand()<.025?-Math.floor(rand()*2000):Math.floor(rand()*200)
  const rows=Array.from({length:Math.floor(rand()*5)},()=>({sample_id:`id${Math.floor(rand()*300)}`,received_at:(now-Math.floor(rand()*150000)+500)/1000}))
  if(rand()<.03)rows.push(null)
  const reset=rand()<.01
  expected=appendFd2Samples(expected,rows,now,reset)
  assert.deepEqual(cache.append(rows,now,reset),expected,`batch ${n}`)
 }
 expected=appendFd2Samples(expected,[],NaN);assert.deepEqual(cache.append([],NaN),expected)
 expected=appendFd2Samples(expected,[],now);assert.deepEqual(cache.append([],now),expected)
})
