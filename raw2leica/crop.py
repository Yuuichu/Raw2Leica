"""Interactive crop in the same oriented geometry used for export."""
from __future__ import annotations

import io
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QPointF, QRectF
from PySide6.QtGui import QPixmap, QPainter, QPainterPath, QPen, QColor
from PySide6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                              QComboBox, QPushButton, QCheckBox, QDialogButtonBox)
from .core import develop, read_metadata, crop_bounds


class CropPreviewWorker(QThread):
    ready = Signal(bytes, object, str)

    def __init__(self, source: Path, exposure_ev=0.):
        super().__init__()
        self.source = source
        self.exposure_ev = exposure_ev

    def run(self):
        try:
            image = develop(self.source, read_metadata(self.source), self.exposure_ev)
            try:
                original_size = image.size
                image.thumbnail((1400, 1000))
                buffer = io.BytesIO()
                image.save(buffer, format='PNG')
                self.ready.emit(buffer.getvalue(), original_size, '')
            finally:
                image.close()
        except Exception as error:
            self.ready.emit(b'', (0, 0), str(error))


class CropCanvas(QWidget):
    changed = Signal(object)

    def __init__(self, box=None):
        super().__init__()
        self.pixmap = QPixmap()
        self.box = box or (0., 0., 1., 1.)
        self.pixel_ratio = None
        self.source_size = (0, 0)
        self.action = None
        self.anchor = None
        self.start_box = self.box
        self.setMinimumSize(560, 360)
        self.setMouseTracking(True)

    def image_rect(self):
        if self.pixmap.isNull():
            return QRectF()
        scale = min((self.width()-32)/self.pixmap.width(), (self.height()-32)/self.pixmap.height())
        w, h = self.pixmap.width()*scale, self.pixmap.height()*scale
        return QRectF((self.width()-w)/2, (self.height()-h)/2, w, h)

    def selection_rect(self):
        image = self.image_rect()
        x0,y0,x1,y1 = self.box
        return QRectF(image.left()+x0*image.width(), image.top()+y0*image.height(),
                      (x1-x0)*image.width(), (y1-y0)*image.height())

    def point(self, position):
        image = self.image_rect()
        return QPointF(max(0., min(1., (position.x()-image.left())/image.width())),
                       max(0., min(1., (position.y()-image.top())/image.height())))

    def set_image(self, data, size):
        self.pixmap.loadFromData(data)
        self.source_size = size
        self.update()
        self.changed.emit(self.box)

    def set_ratio(self, ratio):
        self.pixel_ratio = ratio
        if self.pixmap.isNull():
            return
        if ratio is None:
            return
        # Pixel ratio -> normalized-coordinate ratio; orientation is already applied.
        normalized = ratio * self.source_size[1] / self.source_size[0]
        x0,y0,x1,y1 = self.box
        cx,cy = (x0+x1)/2, (y0+y1)/2
        w = min(x1-x0, (y1-y0)*normalized)
        h = w/normalized
        self.set_box((cx-w/2, cy-h/2, cx+w/2, cy+h/2))

    def set_box(self, box):
        self.box = tuple(box)
        self.update()
        self.changed.emit(self.box)

    def reset(self):
        self.set_box((0.,0.,1.,1.))
        if self.pixel_ratio is not None:
            self.set_ratio(self.pixel_ratio)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#252b25'))
        if self.pixmap.isNull():
            painter.setPen(QColor('#c5cdbf'))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, '正在开发照片预览…')
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        image = self.image_rect()
        painter.drawPixmap(image, self.pixmap, QRectF(self.pixmap.rect()))
        crop = self.selection_rect()
        overlay = QPainterPath()
        overlay.setFillRule(Qt.FillRule.OddEvenFill)
        overlay.addRect(image)
        overlay.addRect(crop)
        painter.fillPath(overlay, QColor(0,0,0,145))
        painter.setPen(QPen(QColor('#f6f9f0'), 1.5))
        painter.drawRect(crop)
        painter.setPen(QPen(QColor(255,255,255,90), 1))
        for fraction in (1/3,2/3):
            x = crop.left()+fraction*crop.width()
            y = crop.top()+fraction*crop.height()
            painter.drawLine(QPointF(x,crop.top()),QPointF(x,crop.bottom()))
            painter.drawLine(QPointF(crop.left(),y),QPointF(crop.right(),y))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#fffefa'))
        for corner in (crop.topLeft(),crop.topRight(),crop.bottomLeft(),crop.bottomRight()):
            painter.drawRect(QRectF(corner.x()-4,corner.y()-4,8,8))

    def mousePressEvent(self, event):
        if self.pixmap.isNull() or event.button()!=Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        if not self.image_rect().adjusted(-10,-10,10,10).contains(pos):
            return
        self.start_box = self.box
        point = self.point(pos)
        rect = self.selection_rect()
        corners = [rect.topLeft(),rect.topRight(),rect.bottomLeft(),rect.bottomRight()]
        x0,y0,x1,y1 = self.box
        opposites = [(x1,y1),(x0,y1),(x1,y0),(x0,y0)]
        if not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            for corner,opposite in zip(corners,opposites):
                if abs(pos.x()-corner.x())<12 and abs(pos.y()-corner.y())<12:
                    self.action='resize'
                    self.anchor=QPointF(*opposite)
                    return
            if rect.contains(pos):
                self.action='move'
                self.anchor=point
                return
        self.action='draw'
        self.anchor=point

    def mouseMoveEvent(self, event):
        if self.pixmap.isNull() or not self.action:
            return
        point = self.point(event.position())
        if self.action=='move':
            x0,y0,x1,y1 = self.start_box
            dx=max(-x0,min(1-x1,point.x()-self.anchor.x()))
            dy=max(-y0,min(1-y1,point.y()-self.anchor.y()))
            self.set_box((x0+dx,y0+dy,x1+dx,y1+dy))
            return
        dx,dy = point.x()-self.anchor.x(),point.y()-self.anchor.y()
        sx,sy = (1 if dx>=0 else -1),(1 if dy>=0 else -1)
        w,h = abs(dx),abs(dy)
        if self.pixel_ratio is not None:
            ratio = self.pixel_ratio*self.source_size[1]/self.source_size[0]
            available_w = 1-self.anchor.x() if sx>0 else self.anchor.x()
            available_h = 1-self.anchor.y() if sy>0 else self.anchor.y()
            w=min(max(w,h*ratio),available_w,available_h*ratio)
            h=w/ratio
        if w < .002 or h < .002:
            return
        end=QPointF(self.anchor.x()+sx*w,self.anchor.y()+sy*h)
        self.set_box((min(self.anchor.x(),end.x()),min(self.anchor.y(),end.y()),
                      max(self.anchor.x(),end.x()),max(self.anchor.y(),end.y())))

    def mouseReleaseEvent(self,event):
        self.action=None


class CropDialog(QDialog):
    def __init__(self, source, box=None, *, selection_count=1, exposure_ev=0., parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'裁剪照片 — {source.name}')
        self.resize(960,760)
        self.closing=False
        self.canvas=CropCanvas(box)
        layout=QVBoxLayout(self)
        header=QHBoxLayout()
        header.addWidget(QLabel('裁剪比例'))
        self.ratio=QComboBox()
        for title,ratio in [('自由',None),('原图比例','original'),('3:2',3/2),('2:3',2/3),
                            ('4:3',4/3),('3:4',3/4),('16:9',16/9),('9:16',9/16),('1:1',1.)]:
            self.ratio.addItem(title,ratio)
        self.ratio.currentIndexChanged.connect(self.ratio_changed)
        header.addWidget(self.ratio)
        reset=QPushButton('重置裁剪')
        reset.clicked.connect(self.reset)
        header.addWidget(reset)
        header.addStretch()
        layout.addLayout(header)
        layout.addWidget(self.canvas,1)
        self.info=QLabel('使用实际 RAW 开发结果生成预览，首次打开需要等待。')
        layout.addWidget(self.info)
        layout.addWidget(QLabel('拖动四角调整范围；拖动框内移动；按住 Shift 拖动可重新画框。'))
        self.apply_all=QCheckBox(f'将同一相对裁剪范围应用到 {selection_count} 张选中照片')
        self.apply_all.setVisible(selection_count>1)
        layout.addWidget(self.apply_all)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('应用裁剪')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.canvas.changed.connect(self.describe)
        self.worker=CropPreviewWorker(source, exposure_ev)
        self.worker.ready.connect(self.loaded)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()

    def loaded(self,data,size,error):
        if error:
            self.info.setText(f'无法开发照片：{error}')
            return
        self.canvas.set_image(data,size)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        self.ratio_changed()

    def describe(self,box):
        w,h=self.canvas.source_size
        x0,y0,x1,y1=box
        left,top,right,bottom=crop_bounds((w,h),box)
        cw,ch=right-left,bottom-top
        self.info.setText(f'原图 {w} × {h}  →  裁剪约 {cw} × {ch} 像素（导出时再按尺寸设置缩小）')

    def ratio_changed(self,*_):
        ratio=self.ratio.currentData()
        if ratio=='original':
            w,h=self.canvas.source_size
            ratio=w/h if h else None
        self.canvas.set_ratio(ratio)

    def reset(self):
        self.ratio.setCurrentIndex(0)
        self.canvas.reset()

    def crop_box(self):
        box=self.canvas.box
        return None if all(abs(a-b)<1e-7 for a,b in zip(box,(0,0,1,1))) else box

    def done(self,result):
        if self.worker.isRunning():
            self.closing=True
            self.info.setText('正在结束预览开发，完成后关闭…')
            return
        super().done(result)

    def worker_finished(self):
        if self.closing:
            self.reject()
