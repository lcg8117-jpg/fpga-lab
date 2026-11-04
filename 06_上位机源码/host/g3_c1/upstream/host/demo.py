"""Run the one-button example or import GowinWidget into the existing app.

Default: recorded physical-data replay, no serial/device access.
--port COM5: explicit standalone connection; manual ZERO/RUN stays with the
existing controller/physical buttons, never triggered by this example.
"""
from pathlib import Path
import argparse,json,time,tkinter as tk
from gowin import GowinController,DisplayPipeline
from gowin_widget import GowinWidget

class ReplayController:
    def __init__(self,path,speed=4):
        path=Path(path)
        if path.stat().st_size>16*1024**2:raise ValueError('回放文件超过16MiB')
        self.record=json.loads(path.read_text(encoding='utf-8-sig'))
        if self.record['format']!='GowinPhysicalReplayV1':raise ValueError('不是带来源的实测回放')
        self.speed=speed;self.pipeline=None;self.generation=0;self.busy=False;self.index=0
        self.status='历史实测日志回放 · 不连接设备；点按钮查看显示组件'
    def play(self):
        if self.busy:return False
        self.pipeline=DisplayPipeline(self.record['manifest']);self.index=0
        self.generation+=1;self.busy=True;self.started=time.monotonic()
        self.status='历史实测回放（加速显示，未改变样本坐标）';return True
    def poll(self):
        if not self.busy:return []
        rows=self.record['frames'];end=(time.monotonic()-self.started)*1000*self.speed
        start=self.record['manifest']['play_ms'];out=[]
        while self.index<len(rows) and rows[self.index]['board_ms']-start<=end:
            p=self.pipeline.feed(rows[self.index]);self.index+=1
            if p:out.append(p)
        if self.index==len(rows):self.busy=False;self.status='历史日志回放结束 · 这不是裁剪固件的新实测成绩'
        return out
    def close(self):self.busy=False

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',help='Only when this process owns the serial port')
    parser.add_argument('--replay',type=Path,default=Path(__file__).parent/'examples/R04_actual_replay.json')
    args=parser.parse_args()
    owner=None
    if args.port:
        from client import SerialOwner
        owner=SerialOwner(5,args.port,Path(__file__).parent/'logs').start()
        controller=GowinController(owner,export_directory=Path(__file__).parent/'records')
    else:controller=ReplayController(args.replay)
    root=tk.Tk();root.title('GOWIN · 单按钮显示接入示例');root.geometry('1200x520')
    widget=GowinWidget(root,controller);widget.pack(fill='both',expand=True)
    def close():
        widget.destroy()
        if owner:owner.stop()
        root.destroy()
    root.protocol('WM_DELETE_WINDOW',close);root.mainloop()

if __name__=='__main__':main()
