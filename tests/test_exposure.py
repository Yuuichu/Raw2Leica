import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import math
import time
from pathlib import Path
from dataclasses import replace
import json

import numpy as np
import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication,QDialog
from PySide6.QtCore import Qt,QPoint,QSettings
from PySide6.QtTest import QTest

from raw2leica.imaging import (exposure_gain,exposure_lut,srgb_to_linear,linear_to_srgb,
                              PreparedImage,prepare_image)
from raw2leica.core import Options,load_profiles,convert,read_metadata
from raw2leica.exposure import ExposureControl,ExposureDialog
from raw2leica.app import MainWindow


def wait(app,predicate,timeout=20):
    deadline=time.monotonic()+timeout
    while not predicate():
        app.processEvents();QTest.qWait(10)
        if time.monotonic()>deadline:
            raise AssertionError('Exposure UI timed out')
    app.processEvents()


def test_exposure_gain_and_linear_roundtrip():
    assert exposure_gain(1)==2 and exposure_gain(-1)==.5
    assert exposure_gain(4)==16 and exposure_gain(-4)==1/16
    values=np.array([0,.01,.18,.5,1],dtype=np.float32)
    assert np.allclose(srgb_to_linear(linear_to_srgb(values)),values,atol=1e-6)
    with pytest.raises(ValueError): exposure_gain(float('nan'))
    with pytest.raises(ValueError): exposure_gain(4.01)


def test_gain_applied_before_gamma_and_preserves_headroom():
    pixels=np.array([[[3277,3277,3277],[49151,49151,49151]]],dtype=np.uint16)
    prepared=PreparedImage(pixels,2.,(2,1),True)
    zero=np.array(prepared.render(0))
    plus=np.array(prepared.render(1))
    minus=np.array(prepared.render(-1))
    expected=round(float(linear_to_srgb(.05*2*2))*255)
    assert abs(int(plus[0,0,0])-expected)<=1
    assert minus[0,0,0]<zero[0,0,0]<plus[0,0,0]
    # High baseline clips for display, but cached linear data retains detail when darkened.
    assert zero[0,1,0]==255 and minus[0,1,0]<255
    assert pixels[0,1,0]==49151


def test_jpeg_zero_ev_and_export_metadata(tmp_path):
    source=tmp_path/'source.jpg'
    Image.new('RGB',(100,60),(90,110,130)).save(source,quality=100,subsampling=0)
    prepared=prepare_image(source,read_metadata(source))
    with Image.open(source) as original:
        assert np.array_equal(np.asarray(prepared.render(0)),np.asarray(original))
    options=Options(load_profiles()[0],output_dir=tmp_path/'out',exposure_ev=.37,max_edge=80)
    output=convert(source,options)
    record=json.loads(output.with_suffix('.jpg.json').read_text())
    assert record['exposure_ev']==.37
    assert Image.open(output).size==(80,48)
    with Image.open(output) as bright,Image.open(source) as original:
        assert np.asarray(bright).mean()>np.asarray(original).mean()
    assert 'ExposureBiasValue' not in read_metadata(output)  # editing EV is not capture EXIF


def test_control_precision_keyboard_drag_reset():
    app=QApplication.instance() or QApplication([])
    control=ExposureControl();control.resize(330,120);control.show();app.processEvents()
    QTest.keyClick(control.spin,Qt.Key.Key_Up)
    assert control.value()==.1
    QTest.keyClick(control.spin,Qt.Key.Key_Up,Qt.KeyboardModifier.ShiftModifier)
    assert control.value()==1.1
    QTest.keyClick(control.spin,Qt.Key.Key_Down,Qt.KeyboardModifier.AltModifier)
    assert control.value()==1.09
    control.set_value(0)
    QTest.mouseDClick(control.slider,Qt.MouseButton.LeftButton,pos=QPoint(160,8))
    assert control.value()==0
    QTest.keyClick(control.slider,Qt.Key.Key_Left,Qt.KeyboardModifier.AltModifier)
    assert control.value()==-.01
    control.set_value(0)
    center=QPoint(control.slider.width()//2,control.slider.height()//2)
    QTest.mousePress(control.slider,Qt.MouseButton.LeftButton,pos=center)
    QTest.mouseMove(control.slider,center+QPoint(30,0))
    QTest.mouseRelease(control.slider,Qt.MouseButton.LeftButton,pos=center+QPoint(30,0))
    normal=control.value();assert normal>0
    control.set_value(0)
    QTest.mousePress(control.slider,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.AltModifier,pos=center)
    # Explicit Qt modifier during move via a QMouseEvent.
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent,QPointF
    position=QPointF(center+QPoint(30,0))
    app.sendEvent(control.slider,QMouseEvent(QEvent.Type.MouseMove,position,position,Qt.MouseButton.NoButton,
                  Qt.MouseButton.LeftButton,Qt.KeyboardModifier.AltModifier))
    QTest.mouseRelease(control.slider,Qt.MouseButton.LeftButton,pos=center+QPoint(30,0))
    assert abs(control.value()-normal*.1)<=.01
    control.close()


def test_dialog_live_latest_preview_comparison_and_speed_edit(tmp_path):
    app=QApplication.instance() or QApplication([])
    source=tmp_path/'photo.jpg';Image.new('RGB',(600,400),(90,100,110)).save(source)
    dialog=ExposureDialog(source,selection_count=3);dialog.show()
    wait(app,lambda:dialog.rendered_token==dialog.token and dialog.viewer.pixmap_data is not None)
    initial=dialog.viewer.pixmap_data.toImage().pixelColor(0,0).red()
    for ev in [.1,.2,.3,.4,.5]: dialog.control.set_value(ev)
    wait(app,lambda:dialog.rendered_token==dialog.token)
    assert dialog.viewer.pixmap_data.toImage().pixelColor(0,0).red()>initial
    dialog.compare.setChecked(True)
    wait(app,lambda:dialog.rendered_token==dialog.token)
    assert dialog.viewer.pixmap_data.toImage().pixelColor(0,0).red()==initial
    assert dialog.control.value()==.5
    dialog.compare.setChecked(False)
    dialog.viewer.setFocus()
    QTest.keyPress(dialog.viewer,Qt.Key.Key_Q)
    QTest.keyClick(dialog.viewer,Qt.Key.Key_Right)
    assert dialog.control.value()==.6
    QTest.keyClick(dialog.viewer,Qt.Key.Key_Space)
    assert dialog.control.value()==0
    QTest.keyRelease(dialog.viewer,Qt.Key.Key_Q)
    dialog.reject()
    wait(app,lambda:not dialog.isVisible())


def test_batch_relative_exposure_and_clamping(tmp_path):
    app=QApplication.instance() or QApplication([])
    settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat)
    window=MainWindow(settings=settings,log_dir=tmp_path/'logs')
    for name in ['a.jpg','b.jpg']:
        p=tmp_path/name;Image.new('RGB',(50,40),'gray').save(p);window.add_job(p,'Test','')
    window.jobs[0].exposure_ev=1.;window.jobs[1].exposure_ev=-.5
    window.apply_exposure([0,1],1.3,mode='relative',initial=1.)
    assert [j.exposure_ev for j in window.jobs]==[1.3,-.2]
    window.apply_exposure([0,1],4.,mode='relative',initial=0.)
    assert [j.exposure_ev for j in window.jobs]==[4.,3.8]
    assert '限制' in window.status.text()
    window.close()


def test_accept_during_render_commits_typed_value(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    source=tmp_path/'photo.jpg';Image.new('RGB',(80,60),'gray').save(source)
    dialog=ExposureDialog(source);dialog.show()
    wait(app,lambda:dialog.rendered_token==dialog.token and dialog.viewer.pixmap_data is not None)
    original=PreparedImage.render
    def slow_render(self,ev=0.):
        time.sleep(.08)
        return original(self,ev)
    monkeypatch.setattr(PreparedImage,'render',slow_render)
    dialog.control.set_value(.2);dialog.render_latest()
    assert dialog.render_worker.isRunning()
    dialog.control.spin.lineEdit().setText('+0.37 EV')
    dialog.accept()
    wait(app,lambda:not dialog.isVisible())
    assert dialog.result()==QDialog.DialogCode.Accepted
    assert dialog.control.value()==.37
    assert not dialog.render_worker.isRunning()


def test_render_failure_does_not_loop_and_can_retry(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    source=tmp_path/'photo.jpg';Image.new('RGB',(80,60),'gray').save(source)
    dialog=ExposureDialog(source);dialog.show()
    wait(app,lambda:dialog.rendered_token==dialog.token and dialog.viewer.pixmap_data is not None)
    original=PreparedImage.render
    def fail(self,ev=0.): raise RuntimeError('test failure')
    monkeypatch.setattr(PreparedImage,'render',fail)
    dialog.control.set_value(.2)
    wait(app,lambda:'test failure' in dialog.status.text() and not dialog.render_worker.isRunning())
    assert dialog.rendered_token==dialog.token
    monkeypatch.setattr(PreparedImage,'render',original)
    dialog.control.set_value(.3)
    wait(app,lambda:dialog.rendered_token==dialog.token and not dialog.render_worker.isRunning())
    dialog.reject()
    wait(app,lambda:not dialog.isVisible())


def test_gui_batch_exports_each_job_exposure(tmp_path):
    app=QApplication.instance() or QApplication([])
    settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat)
    window=MainWindow(settings=settings,log_dir=tmp_path/'logs')
    for name in ['dark.jpg','bright.jpg']:
        source=tmp_path/name;Image.new('RGB',(120,80),(90,110,130)).save(source)
        window.add_job(source,'Test','')
    window.destination.setCurrentIndex(1);window.folder.setText(str(tmp_path/'output'))
    window.apply_exposure([0],-.5);window.apply_exposure([1],.5)
    window.start()
    wait(app,lambda:not window.is_busy())
    assert [job.state for job in window.jobs]==['已完成','已完成']
    means=[]
    for job in window.jobs:
        record=json.loads(job.output.with_suffix('.jpg.json').read_text())
        assert record['exposure_ev']==job.exposure_ev
        with Image.open(job.output) as image: means.append(np.asarray(image).mean())
    assert means[0]<110<means[1]
    window.close()


def test_cancel_during_first_preview_waits_for_loader(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    source=tmp_path/'photo.jpg';Image.new('RGB',(80,60),'gray').save(source)
    import raw2leica.exposure as editor
    original=editor.prepare_image
    def slow_prepare(*args,**kwargs):
        time.sleep(.08)
        return original(*args,**kwargs)
    monkeypatch.setattr(editor,'prepare_image',slow_prepare)
    dialog=ExposureDialog(source);dialog.show();dialog.reject()
    assert dialog.closing
    wait(app,lambda:not dialog.isVisible())
    assert not dialog.loader.isRunning()
    assert dialog.result()==QDialog.DialogCode.Rejected
