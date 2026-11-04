"""Unwrapped measured/reference/final coordinates, with gaps kept visible."""
import time
def draw_motion(canvas,state):
    full=state['history'];compact=state.get('compact_history',[])
    if state['control_fresh'] and full:
        samples=[dict(ms=r[6],actual=r[4],reference=r[5],angle=r[1],angle_target=r[2]) for r in full]
        goals=[(r[0],r[1] if len(r)<5 or r[4] else None) for r in state.get('goal_history',[])];source='USB T 200Hz；最终目标取独立D'
    elif compact and state['last_compact']['range_valid'] and state['last_compact']['coherent'] and time.monotonic()-state['last_compact_rx']<=.5:
        samples=[dict(ms=r['board_ms'],actual=r['wide_encoder'],reference=r['wide_target'],angle=r['angle_error']*345/4096,angle_target=r['target_angle_error']*345/4096) for r in compact]
        goals=[(r['board_ms'],r['final_target'] if r['motion_state'] not in (2,3) else None) for r in compact];source='BLE紧凑B；板端200Hz，实际接收节拍见日志'
    else:
        canvas.delete('all');canvas.create_text(12,16,text='等待新鲜有效运动事务；过期、诊断和超范围数据不补画成当前控制样本。',anchor='nw',fill='#555');return
    if len(samples)<2:canvas.delete('all');return
    canvas.delete('all');width=max(300,canvas.winfo_width());height=max(80,canvas.winfo_height())
    end=samples[-1]['ms'];start=samples[0]['ms'];duration=((end-start)&0xffffffff)/1000
    def relative(ms):return ((ms-start)&0xffffffff)/1000
    goals=[(relative(ms),value*360/1040 if value is not None else None) for ms,value in goals if ((ms-start)&0xffffffff)<0x80000000 and relative(ms)<=duration]
    for panel,keys,title,scale in [(0,('angle','angle_target'),'摆杆角度：实际蓝 / 目标红（°）',1),(1,('actual','reference'),'累计旋臂：实际蓝 / 参考红 / 最终绿（°）',360/1040)]:
        left=panel*width/2+90;right=(panel+1)*width/2-12;top=26;bottom=height-50
        band=5*360/1040
        values=[s[key]*scale for s in samples for key in keys]+([edge for _,v in goals if v is not None for edge in (v-band,v+band)] if panel else [])
        low=min(values);high=max(values);span=max(high-low,.02);low-=span*.06;high+=span*.06
        def xy(t,value):return left+(t/duration if duration else 0)*(right-left),bottom-(value-low)/(high-low)*(bottom-top)
        canvas.create_text(left,4,text=title,anchor='nw');canvas.create_rectangle(left,top,right,bottom,outline='#bbb')
        canvas.create_text(left-4,top,text=f'{high:.4g}',anchor='e',font=('Consolas',9));canvas.create_text(left-4,bottom,text=f'{low:.4g}',anchor='e',font=('Consolas',9))
        if panel:
            for index,(t,value) in enumerate(goals):
                if value is None:continue
                until=goals[index+1][0] if index+1<len(goals) else duration
                x1,y1=xy(t,value+band);x2,y2=xy(until,value-band)
                canvas.create_rectangle(x1,y1,x2,y2,fill='#e5f3e8',outline='')
        for key,color in zip(keys,('#1565c0','#c62828')):
            points=[];prior=None
            for s in samples:
                if prior is not None and ((s['ms']-prior)&0xffffffff)!=5:
                    if len(points)>=4:canvas.create_line(*points,fill=color,width=1.3)
                    points=[]
                points.extend(xy(relative(s['ms']),s[key]*scale));prior=s['ms']
            if len(points)>=4:canvas.create_line(*points,fill=color,width=1.3)
        if panel and goals:
            points=[];last=None
            for t,value in goals:
                if value is None:
                    if len(points)>=4:canvas.create_line(*points,fill='#16803a',dash=(5,3),width=1.7)
                    points=[];last=None;continue
                if last is not None:points.extend(xy(t,last))
                points.extend(xy(t,value));last=value
            if len(points)>=4:canvas.create_line(*points,fill='#16803a',dash=(5,3),width=1.7)
            endpoint=goals[-1][1]
            final_text=f"最终={endpoint*1040/360:.0f} 误差={samples[-1]['actual']-endpoint*1040/360:+.3f}计数" if endpoint is not None else '连续/刹车中：终点尚未锁定'
            diagnostic=state.get('latest_diagnostic');age=time.monotonic()-state.get('last_diagnostic_rx',0)
            dwell=f"到位资格={diagnostic['settle_ticks']*5}ms / 500ms" if diagnostic and age<=.5 else '到位资格诊断待更新'
            canvas.create_text(right,top+3,text=f"实测={samples[-1]['actual']:.0f} 参考={samples[-1]['reference']:.0f}\n{final_text}",anchor='ne',fill='#222',font=('Consolas',9))
        canvas.create_text(left,bottom+4,text=f'跨度 {duration:.3f}s / 板时刻',anchor='nw');canvas.create_text(right,bottom+4,text=f"{end}ms",anchor='ne')
    canvas.create_text(8,height-2,text=source+'；浅绿带±5计数；'+(dwell if goals else '目标诊断待更新')+'；缺帧断线',anchor='sw',fill='#555',font=('Consolas',9))
