"""Draw report figures at native double resolution using logical layout units."""
from numbers import Real

from PIL import Image, ImageDraw

RENDER_SCALE = 2


def scaled(value):
    if isinstance(value, Real):
        return float(value) * RENDER_SCALE
    return tuple(scaled(v) for v in value)


class FigureCanvas:
    def __init__(self, size, background='white'):
        self.image = Image.new('RGB', tuple(int(v * RENDER_SCALE) for v in size), background)

    def paste(self, source, xy):
        if isinstance(source, FigureCanvas):
            image = source.image
        else:
            image = source.resize(tuple(v * RENDER_SCALE for v in source.size), Image.Resampling.NEAREST)
        self.image.paste(image, tuple(int(v * RENDER_SCALE) for v in xy))

    def save(self, path, **kwargs):
        self.image.save(path, dpi=(450, 450), **kwargs)


class FigureDraw:
    def __init__(self, canvas):
        self.draw = ImageDraw.Draw(canvas.image)

    def shape(self, method, xy, **kwargs):
        if 'width' in kwargs:
            kwargs['width'] = max(1, round(kwargs['width'] * RENDER_SCALE))
        return getattr(self.draw, method)(scaled(xy), **kwargs)

    def line(self, xy, **kwargs): return self.shape('line', xy, **kwargs)
    def polygon(self, xy, **kwargs):
        # Pillow creates a full-canvas mask for polygon outlines wider than one
        # pixel. Draw the closed edge path directly so dense FE meshes stay fast.
        outline = kwargs.pop('outline', None)
        width = max(1, round(kwargs.pop('width', 1) * RENDER_SCALE))
        points = scaled(xy)
        self.draw.polygon(points, **kwargs)
        if outline is not None:
            self.draw.line((*points, points[0]), fill=outline, width=width, joint='curve')
    def rectangle(self, xy, **kwargs): return self.shape('rectangle', xy, **kwargs)
    def ellipse(self, xy, **kwargs): return self.shape('ellipse', xy, **kwargs)
    def rounded_rectangle(self, xy, radius, **kwargs):
        return self.shape('rounded_rectangle', xy, radius=radius * RENDER_SCALE, **kwargs)

    def text(self, xy, text, **kwargs):
        kwargs.setdefault('spacing', 8 * RENDER_SCALE)
        return self.draw.text(scaled(xy), text, **kwargs)

    def textbbox(self, xy, text, **kwargs):
        kwargs.setdefault('spacing', 8 * RENDER_SCALE)
        return tuple(v / RENDER_SCALE for v in self.draw.textbbox(scaled(xy), text, **kwargs))

    def textlength(self, text, **kwargs):
        return self.draw.textlength(text, **kwargs) / RENDER_SCALE


def wrap_text(draw, text, font, width):
    """Wrap long Chinese labels rather than shrinking them below a readable size."""
    lines, current = [], ''
    for char in str(text):
        if char == '\n':
            lines.append(current); current = ''
        elif current and draw.textlength(current + char, font=font) > width:
            lines.append(current); current = char
        else:
            current += char
    lines.append(current)
    return '\n'.join(lines)
