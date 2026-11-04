// Keep time-sensitive drawing responsive on machines where WebGL falls back to
// a slow renderer. Normal 3D rendering resumes as soon as drawing ends.
export function createDrawRenderBudget() {
  let slow=false,fastFrames=0,lastRender=-Infinity
  return {
    shouldRender(now,drawing){return !drawing || (!slow && now-lastRender>=100)},
    paused(drawing){return Boolean(drawing && slow)},
    record(now,cost,drawing,frameGap=0){
      lastRender=now
      if(cost>50 || (!drawing && frameGap>100)){slow=true;fastFrames=0}
      else if(!drawing && slow){
        if(cost<25 && frameGap<50)fastFrames++
        else fastFrames=0
        if(fastFrames>=3){slow=false;fastFrames=0}
      }
    },
  }
}
