import {appendFd2Samples} from './fd2Presentation.js'

// Keep the same 120-second history as the normal FD2 cache, but update the
// live DS13 stream incrementally instead of rebuilding a 24,000-id Set at 30Hz.
export function createDs13SampleCache() {
  let rows=[],head=0,seen=new Set(),ordered=true,lastNow=-Infinity,snapshot=[]
  function clear(){rows=[];head=0;seen=new Set();ordered=true;lastNow=-Infinity;snapshot=[]}
  function rebuild(result,now){
    rows=result.slice();head=0;seen=new Set();ordered=true
    let previous=-Infinity
    for(const row of rows){
      if(row.sample_id)seen.add(row.sample_id)
      if(row.received_at<previous)ordered=false
      previous=row.received_at
    }
    lastNow=now;snapshot=result;return snapshot
  }
  function append(incoming,now,reset=false){
    if(reset)clear()
    if(!Number.isFinite(now) || (lastNow!==-Infinity && !Number.isFinite(lastNow)) || now<lastNow || !ordered){
      return rebuild(appendFd2Samples(snapshot,incoming,now),now)
    }
    let changed=false
    const cutoff=now-120000
    while(head<rows.length && rows[head].received_at*1000<cutoff){
      const id=rows[head++].sample_id;if(id)seen.delete(id);changed=true
    }
    let last=head<rows.length ? rows[rows.length-1].received_at : -Infinity
    for(const row of incoming){
      if(!row || !Number.isFinite(row.received_at) || row.received_at*1000<cutoff || row.received_at*1000>now)continue
      const id=row.sample_id;if(id && seen.has(id))continue
      if(id)seen.add(id)
      if(row.received_at<last)ordered=false
      last=row.received_at;rows.push(row);changed=true
    }
    if(head>=4096 && head*2>=rows.length){rows=rows.slice(head);head=0}
    lastNow=now
    if(changed)snapshot=rows.slice(head)
    return snapshot
  }
  return {append,prune:now=>append([],now),clear}
}
