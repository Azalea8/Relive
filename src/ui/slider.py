"""SeekSlider — custom slider with click-to-seek and mark-in/out lines."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider, QWidget
from src.logger import get as _log


class DensityOverlay(QWidget):
    """Thin transparent bar drawn above the slider to show danmaku density."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._density: list[int] = []
        self.setFixedHeight(48)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_density(self, buckets: list[int]):
        self._density = buckets
        self.update()

    def clear_density(self):
        self._density = []
        self.update()

    def paintEvent(self, event):
        if not self._density or len(self._density) < 2:
            return
        peak = max(self._density)
        if peak == 0:
            return
        w = self.width()
        h = self.height()
        step = w / (len(self._density) - 1)

        def pt(i):
            return (i * step, h - 1 - (self._density[i] / peak) * (h - 2))

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(90, 100, 140, 180))

        path = QPainterPath()
        path.moveTo(*pt(0))
        n = len(self._density)
        for i in range(n - 1):
            # Catmull-Rom → cubic Bezier control points
            x0, y0 = pt(max(i - 1, 0))
            x1, y1 = pt(i)
            x2, y2 = pt(i + 1)
            x3, y3 = pt(min(i + 2, n - 1))
            cp1x = x1 + (x2 - x0) / 6
            cp1y = y1 + (y2 - y0) / 6
            cp2x = x2 - (x3 - x1) / 6
            cp2y = y2 - (y3 - y1) / 6
            path.cubicTo(cp1x, cp1y, cp2x, cp2y, x2, y2)
        painter.drawPath(path)
        painter.end()


class SeekSlider(QSlider):
    """Slider with click-to-seek + mark-in/out lines."""

    _log = _log("slider")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mark_in_pos: int | None = None
        self._mark_out_pos: int | None = None

    def set_mark_in_line(self, pos: int | None):
        self._mark_in_pos = pos
        self.update()

    def set_mark_out_line(self, pos: int | None):
        self._mark_out_pos = pos
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            val = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), int(event.position().x()), self.width()
            )
            self._log.info("click: x=%d -> val=%d (range %d-%d)",
                           int(event.position().x()), val, self.minimum(), self.maximum())
            self.setValue(val)
            self.sliderPressed.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.maximum() <= 0:
            return

        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt,
            QStyle.SubControl.SC_SliderGroove, self,
        )
        total = self.maximum()
        groove_x = groove.x()
        groove_w = groove.width()
        groove_y = groove.y()
        groove_h = groove.height()

        painter = QPainter(self)
        region_y = groove_y - 5
        region_h = groove_h + 10

        # Mark-in line (orange)
        if self._mark_in_pos is not None and self._mark_in_pos >= 0:
            x = groove_x + int(self._mark_in_pos / total * groove_w)
            painter.setPen(QColor(234, 146, 90, 220))
            painter.drawLine(x, region_y, x, region_y + region_h)
            painter.drawLine(x + 1, region_y, x + 1, region_y + region_h)

        # Mark-out line (purple)
        if self._mark_out_pos is not None and self._mark_out_pos >= 0:
            x = groove_x + int(self._mark_out_pos / total * groove_w)
            painter.setPen(QColor(124, 58, 237, 220))
            painter.drawLine(x, region_y, x, region_y + region_h)
            painter.drawLine(x + 1, region_y, x + 1, region_y + region_h)

        painter.end()
