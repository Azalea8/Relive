"""SeekSlider — custom slider with click-to-seek and mark-in/out lines."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QSlider, QStyle, QStyleOptionSlider
from src.logger import get as _log


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
