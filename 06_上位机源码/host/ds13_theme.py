"""Apply the showcase palette to the unmodified DS13 Host window."""
from tkinter import ttk


BACKGROUND = '#242622'
PLOT_BACKGROUND = '#252a28'
TEXT = '#f1f2e9'
MUTED = '#a6aa9b'
AMBER = '#dbaa64'
GREEN = '#a1bb90'
BUTTON = '#30332d'
BUTTON_HOVER = '#41463b'
BUTTON_BORDER = '#68705b'
STOP = '#e8a193'

# The upstream editor continues to create all of its own graph objects. Only
# the requested colors are translated; positions, line styles and data stay in
# the original draw() implementation.
PLOT_COLORS = {
    '#e1e9f0': '#3e433d',
    '#aebdce': '#62675d',
    '#eff3f7': '#343934',
    '#526477': MUTED,
    '#8db8e2': AMBER,
    '#1769b5': AMBER,
    '#df701d': GREEN,
    '#64788e': '#858d76',
    '#71859c': MUTED,
    'white': PLOT_BACKGROUND,
}


def _theme_canvas(canvas):
    canvas.configure(background=PLOT_BACKGROUND,highlightbackground='#62685d',highlightcolor=AMBER)
    if getattr(canvas,'_j280_theme_applied',False): return
    for name in ('create_line','create_text','create_oval'):
        original=getattr(canvas,name)
        def recolored(*args,_original=original,**kwargs):
            for key in ('fill','outline'):
                value=kwargs.get(key)
                if isinstance(value,str): kwargs[key]=PLOT_COLORS.get(value.lower(),value)
            return _original(*args,**kwargs)
        setattr(canvas,name,recolored)
    canvas._j280_theme_applied=True


def apply_theme(window):
    """Change colors only; never replace a control, callback or data source."""
    window.configure(background=BACKGROUND)
    style=ttk.Style(window)
    style.configure('.',background=BACKGROUND,foreground=TEXT)
    style.configure('TFrame',background=BACKGROUND)
    style.configure('TLabel',background=BACKGROUND,foreground=TEXT)
    style.configure('PlotTitle.TLabel',background=BACKGROUND,foreground=TEXT)
    style.configure('Small.TLabel',background=BACKGROUND,foreground=MUTED)
    style.configure('TButton',background=BUTTON,foreground='#e1e5d6',
                    bordercolor=BUTTON_BORDER,lightcolor=BUTTON,darkcolor=BUTTON,focuscolor=AMBER)
    style.map('TButton',background=[('disabled','#292c27'),('pressed','#383d33'),('active',BUTTON_HOVER)],
              foreground=[('disabled','#7c8376')])
    style.configure('Stop.TButton',background='#34302a',foreground=STOP,
                    bordercolor='#a17460',lightcolor='#34302a',darkcolor='#34302a',focuscolor=AMBER)
    style.map('Stop.TButton',background=[('disabled','#292c27'),('pressed','#473b31'),('active','#473b31')],
              foreground=[('disabled','#816c66'),('!disabled',STOP)])
    style.configure('TCombobox',fieldbackground=BUTTON,background=BUTTON,
                    foreground=TEXT,arrowcolor=TEXT,bordercolor=BUTTON_BORDER,
                    lightcolor=BUTTON,darkcolor=BUTTON,selectbackground='#59634d',selectforeground=TEXT)
    style.map('TCombobox',fieldbackground=[('readonly',BUTTON),('disabled','#292c27')],
              foreground=[('disabled','#7c8376'),('readonly',TEXT)],
              background=[('active',BUTTON_HOVER),('!active',BUTTON)])
    for option,value in (('background',BUTTON),('foreground',TEXT),
                         ('selectBackground','#59634d'),('selectForeground',TEXT)):
        window.option_add(f'*TCombobox*Listbox.{option}',value)
    for canvas in (window.editor.canvas,window.editor.actual_canvas):
        _theme_canvas(canvas)
    window.editor.draw()
