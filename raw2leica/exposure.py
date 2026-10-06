"""Photo exposure editor with cached scene-linear previews and precise controls."""
from __future__ import annotations
import math
import time
from pathlib import Path

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt, QThread, QTimer, Signal, QPointF
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor
from PySide6.QtWidgets import (QWidget,QDialog,QLabel,QSlider,QDoubleSpinBox,QVBoxLayout,QHBoxLayout,
                              QPushButton,QCheckBox,QComboBox,QDialogButtonBox)
from .core import read_metadata, transform_image
from .imaging import prepare_image


def step_for(modifiers):
    if modifiers & Qt.KeyboardModifier.AltModifier:
        return .01
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        return 1.
    return .1


class EVSlider(QSlider):
    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.setRange(-400,400)
        self.setSingleStep(10)
        self.setPageStep(100)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.drag_x=None
        self.drag_value=0.

    def mousePressEvent(self,event):
        if event.button()!=Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.setFocus()
        self.drag_x=event.position().x()
        span=max(1,self.width()-16)
        handle=8+(self.value()-self.minimum())/(self.maximum()-self.minimum())*span
        if abs(self.drag_x-handle)>12 and not event.modifiers() & Qt.KeyboardModifier.AltModifier:
            self.setValue(round(self.minimum()+(self.drag_x-8)/span*(self.maximum()-self.minimum())))
        self.drag_value=float(self.value())
        self.setSliderDown(True)
        event.accept()

    def mouseMoveEvent(self,event):
        if self.drag_x is None:
            super().mouseMoveEvent(event)
            return
        x=event.position().x()
        precision=.1 if event.modifiers() & Qt.KeyboardModifier.AltModifier else 1.
        delta=(x-self.drag_x)*(self.maximum()-self.minimum())/max(1,self.width()-16)*precision
        self.drag_value=max(self.minimum(),min(self.maximum(),self.drag_value+delta))
        self.drag_x=x
        self.setValue(round(self.drag_value))
        event.accept()

    def mouseReleaseEvent(self,event):
        self.drag_x=None
        self.setSliderDown(False)
        event.accept()

    def mouseDoubleClickEvent(self,event):
        self.setValue(0)
        event.accept()

    def keyPressEvent(self,event):
        if event.key() in (Qt.Key.Key_Up,Qt.Key.Key_Right,Qt.Key.Key_Down,Qt.Key.Key_Left):
            sign=1 if event.key() in (Qt.Key.Key_Up,Qt.Key.Key_Right) else -1
            self.setValue(self.value()+sign*round(step_for(event.modifiers())*100))
            event.accept()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self,event):
        # No accidental changes while scrolling the surrounding interface.
        if not self.hasFocus():
            event.ignore()
            return
        delta=event.angleDelta().y()/120 if event.angleDelta().y() else event.pixelDelta().y()/40
        self.setValue(self.value()+round(delta*step_for(event.modifiers())*100))
        event.accept()


class EVSpinBox(QDoubleSpinBox):
    def __init__(self):
        super().__init__()
        self.setRange(-4,4)
        self.setDecimals(2)
        self.setSingleStep(.1)
        self.setSuffix(' EV')
        self.setKeyboardTracking(False)
        self.setMinimumWidth(122)

    def textFromValue(self,value):
        return f'{value:+.2f}'

    def keyPressEvent(self,event):
        if event.key() in (Qt.Key.Key_Up,Qt.Key.Key_Down):
            self.interpretText()
            sign=1 if event.key()==Qt.Key.Key_Up else -1
            self.setValue(round(self.value()+sign*step_for(event.modifiers()),2))
            event.accept()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self,event):
        if not self.hasFocus():
            event.ignore()
            return
        delta=event.angleDelta().y()/120 if event.angleDelta().y() else event.pixelDelta().y()/40
        self.setValue(round(self.value()+delta*step_for(event.modifiers()),2))
        event.accept()


class ExposureControl(QWidget):
    changed=Signal(float)

    def __init__(self,value=0.):
        super().__init__()
        layout=QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        head=QHBoxLayout()
        head.addWidget(QLabel('曝光补偿'))
        head.addStretch()
        self.spin=EVSpinBox()
        head.addWidget(self.spin)
        self.reset=QPushButton('↺')
        self.reset.setFixedWidth(34)
        self.reset.setToolTip('重置为 0.00 EV；也可双击滑块')
        head.addWidget(self.reset)
        layout.addLayout(head)
        self.slider=EVSlider()
        layout.addWidget(self.slider)
        ticks=QHBoxLayout()
        for text in ['−4','−2','0','+2','+4']:
            l=QLabel(text)
            l.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ticks.addWidget(l)
        layout.addLayout(ticks)
        self.slider.valueChanged.connect(lambda v:self.set_value(v/100))
        self.spin.valueChanged.connect(self.set_value)
        self.reset.clicked.connect(lambda:self.set_value(0.))
        self._value=0.
        self.set_value(value)

    def value(self):
        return self._value

    def set_value(self,value):
        value=round(max(-4.,min(4.,value)),2)
        old=self._value
        self._value=value
        self.slider.blockSignals(True);self.spin.blockSignals(True)
        self.slider.setValue(round(value*100));self.spin.setValue(value)
        self.slider.blockSignals(False);self.spin.blockSignals(False)
        if value!=old:
            self.changed.emit(value)


class PreviewLoader(QThread):
    ready=Signal(object,str)

    def __init__(self,source):
        super().__init__()
        self.source=source

    def run(self):
        try:
            self.ready.emit(prepare_image(self.source,read_metadata(self.source),preview_edge=1500),'')
        except Exception as error:
            self.ready.emit(None,str(error))


class RenderWorker(QThread):
    ready=Signal(object)
    failed=Signal(int,str)

    def __init__(self,prepared,ev,crop_box,warnings,token):
        super().__init__()
        self.prepared,self.ev,self.crop_box,self.warnings,self.token=prepared,ev,crop_box,warnings,token

    def run(self):
        try:
            started=time.perf_counter()
            original=self.prepared.render(self.ev)
            image=transform_image(original,crop_box=self.crop_box)
            original.close()
            pixels=np.array(image)
            image.close()
            hist=[np.bincount(pixels[:,:,c].ravel(),minlength=256).tolist() for c in range(3)]
            high=np.any(pixels>=254,axis=2)
            low=np.all(pixels<=1,axis=2)
            if self.warnings:
                pixels[high]=(239,58,46)
                pixels[low]=(53,107,242)
            self.ready.emit({'token':self.token,'data':pixels.tobytes(),'width':pixels.shape[1],
                'height':pixels.shape[0],'hist':hist,'high':float(high.mean()*100),'low':float(low.mean()*100),
                'elapsed_ms':(time.perf_counter()-started)*1000})
        except Exception as error:
            self.failed.emit(self.token,str(error))


class Histogram(QWidget):
    def __init__(self):
        super().__init__()
        self.hist=None
        self.setMinimumSize(230,125)
        self.setMaximumHeight(150)

    def paintEvent(self,event):
        painter=QPainter(self)
        painter.fillRect(self.rect(),QColor('#242b25'))
        if self.hist is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # sqrt scale keeps low-count tonal regions visible; bins are display sRGB.
        scale=max(1.,max(math.sqrt(v) for channel in self.hist for v in channel))
        for channel,color in zip(self.hist,['#e07060','#85b272','#719bc9']):
            painter.setPen(QPen(QColor(color),1))
            last=None
            for x,count in enumerate(channel):
                point=QPointF(8+x*(self.width()-16)/255,self.height()-8-math.sqrt(count)/scale*(self.height()-16))
                if last is not None:
                    painter.drawLine(last,point)
                last=point


class ExposureViewer(QLabel):
    valueRequested=Signal(float)
    compareHeld=Signal(bool)

    def __init__(self):
        super().__init__('正在开发高精度预览…')
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet('background:#252b25;color:#c6cec0;border-radius:8px;')
        self.setMinimumSize(560,400)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.pixmap_data=None
        self.ev=0.
        self.speed=False
        self.drag_anchor=None
        self.drag_ev=0.

    def set_image(self,result):
        image=QImage(result['data'],result['width'],result['height'],result['width']*3,QImage.Format.Format_RGB888).copy()
        self.pixmap_data=QPixmap.fromImage(image)
        self.update_image()

    def update_image(self):
        if self.pixmap_data is not None:
            self.setPixmap(self.pixmap_data.scaled(self.size(),Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self,event):
        self.update_image()
        super().resizeEvent(event)

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Q:
            self.speed=True
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            event.accept()
        elif event.key()==Qt.Key.Key_Space:
            if self.speed:
                self.valueRequested.emit(0.)
            else:
                self.compareHeld.emit(True)
            event.accept()
        elif event.key() in (Qt.Key.Key_Up,Qt.Key.Key_Right,Qt.Key.Key_Down,Qt.Key.Key_Left):
            sign=1 if event.key() in (Qt.Key.Key_Up,Qt.Key.Key_Right) else -1
            self.valueRequested.emit(self.ev+sign*step_for(event.modifiers()))
            event.accept()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self,event):
        if event.isAutoRepeat():
            return
        if event.key()==Qt.Key.Key_Q:
            self.speed=False
            self.drag_anchor=None
            self.unsetCursor()
            event.accept()
        elif event.key()==Qt.Key.Key_Space:
            self.compareHeld.emit(False)
            event.accept()
        else:
            super().keyReleaseEvent(event)

    def focusOutEvent(self,event):
        self.speed=False
        self.drag_anchor=None
        self.unsetCursor()
        self.compareHeld.emit(False)
        super().focusOutEvent(event)

    def mousePressEvent(self,event):
        self.setFocus()
        if self.speed and event.button()==Qt.MouseButton.LeftButton:
            self.drag_anchor=event.position().x()
            self.drag_ev=self.ev
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self.drag_anchor is not None:
            precision=.1 if event.modifiers() & Qt.KeyboardModifier.AltModifier else 1.
            x=event.position().x()
            self.drag_ev=max(-4.,min(4.,self.drag_ev+(x-self.drag_anchor)*.01*precision))
            self.drag_anchor=x
            self.valueRequested.emit(self.drag_ev)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        self.drag_anchor=None
        super().mouseReleaseEvent(event)

    def wheelEvent(self,event):
        if self.speed:
            delta=event.angleDelta().y()/120 if event.angleDelta().y() else event.pixelDelta().y()/40
            self.valueRequested.emit(self.ev+delta*step_for(event.modifiers()))
            event.accept()
        else:
            event.ignore()


class ExposureDialog(QDialog):
    def __init__(self,source,value=0.,crop_box=None,*,selection_count=1,parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'曝光调整 — {source.name}')
        self.resize(1160,780)
        self.initial_value=value
        self.prepared=None
        self.crop_box=crop_box
        self.render_worker=None
        self.token=0
        self.rendered_token=-1
        self.closing=False
        self.close_result=QDialog.DialogCode.Rejected
        self.compare_key=False
        self.preview_ms=0.
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel(source.name))
        body=QHBoxLayout()
        self.viewer=ExposureViewer()
        body.addWidget(self.viewer,1)
        panel=QWidget();panel.setFixedWidth(285)
        side=QVBoxLayout(panel);side.setContentsMargins(14,4,0,4)
        side.addWidget(QLabel('RGB 直方图'))
        self.histogram=Histogram();side.addWidget(self.histogram)
        self.control=ExposureControl(value);side.addWidget(self.control)
        self.compare=QCheckBox('对比调整前（0.00 EV）')
        self.warnings=QCheckBox('显示高光 / 阴影剪裁')
        side.addWidget(self.compare);side.addWidget(self.warnings)
        self.clip_info=QLabel('预览剪裁：等待计算');self.clip_info.setWordWrap(True);side.addWidget(self.clip_info)
        side.addSpacing(14)
        help_text=QLabel('方向键：0.1 EV\nShift + 方向键：1 EV\nAlt + 方向键：0.01 EV\n双击滑块 / ↺：归零\n\n点击图像后，按住 Q 拖动或滚动\nQ + 空格：归零\n按住空格：查看调整前')
        help_text.setWordWrap(True);side.addWidget(help_text)
        side.addStretch()
        self.batch_mode=QComboBox()
        self.batch_mode.addItem('仅当前照片', 'single')
        if selection_count>1:
            self.batch_mode.addItem(f'{selection_count} 张选中照片统一设值','absolute')
            self.batch_mode.addItem(f'{selection_count} 张选中照片相对增减','relative')
        side.addWidget(self.batch_mode)
        body.addWidget(panel);layout.addLayout(body,1)
        self.status=QLabel('首次开发后缓存线性图像；拖动时直接更新预览。')
        self.status.setWordWrap(True);layout.addWidget(self.status)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('应用曝光')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.buttons.accepted.connect(self.accept);self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(45)
        self.timer.timeout.connect(self.render_latest)
        self.control.changed.connect(self.on_ev)
        self.viewer.ev=value
        self.viewer.valueRequested.connect(self.control.set_value)
        self.viewer.compareHeld.connect(self.on_compare_key)
        self.compare.toggled.connect(self.request_render);self.warnings.toggled.connect(self.request_render)
        self.loader=PreviewLoader(source)
        self.loader.ready.connect(self.loaded)
        self.loader.finished.connect(self.finish_or_render)
        self.loader.start()

    def loaded(self,prepared,error):
        if error:
            self.status.setText('无法开发预览：'+error)
            self.viewer.setText('预览开发失败')
            return
        self.prepared=prepared
        kind='RAW · 16-bit 线性开发' if prepared.is_raw else 'JPEG · 转回线性 RGB；已剪裁细节无法恢复'
        self.status.setText(f'{kind} · {prepared.source_size[0]} × {prepared.source_size[1]}')
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        self.request_render()

    def on_ev(self,value):
        self.viewer.ev=value
        self.request_render()

    def on_compare_key(self,held):
        self.compare_key=held
        self.request_render()

    def request_render(self,*_):
        self.token+=1
        self.timer.start()

    def render_latest(self):
        if self.closing or self.prepared is None or (self.render_worker and self.render_worker.isRunning()):
            return
        if self.token==self.rendered_token:
            return
        ev=0. if self.compare.isChecked() or self.compare_key else self.control.value()
        self.render_worker=RenderWorker(self.prepared,ev,self.crop_box,self.warnings.isChecked(),self.token)
        self.render_worker.ready.connect(self.rendered)
        self.render_worker.failed.connect(self.render_failed)
        self.render_worker.finished.connect(self.finish_or_render)
        self.render_worker.start()

    def rendered(self,result):
        if result['token']!=self.token or self.closing:
            return
        self.rendered_token=result['token']
        self.preview_ms=result['elapsed_ms']
        self.viewer.set_image(result)
        self.histogram.hist=result['hist'];self.histogram.update()
        self.clip_info.setText(f"预览剪裁：高光 {result['high']:.2f}% / 阴影 {result['low']:.2f}%\n红色 = 高光；蓝色 = 阴影")

    def render_failed(self,token,error):
        if token==self.token and not self.closing:
            self.rendered_token=token
            self.status.setText('预览更新失败：'+error+'；重新调整曝光可重试。')

    def finish_or_render(self):
        busy=self.loader.isRunning() or (self.render_worker and self.render_worker.isRunning())
        if self.closing:
            if not busy:
                super().done(self.close_result)
        elif not busy:
            self.render_latest()

    def done(self,result):
        # Commit a numeric edit even if Apply is clicked while the field is focused.
        self.control.spin.interpretText()
        busy=self.loader.isRunning() or (self.render_worker and self.render_worker.isRunning())
        self.timer.stop()
        if busy:
            self.closing=True
            self.close_result=result
            self.buttons.setEnabled(False)
            self.status.setText('正在结束当前预览，完成后关闭…')
            return
        super().done(result)
