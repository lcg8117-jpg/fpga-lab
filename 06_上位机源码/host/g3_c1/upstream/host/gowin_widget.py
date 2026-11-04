"""The complete visible UI: one button, one GOWIN canvas, one status line."""
import tkinter as tk
from tkinter import ttk

class GowinWidget(ttk.Frame):
    def __init__(self,parent,controller):
        super().__init__(parent,padding=10)
        self.controller=controller;self.generation=-1;self.points=[];self.previous=None
        self.play_button=ttk.Button(self,text='播放运动图像',command=self.play)
        self.play_button.pack(anchor='w',pady=(0,8))
        self.canvas=tk.Canvas(self,bg='white',height=430,highlightthickness=0)
        self.canvas.pack(fill='both',expand=True)
        self.message=tk.StringVar(value=controller.status)
        ttk.Label(self,textvariable=self.message,wraplength=1000).pack(fill='x',pady=(8,0))
        ttk.Label(self,text='GOWIN 映射显示 · 由设备新采样推进',foreground='#777').pack(anchor='w')
        self.canvas.bind('<Configure>',self.redraw)
        self.after_id=self.after(50,self.poll)

    def play(self):
        self.controller.play();self.message.set(self.controller.status)

    def draw_points(self,points):
        pipeline=self.controller.pipeline
        if not pipeline:return
        layout=pipeline.mapper.layout
        width=max(300,self.canvas.winfo_width());height=max(200,self.canvas.winfo_height())
        scale=min((width-70)/(layout['width']+40),(height-60)/(2*layout['y_limit']+30))
        # One real mapped point per accepted source point. No point smoothing,
        # interpolation, filling, target substitution, or timer-only drawing.
        for p in points:
            x=width/2+p['x_display']*scale;y=height/2-p['y_display']*scale
            if p['break_before']:self.previous=None
            if self.previous is not None:self.canvas.create_line(*self.previous,x,y,fill='#1764cc',width=1.05,capstyle='round',joinstyle='round')
            else:self.canvas.create_oval(x-.7,y-.7,x+.7,y+.7,outline='#1764cc')
            self.previous=x,y

    def redraw(self,event=None):
        self.canvas.delete('all');self.previous=None;self.draw_points(self.points)

    def poll(self):
        try:
            new=self.controller.poll()
            if self.generation!=self.controller.generation:
                self.generation=self.controller.generation;self.points=[];self.canvas.delete('all');self.previous=None
            self.points.extend(new);self.draw_points(new)
            self.message.set(self.controller.status)
            self.play_button.configure(state='disabled' if self.controller.busy else 'normal')
        except Exception as exc:
            self.controller.close();self.message.set('显示处理失败：'+str(exc));self.play_button.configure(state='disabled')
        self.after_id=self.after(50,self.poll)

    def destroy(self):
        self.after_cancel(self.after_id);self.controller.close();super().destroy()
