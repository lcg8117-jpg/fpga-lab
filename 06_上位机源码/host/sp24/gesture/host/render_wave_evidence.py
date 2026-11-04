"""Render the real TkCanvas display list with clearly synthetic data; no COM."""
import json
import math
from pathlib import Path
import time
import tkinter as tk
import tkinter.font as tkfont
from PIL import Image, ImageDraw, ImageFont
from swing_tune_host import Engine, Window
from test_precise_telemetry import telemetry_packet
from tune_protocol import decode


def raster_canvas(canvas, destination):
    """Rasterize actual Canvas primitives, including native Tk text positions."""
    image = Image.new('RGB', (canvas.winfo_width(), canvas.winfo_height()), canvas.cget('background'))
    draw, records = ImageDraw.Draw(image), []
    for item in canvas.find_all():
        kind, coords = canvas.type(item), canvas.coords(item)
        fill = canvas.itemcget(item, 'fill')
        record = dict(kind=kind, coords=coords, fill=fill)
        if kind == 'line':
            points = list(zip(coords[0::2], coords[1::2]))
            width = max(1, round(float(canvas.itemcget(item, 'width'))))
            dash = canvas.itemcget(item, 'dash')
            record.update(width=width, dash=dash)
            if dash and len(points) == 2 and points[0][0] == points[1][0]:
                x, a, b = points[0][0], points[0][1], points[1][1]
                for y in range(round(a), round(b), 6):
                    draw.line((x, y, x, min(y+3, b)), fill=fill, width=width)
            else:
                draw.line(points, fill=fill, width=width)
        elif kind == 'oval':
            draw.ellipse(coords, fill=fill, outline=canvas.itemcget(item, 'outline'))
        elif kind == 'text':
            text, anchor = canvas.itemcget(item, 'text'), canvas.itemcget(item, 'anchor')
            font_info = tkfont.Font(font=canvas.itemcget(item, 'font')).actual()
            size = abs(font_info['size'])
            pixels = max(9, round(size*canvas.winfo_fpixels('1p'))) if font_info['size'] > 0 else size
            face = Path('C:/Windows/Fonts/msyhbd.ttc' if font_info['weight'] == 'bold' else 'C:/Windows/Fonts/msyh.ttc')
            font = ImageFont.truetype(str(face), pixels)
            box = draw.multiline_textbbox((0, 0), text, font=font, spacing=1)
            width, height = box[2]-box[0], box[3]-box[1]
            x, y = coords
            if 'w' not in anchor:
                x -= width if 'e' in anchor else width/2
            if 'n' not in anchor:
                y -= height if 's' in anchor else height/2
            draw.multiline_text((x-box[0], y-box[1]), text, font=font, fill=fill, spacing=1)
            record.update(text=text, anchor=anchor, font=font_info)
        records.append(record)
    image.save(destination)
    return records


def main():
    out = Path(__file__).resolve().parents[1]/'reports/host'
    out.mkdir(parents=True, exist_ok=True)
    engine = Engine(out/'unused_render_session', allow_device=False)
    root = tk.Tk()
    root.withdraw()
    window = Window(root, engine, 8371)
    root.geometry('1180x850+20000+20000')
    root.deiconify()
    root.update()
    try:
        now = time.monotonic()
        for n in range(1001):
            if 680 <= n <= 682:
                continue
            alpha = round((.15*math.sin(n*.12)+(.65 if n == 502 else 0))*math.pi/180*2**20)
            reference = round(.055*math.sin(n*.12-.35)*math.pi/180*2**20)
            target = round((90+35*math.sin(n*.012))*2**32)
            frame = decode(telemetry_packet(sequence=n, stamp=n*10, capture=n*10+3,
                alpha=alpha, reference=reference, encoder=round(90+32*math.sin(n*.012-.12)),
                target=target, pwm=round(2300*math.sin(n*.12-.6))))
            engine.wave.receive(frame.values, now-10+n*.01)
        window.expand_wave()
        top = window.large_canvas.winfo_toplevel()
        top.geometry('1180x700+20000+20000')
        root.update()
        window.plot()
        root.update_idletasks()
        canvas = window.large_canvas
        assert canvas.winfo_height() >= 550
        canvas.create_text(canvas.winfo_width()-10, canvas.winfo_height()-31,
            text='OFFLINE SYNTHETIC FIXTURE - NOT HARDWARE', anchor='se', fill='#edc6a0')
        records = raster_canvas(canvas, out/'waveform_synthetic_display.png')
        canvas.postscript(file=str(out/'waveform_synthetic_display.ps'), colormode='color')
        assert sum(r['kind'] == 'line' and r['fill'] == '#ef6a72' for r in records) == 3
        assert any(r.get('text') == '摆角 / 参考 °' for r in records)
        (out/'waveform_render.json').write_text(json.dumps(dict(status='PASS', hardware_opened=False,
            data_origin='Explicit synthetic fixture, not real hardware measurements',
            rendering='Actual TkCanvas display-list primitives rasterized with Windows YaHei fonts; native Canvas PostScript also exported. This is not an operating-system screenshot.',
            size=[canvas.winfo_width(),canvas.winfo_height()],
            accepted=engine.wave.accepted, missing=engine.wave.gaps,
            native_canvas_postscript='waveform_synthetic_display.ps',
            image='waveform_synthetic_display.png', display_list=records),ensure_ascii=False,indent=2),encoding='utf-8')
        assert engine.worker is None
        print('PASS actual TkCanvas plot display-list raster and PostScript: subdegree sine, narrow spike, target, reference and3-frame gap; explicitly synthetic')
    finally:
        window.alive = False
        root.destroy()
        engine.close()


if __name__ == '__main__':
    main()
