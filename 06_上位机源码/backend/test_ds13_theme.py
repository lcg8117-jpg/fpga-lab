"""Offline check of the delivered DS13 drawing widget and the color adapter."""
import os
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from host.ds13_bridge import load_upstream
from host.ds13_theme import apply_theme
from launch_host import configure_tk_runtime


class DS13ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        environment=patch.dict(os.environ)
        environment.start();cls.addClassCleanup(environment.stop)
        configure_tk_runtime()

    def test_original_plot_geometry_and_bindings_survive_recoloring(self):
        modules=load_upstream()
        try: root=tk.Tk()
        except tk.TclError as exc: self.skipTest(f'Tk display unavailable: {exc}')
        root.withdraw()
        try:
            ttk.Style(root).theme_use('clam')
            editor=modules['draw_editor'].DrawEditor(root,lambda:None)
            root.editor=editor
            root.update_idletasks()
            editor.timeline.rows.extend([
                dict(t=0,target_deg=0,actual_deg=5,reference_deg=0,point_sequence=1,gap=False),
                dict(t=1,target_deg=30,actual_deg=15,reference_deg=20,point_sequence=2,gap=False),
                dict(t=2,target_deg=10,actual_deg=25,reference_deg=15,point_sequence=3,gap=False),
            ])
            editor.timeline.cursor=2
            editor.draw()
            def items(canvas):
                result=[]
                for item in canvas.find_all():
                    options={name:canvas.itemcget(item,name) for name in ('width','dash','text')
                             if name in canvas.itemconfig(item)}
                    result.append((canvas.type(item),canvas.coords(item),options))
                return result
            before=[items(editor.canvas),items(editor.actual_canvas)]
            binding=editor.canvas.bind('<ButtonPress-1>')
            controller=editor.controller
            apply_theme(root)
            editor.draw()
            self.assertEqual([items(editor.canvas),items(editor.actual_canvas)],before)
            self.assertEqual(editor.canvas.bind('<ButtonPress-1>'),binding)
            self.assertIs(editor.controller,controller)
            self.assertEqual(editor.canvas.cget('background').lower(),'#252a28')
            self.assertEqual(editor.actual_canvas.cget('background').lower(),'#252a28')
            def line_colors(canvas):
                return {canvas.itemcget(item,'fill').lower() for item in canvas.find_all() if canvas.type(item)=='line'}
            self.assertIn('#dbaa64',line_colors(editor.canvas))
            self.assertIn('#dbaa64',line_colors(editor.actual_canvas))
            self.assertIn('#a1bb90',line_colors(editor.actual_canvas))
            self.assertEqual(ttk.Style(root).lookup('TButton','background').lower(),'#30332d')
        finally:
            root.destroy()


if __name__=='__main__':unittest.main()
