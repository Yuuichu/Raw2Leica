import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import time

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings
from PIL import Image
from raw2leica.app import MainWindow, STYLE
from raw2leica.core import read_metadata


def wait_for(app, predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while not predicate():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError('GUI operation timed out')
        time.sleep(0.01)
    app.processEvents()


def test_gui_import_batch_failure_isolation_and_retry(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings, log_dir=tmp_path / 'logs')
    window.show()
    path = tmp_path / 'good.jpg'
    Image.new('RGB', (32, 24), 'red').save(path)
    broken = tmp_path / 'broken.arw'
    broken.write_bytes(b'bad raw')
    window.import_paths([path, broken])
    wait_for(app, lambda: window.scanner and not window.scanner.isRunning())
    assert len(window.jobs) == 2
    window.destination.setCurrentIndex(1)
    window.folder.setText(str(tmp_path / 'output'))
    window.start()
    wait_for(app, lambda: window.worker and not window.worker.isRunning())
    assert {j.state for j in window.jobs} == {'已完成', '失败'}
    completed = next(j for j in window.jobs if j.output)
    assert read_metadata(completed.output)['Model'] == window.target.currentText()
    assert window.retry.isEnabled()
    assert window.open_button.isEnabled()
    assert '1 成功' in window.status.text()
    # A repaired failed input can be retried while the completed job stays untouched.
    before = completed.output.read_bytes()
    Image.new('RGB', (32, 24), 'blue').save(tmp_path / 'fixed.jpg')
    failed = next(j for j in window.jobs if j.state == '失败')
    failed.source = tmp_path / 'fixed.jpg'
    window.start(retry=True)
    wait_for(app, lambda: not window.worker.isRunning())
    assert all(j.state == '已完成' for j in window.jobs)
    assert completed.output.read_bytes() == before
    window.close()


def test_gui_crop_size_reexport_and_settings(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings, log_dir=tmp_path / 'logs')
    source = tmp_path / 'landscape.jpg'
    Image.new('RGB', (800, 600), 'green').save(source)
    window.add_job(source, 'Test', '')
    window.destination.setCurrentIndex(1)
    window.folder.setText(str(tmp_path / 'out'))
    window.export_size.setCurrentIndex(window.export_size.findData(-1))
    window.custom_edge.setValue(320)
    window.apply_crop([0], (.125, 0, .875, 1))  # 600x600 square
    window.start()
    wait_for(app, lambda: window.worker and not window.worker.isRunning())
    first = window.jobs[0].output
    assert Image.open(first).size == (320,320)
    window.table.selectRow(0)
    window.start(reexport=True)
    wait_for(app, lambda: not window.worker.isRunning())
    assert window.jobs[0].output != first and first.exists()
    window.save_settings()
    window.close()
    wait_for(app, lambda: not window.isVisible())
    reopened = MainWindow(settings=settings, log_dir=tmp_path / 'logs')
    assert reopened.export_size.currentData() == -1
    assert reopened.custom_edge.value() == 320
    reopened.close()


def test_interactive_crop_ratio_resize_and_move():
    from raw2leica.crop import CropCanvas
    from PySide6.QtGui import QPixmap, QColor
    from PySide6.QtCore import Qt, QPoint
    from PySide6.QtTest import QTest
    import io
    app = QApplication.instance() or QApplication([])
    canvas = CropCanvas()
    canvas.resize(640,480)
    buffer=io.BytesIO()
    Image.new('RGB',(640,480),'gray').save(buffer,format='PNG')
    canvas.set_image(buffer.getvalue(),(640,480))
    canvas.set_ratio(1)
    canvas.show()
    app.processEvents()
    crop=canvas.selection_rect()
    QTest.mousePress(canvas,Qt.MouseButton.LeftButton,pos=crop.bottomRight().toPoint())
    QTest.mouseMove(canvas,QPoint(390,310))
    QTest.mouseRelease(canvas,Qt.MouseButton.LeftButton,pos=QPoint(390,310))
    x0,y0,x1,y1=canvas.box
    assert abs((x1-x0)*640-(y1-y0)*480)<.01
    assert x1-x0 < .75
    center=canvas.selection_rect().center().toPoint()
    QTest.mousePress(canvas,Qt.MouseButton.LeftButton,pos=center)
    QTest.mouseMove(canvas,QPoint(630,470))
    QTest.mouseRelease(canvas,Qt.MouseButton.LeftButton,pos=QPoint(630,470))
    assert all(0<=v<=1 for v in canvas.box)
    canvas.set_ratio(None)
    canvas.reset()
    assert canvas.box==(0,0,1,1)
    canvas.close()


def test_crop_dialog_load_apply_and_reset(tmp_path):
    from raw2leica.crop import CropDialog
    from PySide6.QtWidgets import QDialogButtonBox
    app = QApplication.instance() or QApplication([])
    source = tmp_path / 'crop-preview.jpg'
    Image.new('RGB', (800,600), 'orange').save(source)
    dialog = CropDialog(source, selection_count=2)
    dialog.show()
    wait_for(app, lambda: not dialog.worker.isRunning())
    assert dialog.canvas.source_size == (800,600)
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    dialog.ratio.setCurrentIndex(dialog.ratio.findText('1:1'))
    box = dialog.crop_box()
    assert abs((box[2]-box[0])*800-(box[3]-box[1])*600)<.01
    dialog.reset()
    assert dialog.crop_box() is None
    dialog.reject()
