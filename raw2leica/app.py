from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import io
import json
import sys
import threading
from datetime import datetime

from PIL import Image, ImageOps
from PySide6.QtCore import Qt, QThread, Signal, QSettings, QStandardPaths, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPixmap
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QGridLayout, QComboBox, QSlider, QCheckBox, QFileDialog, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar, QMessageBox, QAbstractItemView,
    QPlainTextEdit, QDialog, QScrollArea, QLayout, QSpinBox)

from .crop import CropDialog
from .exposure import ExposureDialog
from . import __version__
from .core import (Options, SUPPORTED_EXTENSIONS, Cancelled, load_profiles, read_metadata,
                   discover, convert, compatibility_test, find_exiftool, exiftool)


@dataclass
class Job:
    source: Path
    camera: str = "读取中…"
    state: str = "等待"
    output: Path | None = None
    error: str = ""
    target: str = ""
    crop_box: tuple[float, float, float, float] | None = None
    exposure_ev: float = 0.


class ScanWorker(QThread):
    found = Signal(object, str, str)
    notice = Signal(str)

    def __init__(self, paths):
        super().__init__()
        self.paths = paths
        self.cancel = threading.Event()

    def run(self):
        try:
            paths = discover(self.paths, self.cancel)
            if not paths:
                self.notice.emit("未找到可导入的 RAW 或 JPEG 文件。")
            for path in paths:
                if self.cancel.is_set():
                    break
                try:
                    m = read_metadata(path)
                    self.found.emit(path, str(m.get("Model", m.get("Make", "未知相机"))), "")
                except Exception as error:
                    self.found.emit(path, "无法读取", str(error))
        except Cancelled:
            pass
        except Exception as error:
            self.notice.emit(str(error))


class BatchWorker(QThread):
    update = Signal(int, str, int)
    result = Signal(int, str, object, str)
    summary = Signal(int, int, int)

    def __init__(self, jobs, options, test=False):
        super().__init__()
        self.jobs = jobs
        self.options = options
        self.test = test
        self.cancel = threading.Event()

    def run(self):
        done = failed = cancelled = 0
        for row, source, crop_box, exposure_ev in self.jobs:
            if self.cancel.is_set():
                self.result.emit(row, "已取消", [], "")
                cancelled += 1
                continue
            try:
                callback = lambda state, value, r=row: self.update.emit(r, state, value)
                options = replace(self.options, crop_box=crop_box, exposure_ev=exposure_ev)
                if self.test:
                    outputs = compatibility_test(source, options, self.cancel, callback)
                else:
                    outputs = [convert(source, options, self.cancel, callback)]
                self.result.emit(row, "已完成", outputs, "")
                done += 1
            except Cancelled:
                self.result.emit(row, "已取消", [], "")
                cancelled += 1
            except Exception as error:
                self.result.emit(row, "失败", [], str(error))
                failed += 1
        self.summary.emit(done, failed, cancelled)


class PreviewWorker(QThread):
    ready = Signal(str, bytes)

    def __init__(self, path):
        super().__init__()
        self.path = path

    def run(self):
        try:
            if self.path.suffix.lower() in {".jpg", ".jpeg"}:
                with Image.open(self.path) as original:
                    original.draft("RGB", (900, 600))
                    image = ImageOps.exif_transpose(original).convert("RGB")
            else:
                data = exiftool("-b", "-PreviewImage", str(self.path))
                if not data:
                    data = exiftool("-b", "-JpgFromRaw", str(self.path))
                if not data:
                    data = exiftool("-b", "-ThumbnailImage", str(self.path))
                image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
            image.thumbnail((900, 600))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            self.ready.emit(str(self.path), buffer.getvalue())
        except Exception:
            self.ready.emit(str(self.path), b"")


class DropZone(QFrame):
    dropped = Signal(object)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setObjectName("dropZone")

    def dragEnterEvent(self, event):
        if self.isEnabled() and event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setProperty("dragging", True)
            self.style().polish(self)

    def dragLeaveEvent(self, event):
        self.setProperty("dragging", False)
        self.style().polish(self)

    def dropEvent(self, event):
        self.setProperty("dragging", False)
        self.style().polish(self)
        self.dropped.emit([Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()


def label(text, name="", word_wrap=False):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(word_wrap)
    return widget


def button(text, callback, name=""):
    widget = QPushButton(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.clicked.connect(callback)
    if name:
        widget.setObjectName(name)
    return widget


def card():
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(24, 22, 24, 22)
    layout.setSpacing(14)
    return frame, layout


STYLE = """
QMainWindow, QDialog { background: #f4f3ef; }
QWidget { color: #242724; font-family: 'PingFang SC', 'Helvetica Neue', sans-serif; font-size: 13px; }
QLabel { background: transparent; }
QFrame#sidebar { background: #222723; border: none; }
QFrame#sidebar QLabel { color: #efefe8; }
QLabel#brand { color: white; font-size: 17px; font-weight: 700; letter-spacing: 1px; }
QLabel#logo { background: #df3b2e; color: white; border-radius: 18px; font-weight: 700; font-size: 19px; }
QLabel#sideMuted { color: #a3aba4; font-size: 11px; }
QLabel#sideSection { color: #9ca79e; font-size: 10px; letter-spacing: 2px; }
QLabel#navActive { background: #3b443c; color: white; border-radius: 7px; padding: 12px; font-weight: 600; }
QLabel#navDisabled { color: #89958b; padding: 12px; }
QLabel#title { font-size: 29px; font-weight: 650; letter-spacing: -1px; }
QLabel#eyebrow { color: #727b72; font-size: 10px; letter-spacing: 2px; font-weight: 600; }
QLabel#subtitle, QLabel#muted { color: #7e857c; }
QLabel#section { font-size: 15px; font-weight: 600; }
QLabel#step { color: #7e857c; font-size: 11px; }
QLabel#badge { background: #e4e9df; color: #51604c; padding: 6px 10px; border-radius: 10px; font-size: 10px; }
QFrame#card { background: #fffefa; border: 1px solid #e4e4db; border-radius: 12px; }
QFrame#dropZone { background: #f7f8f3; border: 1px dashed #bac4b6; border-radius: 9px; }
QFrame#dropZone[dragging=true] { border: 2px solid #687e5d; background: #e9efe4; }
QPushButton { background: #fffefa; border: 1px solid #d9ddd2; border-radius: 6px; padding: 8px 13px; font-weight: 500; }
QPushButton:hover { background: #edf0e7; border-color: #aab7a0; }
QPushButton:disabled { color: #b6bbb2; background: #f3f3ee; border-color: #e4e5de; }
QPushButton#primary { background: #d84131; color: white; border: none; padding: 12px 24px; font-weight: 600; }
QPushButton#primary:hover { background: #c3382b; }
QPushButton#primary:disabled { background: #dbb8af; color: #fffefa; }
QPushButton#quiet { border: none; background: transparent; color: #7e857c; padding: 6px; font-size: 11px; }
QComboBox, QLineEdit { background: #fffefa; border: 1px solid #d9ddd2; padding: 9px 10px; border-radius: 6px; min-height: 18px; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox::down-arrow { width: 12px; height: 8px; image: url(CHEVRON_ASSET); }
QScrollArea#settingsScroll { background: transparent; }
QComboBox QAbstractItemView { background: #fffefa; selection-background-color: #e4eadc; color: #242724; }
QLineEdit:read-only { color: #7e857c; }
QCheckBox { spacing: 9px; }
QCheckBox::indicator { width: 15px; height: 15px; border-radius: 3px; border: 1px solid #b7c0af; background: white; }
QCheckBox::indicator:checked { background: #697d5b; border: 1px solid #697d5b; image: url(CHECK_ASSET); }
QSlider::groove:horizontal { height: 4px; background: #e5e7dd; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #718365; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; margin: -5px 0; border-radius: 7px; background: #5b7050; }
QTableWidget { background: transparent; border: none; gridline-color: #efefe7; selection-background-color: #edf1e8; selection-color: #283525; outline: none; }
QHeaderView::section { background: #f6f6f0; border: none; border-bottom: 1px solid #e4e6db; padding: 10px 8px; color: #808778; font-size: 11px; }
QTableWidget::item { border-bottom: 1px solid #efefe7; padding: 7px; }
QProgressBar { height: 6px; border: none; border-radius: 3px; background: #e4e7dc; }
QProgressBar::chunk { background: #6e825f; border-radius: 3px; }
QPlainTextEdit { background: #fffefa; border: 1px solid #ddd; font-family: Menlo, monospace; font-size: 11px; }
QLabel#preview { background: #efefe8; border-radius: 7px; color: #92998b; }
QToolTip { background: #272c26; color: white; padding: 8px; border: none; }
"""

STYLE = STYLE.replace("CHEVRON_ASSET", str(Path(__file__).with_name("assets") / "chevron.svg")).replace("CHECK_ASSET", str(Path(__file__).with_name("assets") / "check.svg"))

class MainWindow(QMainWindow):
    def __init__(self, *, settings=None, log_dir=None):
        super().__init__()
        self.setWindowTitle(f"RAW2LEICA — Leica FOTOS Bridge v{__version__}")
        self.resize(1280, 870)
        self.setMinimumSize(1140, 780)
        self.settings = settings if settings is not None else QSettings("Raw2Leica", "Bridge")
        self.profiles = load_profiles()
        self.jobs: list[Job] = []
        self.scanner = None
        self.worker = None
        self.preview_worker = None
        self.pending_preview = None
        self.closing = False
        self.last_outputs: list[Path] = []
        self.logs: list[str] = []
        self.batch_rows = []
        self.batch_progress = {}
        self.log_dir = Path(log_dir) if log_dir is not None else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / "conversion.jsonl"
        self._build()
        self._restore_settings()
        self._update_counts()
        try:
            find_exiftool()
        except RuntimeError as error:
            self.status.setText(str(error))
            self.log(str(error))

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(198)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(22, 30, 22, 26)
        side.setSpacing(14)
        brand_row = QHBoxLayout()
        logo = label("R", "logo")
        logo.setFixedSize(36, 36)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_row.addWidget(logo)
        brand_row.addWidget(label("RAW2LEICA", "brand"))
        side.addLayout(brand_row)
        side.addWidget(label("Your files. A new identity.", "sideMuted"))
        side.addSpacing(38)
        side.addWidget(label("WORKSPACE", "sideSection"))
        side.addWidget(label("◉    照片桥接", "navActive"))
        side.addWidget(label("◇    DNG 输出 · 后续", "navDisabled"))
        side.addStretch()
        side.addWidget(label("RAW → JPEG → FOTOS", "sideMuted"))
        side.addWidget(label(f"DESKTOP  /  v{__version__}", "sideMuted"))
        outer.addWidget(sidebar)

        workspace = QVBoxLayout()
        workspace.setContentsMargins(32, 30, 32, 24)
        workspace.setSpacing(20)
        outer.addLayout(workspace, 1)
        head = QHBoxLayout()
        headings = QVBoxLayout()
        headings.setSpacing(7)
        headings.addWidget(label("LEICA FOTOS BRIDGE", "eyebrow"))
        headings.addWidget(label("让照片，多一种可能。", "title"))
        headings.addWidget(label("批量开发 RAW，写入 Leica 身份，交给 FOTOS 探索 Leica Looks。", "subtitle"))
        head.addLayout(headings)
        head.addStretch()
        badge = label("JPEG BRIDGE  /  L0", "badge")
        head.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)
        workspace.addLayout(head)
        route = QHBoxLayout()
        for text in ["01  导入照片", "→", "02  选择机型", "→", "03  导出并校验"]:
            route.addWidget(label(text, "step"))
        route.addStretch()
        workspace.addLayout(route)

        body = QHBoxLayout()
        body.setSpacing(22)
        workspace.addLayout(body, 1)
        left, left_layout = card()
        body.addWidget(left, 1)
        queue_header = QHBoxLayout()
        queue_header.addWidget(label("照片队列", "section"))
        self.count = label("0 张照片", "muted")
        queue_header.addStretch()
        queue_header.addWidget(self.count)
        left_layout.addLayout(queue_header)
        self.drop = DropZone()
        self.drop.dropped.connect(self.import_paths)
        drop_layout = QVBoxLayout(self.drop)
        drop_layout.setContentsMargins(18, 20, 18, 20)
        drop_layout.setSpacing(10)
        drop_title = label("＋   拖入 RAW、JPEG 或文件夹")
        drop_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(drop_title)
        drop_note = label("ARW · CR3 · NEF · RAF · DNG 及更多 RAW 格式", "muted")
        drop_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(drop_note)
        add_row = QHBoxLayout()
        add_row.addStretch()
        self.add_files = button("添加文件", self.choose_files)
        self.add_folder = button("添加文件夹", self.choose_folder)
        add_row.addWidget(self.add_files)
        add_row.addWidget(self.add_folder)
        add_row.addStretch()
        drop_layout.addLayout(add_row)
        left_layout.addWidget(self.drop)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["文件名", "源相机", "目标机型", "状态"])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(52)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in [(1, 135), (2, 112), (3, 125)]:
            self.table.setColumnWidth(column, width)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.table.cellDoubleClicked.connect(self.open_job)
        left_layout.addWidget(self.table, 1)
        self.empty = label("队列为空。添加照片后即可开始。", "muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self.empty)
        queue_actions = QHBoxLayout()
        self.remove = button("移除选中", self.remove_selected, "quiet")
        self.clear = button("清空队列", self.clear_queue, "quiet")
        self.retry = button("重试失败 / 取消项", lambda: self.start(retry=True), "quiet")
        queue_actions.addWidget(self.remove)
        queue_actions.addWidget(self.clear)
        queue_actions.addStretch()
        queue_actions.addWidget(self.retry)
        left_layout.addLayout(queue_actions)

        right, right_layout = card()
        right.setFixedWidth(300)
        right_layout.setSpacing(10)
        right_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        settings_scroll = QScrollArea()
        settings_scroll.setObjectName("settingsScroll")
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        settings_scroll.setFixedWidth(300)
        right.setMinimumWidth(0)
        right.setMaximumWidth(16777215)
        settings_scroll.setWidget(right)
        body.addWidget(settings_scroll)
        right_layout.addWidget(label("导出设置", "section"))
        right_layout.addWidget(label("目标 Leica 机型", "muted"))
        self.target = QComboBox()
        for profile in self.profiles:
            self.target.addItem(profile.display_name, profile.id)
        self.target.currentIndexChanged.connect(self.target_changed)
        right_layout.addWidget(self.target)
        quality_row = QHBoxLayout()
        quality_row.addWidget(label("JPEG 质量", "muted"))
        quality_row.addStretch()
        self.quality_label = label("95", "section")
        quality_row.addWidget(self.quality_label)
        right_layout.addLayout(quality_row)
        self.quality = QSlider(Qt.Orientation.Horizontal)
        self.quality.setRange(50, 100)
        self.quality.setValue(95)
        self.quality.valueChanged.connect(lambda v: self.quality_label.setText(str(v)))
        right_layout.addWidget(self.quality)
        right_layout.addWidget(label("sRGB  ·  相机白平衡", "muted"))
        right_layout.addWidget(label("导出尺寸 · 长边像素", "muted"))
        self.export_size = QComboBox()
        for title, edge in [("原尺寸", None), ("6000 px", 6000), ("4000 px", 4000),
                            ("3000 px", 3000), ("2048 px", 2048), ("自定义…", -1)]:
            self.export_size.addItem(title, edge)
        self.custom_edge = QSpinBox()
        self.custom_edge.setRange(320, 16000)
        self.custom_edge.setValue(4000)
        self.custom_edge.setSuffix(" px")
        self.export_size.currentIndexChanged.connect(self.size_changed)
        right_layout.addWidget(self.export_size)
        right_layout.addWidget(self.custom_edge)
        right_layout.addWidget(label("保持比例 · 先裁剪后缩小 · 不放大小图", "muted"))
        right_layout.addWidget(label("输出位置", "muted"))
        self.destination = QComboBox()
        self.destination.addItems(["与源文件相同", "指定文件夹"])
        self.destination.currentIndexChanged.connect(self.destination_changed)
        right_layout.addWidget(self.destination)
        folder_row = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setReadOnly(True)
        self.folder.setPlaceholderText("选择输出文件夹")
        self.browse = button("…", self.choose_destination)
        self.browse.setFixedWidth(34)
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(self.browse)
        right_layout.addLayout(folder_row)
        self.date = QCheckBox("保留拍摄日期")
        self.exposure = QCheckBox("保留曝光与焦距")
        self.gps = QCheckBox("保留 GPS")
        for widget in [self.date, self.exposure, self.gps]:
            widget.setChecked(True)
            right_layout.addWidget(widget)
        self.lens = QComboBox()
        self.lens.addItem("镜头 EXIF：兼容模式", "compatible")
        self.lens.addItem("镜头 EXIF：保留真实镜头", "original")
        self.lens.addItem("镜头 EXIF：移除镜头信息", "remove")
        self.lens.setToolTip("此选项只控制镜头元数据，不执行畸变、暗角或色差校正。")
        right_layout.addWidget(self.lens)
        right_layout.addWidget(label("仅保留/替换镜头信息，不应用镜头校正。", "muted", True))
        right_layout.addWidget(label("命名：原文件名_Leica.jpg\n重名自动编号，原文件保持不变。", "muted", True))
        right_layout.addStretch()
        self.test = button("同图多机型测试 ↗", lambda: self.start(test=True))
        self.test.setToolTip("选中一张照片，生成 M11-P / M11 / Q3 / Q3 43 / SL3 / M EV1 六份像素一致的 JPEG。")
        right_layout.addWidget(self.test)

        detail_row = QHBoxLayout()
        self.preview = label("照片预览", "preview")
        self.preview.setFixedSize(150, 88)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail_row.addWidget(self.preview)
        details = QVBoxLayout()
        self.detail_name = label("选择照片查看详情")
        self.detail_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_note = label("输出的 EXIF 会逐张校验。FOTOS 中的 Looks 可用性需在 App 中实测。", "muted", True)
        self.detail_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details.addWidget(self.detail_name)
        details.addWidget(self.detail_note)
        detail_row.addLayout(details, 1)
        edit_actions = QVBoxLayout()
        self.crop_button = button("裁剪选中照片", self.edit_crop)
        self.exposure_button = button("曝光调整", self.edit_exposure)
        edit_actions.addWidget(self.exposure_button)
        self.reexport_button = button("重新导出选中", lambda: self.start(reexport=True))
        edit_actions.addWidget(self.crop_button)
        edit_actions.addWidget(self.reexport_button)
        detail_row.addLayout(edit_actions)
        workspace.addLayout(detail_row)
        footer = QHBoxLayout()
        progress_col = QVBoxLayout()
        self.status = label("准备就绪", "muted")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        progress_col.addWidget(self.status)
        progress_col.addWidget(self.progress)
        footer.addLayout(progress_col, 1)
        footer.addSpacing(16)
        self.log_button = button("日志", self.show_logs, "quiet")
        self.open_button = button("打开输出", self.open_outputs)
        self.cancel_button = button("取消", self.cancel)
        self.convert_button = button("开始转换  →", self.start, "primary")
        for widget in [self.log_button, self.open_button, self.cancel_button, self.convert_button]:
            footer.addWidget(widget)
        workspace.addLayout(footer)
        for combo in [self.target, self.destination, self.lens, self.export_size]:
            combo.setFixedHeight(38)
        self.folder.setFixedHeight(36)
        self.browse.setFixedHeight(36)
        self.test.setFixedHeight(38)
        self.quality.setMinimumHeight(20)
        self.status.setWordWrap(True)
        self.setting_widgets = [self.target, self.quality, self.destination, self.folder, self.browse,
                                self.date, self.exposure, self.gps, self.lens, self.export_size, self.custom_edge]

    def _restore_settings(self):
        target_id = self.settings.value("profile", "m11p")
        index = self.target.findData(target_id)
        self.target.setCurrentIndex(max(0, index))
        self.quality.setValue(int(self.settings.value("quality", 95)))
        self.folder.setText(self.settings.value("folder", ""))
        self.destination.setCurrentIndex(int(self.settings.value("destination", 0)))
        for name in ["date", "exposure", "gps"]:
            getattr(self, name).setChecked(self.settings.value(name, True, type=bool))
        self.lens.setCurrentIndex(max(0, self.lens.findData(self.settings.value("lens", "compatible"))))
        size = self.settings.value("max_edge", 4000, type=int)
        self.export_size.setCurrentIndex(max(0, self.export_size.findData(None if size == 0 else size)))
        self.custom_edge.setValue(self.settings.value("custom_edge", 4000, type=int))
        self.destination_changed()
        self.size_changed()

    def save_settings(self):
        for key, value in {"profile": self.target.currentData(), "quality": self.quality.value(),
            "folder": self.folder.text(), "destination": self.destination.currentIndex(),
            "date": self.date.isChecked(), "exposure": self.exposure.isChecked(),
            "gps": self.gps.isChecked(), "lens": self.lens.currentData(),
            "max_edge": self.export_size.currentData() or 0, "custom_edge": self.custom_edge.value()}.items():
            self.settings.setValue(key, value)

    def is_busy(self):
        return bool((self.worker and self.worker.isRunning()) or (self.scanner and self.scanner.isRunning()))

    def _update_counts(self):
        busy = self.is_busy()
        self.count.setText(f"{len(self.jobs)} 张照片")
        self.empty.setVisible(not self.jobs)
        for widget in [self.add_files, self.add_folder, self.remove, self.clear, self.drop]:
            widget.setEnabled(not busy)
        for widget in self.setting_widgets:
            widget.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.convert_button.setEnabled(not busy and any(j.state != "已完成" for j in self.jobs))
        self.retry.setEnabled(not busy and any(j.state in {"失败", "已取消"} for j in self.jobs))
        self.test.setEnabled(not busy and len(self.selected_rows()) == 1)
        self.crop_button.setEnabled(not busy and bool(self.selected_rows()))
        self.exposure_button.setEnabled(not busy and bool(self.selected_rows()))
        self.reexport_button.setEnabled(not busy and bool(self.selected_rows()))
        self.open_button.setEnabled(bool(self.last_outputs))
        if not busy:
            self.destination_changed()
            self.size_changed()

    def size_changed(self, *_):
        self.custom_edge.setVisible(self.export_size.currentData() == -1)

    def destination_changed(self, *_):
        enabled = self.destination.currentIndex() == 1 and not self.is_busy()
        self.folder.setVisible(self.destination.currentIndex() == 1)
        self.browse.setVisible(self.destination.currentIndex() == 1)
        self.folder.setEnabled(enabled)
        self.browse.setEnabled(enabled)

    def target_changed(self, *_):
        for row, job in enumerate(self.jobs):
            if job.state != "已完成":
                job.target = self.target.currentText()
                self.table.item(row, 2).setText(job.target)

    def choose_files(self):
        extensions = " ".join(f"*{e} *{e.upper()}" for e in sorted(SUPPORTED_EXTENSIONS))
        paths, _ = QFileDialog.getOpenFileNames(self, "添加照片", "", f"RAW 与 JPEG ({extensions});;所有文件 (*)")
        if paths:
            self.import_paths([Path(p) for p in paths])

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "添加文件夹（递归导入）")
        if folder:
            self.import_paths([Path(folder)])

    def choose_destination(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出文件夹", self.folder.text())
        if folder:
            self.folder.setText(folder)
            self.destination.setCurrentIndex(1)

    def import_paths(self, paths):
        if self.is_busy():
            return
        self.scanner = ScanWorker(paths)
        self.scanner.found.connect(self.add_job)
        self.scanner.notice.connect(self.status.setText)
        self.scanner.finished.connect(self.scan_finished)
        self.status.setText("正在读取照片与源相机信息…")
        self.progress.setRange(0, 0)
        self.scanner.start()
        self._update_counts()

    def add_job(self, path, camera, error):
        if any(j.source == path for j in self.jobs):
            return
        job = Job(path, camera, "失败" if error else "等待", error=error, target=self.target.currentText())
        self.jobs.append(job)
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, value in enumerate([path.name, camera, job.target, job.state]):
            item = QTableWidgetItem(value)
            item.setToolTip(str(path) if col == 0 else error or value)
            self.table.setItem(row, col, item)
        self.count.setText(f"{len(self.jobs)} 张照片")
        self.empty.hide()
        if error:
            self.log(f"读取失败 {path}: {error}")

    def scan_finished(self):
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status.setText(f"已导入 {len(self.jobs)} 张照片。请选择目标机型并开始转换。")
        self._update_counts()
        if self.closing:
            self.close()

    def selected_rows(self):
        return sorted({i.row() for i in self.table.selectionModel().selectedRows()})

    def remove_selected(self):
        for row in reversed(self.selected_rows()):
            self.jobs.pop(row)
            self.table.removeRow(row)
        self._update_counts()

    def clear_queue(self):
        self.table.setRowCount(0)
        self.jobs.clear()
        self._update_counts()

    def options(self):
        folder = Path(self.folder.text()).expanduser().resolve() if self.destination.currentIndex() else None
        if self.destination.currentIndex() and not self.folder.text():
            raise ValueError("请先选择输出文件夹。")
        return Options(profile=self.profiles[self.target.currentIndex()], quality=self.quality.value(),
                       output_dir=folder, preserve_date=self.date.isChecked(),
                       preserve_exposure=self.exposure.isChecked(), preserve_gps=self.gps.isChecked(),
                       lens_mode=self.lens.currentData(),
                       max_edge=self.custom_edge.value() if self.export_size.currentData() == -1 else self.export_size.currentData())

    def start(self, checked=False, *, retry=False, test=False, reexport=False):
        if self.is_busy():
            return
        rows = self.selected_rows() if (test or reexport) else [i for i, j in enumerate(self.jobs)
                    if j.state in ({"失败", "已取消"} if retry else {"等待", "失败", "已取消"})]
        if not rows or (test and len(rows) != 1):
            return
        try:
            find_exiftool()
            options = self.options()
        except Exception as error:
            QMessageBox.warning(self, "无法开始", str(error))
            return
        self.save_settings()
        self.batch_rows = rows
        self.batch_progress = {row: 0 for row in rows}
        for row in rows:
            job = self.jobs[row]
            job.state = "等待"
            job.error = ""
            job.target = "6 种机型" if test else options.profile.model
            self.table.item(row, 2).setText(job.target)
            self.table.item(row, 3).setText(job.state)
        self.worker = BatchWorker([(r, self.jobs[r].source, self.jobs[r].crop_box, self.jobs[r].exposure_ev) for r in rows], options, test)
        self.worker.update.connect(self.on_progress)
        self.worker.result.connect(self.on_result)
        self.worker.summary.connect(self.on_summary)
        self.worker.finished.connect(self.batch_finished)
        self.progress.setValue(0)
        self.worker.start()
        self._update_counts()
        self.log(f"开始{'兼容性测试' if test else '批量转换'}: {len(rows)} 张，目标 {options.profile.model}")

    def on_progress(self, row, state, value):
        self.jobs[row].state = state
        self.table.item(row, 3).setText(state)
        self.batch_progress[row] = value
        self.progress.setValue(int(sum(self.batch_progress.values()) / len(self.batch_rows)))
        self.status.setText(f"{self.jobs[row].source.name} · {state} · {value}%")

    def on_result(self, row, state, outputs, error):
        job = self.jobs[row]
        job.state, job.error = state, error
        if outputs:
            job.output = outputs[0]
            self.last_outputs = outputs
        else:
            job.output = None
        item = self.table.item(row, 3)
        item.setText("✓ EXIF 已校验" if state == "已完成" else state)
        item.setForeground(QColor("#5d7450" if state == "已完成" else "#c44737" if error else "#7c8274"))
        item.setToolTip(error or "\n".join(map(str, outputs)))
        self.batch_progress[row] = 100
        self.progress.setValue(int(sum(self.batch_progress.values()) / len(self.batch_rows)))
        self.log(f"{job.source.name} · {state}" + (f" · {error}" if error else ""), source=str(job.source),
                 outputs=list(map(str, outputs)), state=state, error=error)
        if row in self.selected_rows():
            self.show_details(row)

    def on_summary(self, done, failed, cancelled):
        self.status.setText(f"本批完成：{done} 成功 · {failed} 失败 · {cancelled} 取消")
        self.log(self.status.text())

    def batch_finished(self):
        self._update_counts()
        if self.closing:
            self.close()

    def cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel.set()
        if self.scanner and self.scanner.isRunning():
            self.scanner.cancel.set()
        self.cancel_button.setEnabled(False)
        self.status.setText("正在取消… 当前解码步骤结束后停止，已完成的输出会保留。")

    def selection_changed(self):
        rows = self.selected_rows()
        self.test.setEnabled(not self.is_busy() and len(rows) == 1)
        self.crop_button.setEnabled(not self.is_busy() and bool(rows))
        self.exposure_button.setEnabled(not self.is_busy() and bool(rows))
        self.reexport_button.setEnabled(not self.is_busy() and bool(rows))
        if not rows:
            self.detail_name.setText("选择照片查看详情")
            self.detail_note.setText("输出的 EXIF 会逐张校验。FOTOS 中的 Looks 可用性需在 App 中实测。")
            self.preview.clear()
            self.preview.setText("照片预览")
            self.pending_preview = None
            return
        row = rows[0]
        self.show_details(row)
        self.request_preview(self.jobs[row].source)

    def edit_exposure(self):
        if self.is_busy():
            return
        rows = self.selected_rows()
        if not rows:
            return
        job = self.jobs[rows[0]]
        dialog = ExposureDialog(job.source, job.exposure_ev, job.crop_box, selection_count=len(rows), parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            mode = dialog.batch_mode.currentData()
            affected = rows[:1] if mode == "single" else rows
            self.apply_exposure(affected, dialog.control.value(), mode=mode, initial=dialog.initial_value)

    def apply_exposure(self, rows, value, *, mode="absolute", initial=0.):
        limited = 0
        for row in rows:
            job = self.jobs[row]
            intended = job.exposure_ev + value - initial if mode == "relative" else value
            adjusted = round(max(-4., min(4., intended)), 2)
            limited += int(abs(adjusted - intended) > .005)
            job.exposure_ev = adjusted
            job.state, job.output, job.error = "等待", None, ""
            self.table.item(row, 3).setText(f"等待 · {adjusted:+.2f} EV")
            self.table.item(row, 3).setForeground(QColor("#7c8274"))
        self._update_counts()
        if self.selected_rows():
            self.show_details(self.selected_rows()[0])
        self.status.setText(f"已更新 {len(rows)} 张照片的曝光。" + (f"{limited} 张已限制在 ±4 EV 内。" if limited else ""))

    def edit_crop(self):
        if self.is_busy():
            return
        rows = self.selected_rows()
        if not rows:
            return
        job = self.jobs[rows[0]]
        dialog = CropDialog(job.source, job.crop_box, selection_count=len(rows), exposure_ev=job.exposure_ev, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            affected = rows if dialog.apply_all.isChecked() else rows[:1]
            self.apply_crop(affected, dialog.crop_box())

    def apply_crop(self, rows, box):
        for row in rows:
            job = self.jobs[row]
            job.crop_box = box
            job.state = "等待"
            job.output = None
            job.error = ""
            self.table.item(row, 0).setText(job.source.name + ("  · 裁剪" if box else ""))
            self.table.item(row, 3).setText("等待")
            self.table.item(row, 3).setForeground(QColor("#7c8274"))
        self._update_counts()
        if self.selected_rows():
            self.show_details(self.selected_rows()[0])
        self.status.setText(f"已更新 {len(rows)} 张照片的裁剪范围。")

    def show_details(self, row):
        job = self.jobs[row]
        self.detail_name.setText(job.source.name)
        self.detail_note.setText(job.error or (f"{job.camera} → {job.target}  ·  曝光 {job.exposure_ev:+.2f} EV\n" +
                                 (f"输出：{job.output}" if job.output else str(job.source)) +
                                 ("\n已设置裁剪范围" if job.crop_box else "")))
        self.detail_note.setToolTip(self.detail_note.text())

    def request_preview(self, path):
        self.pending_preview = path
        self.preview.clear()
        self.preview.setText("读取预览…")
        if self.preview_worker and self.preview_worker.isRunning():
            return
        self._start_preview()

    def _start_preview(self):
        if self.pending_preview is None:
            return
        self.preview_worker = PreviewWorker(self.pending_preview)
        self.preview_worker.ready.connect(self.on_preview)
        self.preview_worker.finished.connect(lambda worker=self.preview_worker: self.preview_finished(worker))
        self.preview_worker.start()

    def on_preview(self, path, data):
        if str(self.pending_preview) != path:
            return
        if data:
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            self.preview.setPixmap(pixmap.scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))
        else:
            self.preview.setText("暂无嵌入预览")

    def preview_finished(self, worker):
        if self.preview_worker is not worker or worker.isRunning():
            return
        if self.closing and not self.is_busy():
            self.close()
        elif self.pending_preview and self.preview_worker.path != self.pending_preview:
            self._start_preview()

    def open_job(self, row, *_):
        job = self.jobs[row]
        if job.output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(job.output)))

    def open_outputs(self):
        if self.last_outputs:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_outputs[0].parent)))

    def log(self, message, **fields):
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        self.logs.append(f"{timestamp}  {message}")
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"time": timestamp, "message": message, **fields}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def show_logs(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("转换日志")
        dialog.resize(850, 480)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(str(self.log_path), "muted"))
        text = QPlainTextEdit("\n".join(self.logs) or "暂无转换记录。")
        text.setReadOnly(True)
        layout.addWidget(text)
        layout.addWidget(button("打开日志目录", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log_dir)))))
        dialog.exec()

    def closeEvent(self, event):
        self.save_settings()
        if self.is_busy() or (self.preview_worker and self.preview_worker.isRunning()):
            self.closing = True
            self.cancel()
            self.status.setText("正在结束当前任务，完成后关闭窗口…")
            event.ignore()
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Raw2Leica")
    app.setOrganizationName("Raw2Leica")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setFont(QFont("PingFang SC", 12))
    window = MainWindow()
    window.show()
    if len(sys.argv) > 1:
        window.import_paths([Path(arg) for arg in sys.argv[1:]])
    sys.exit(app.exec())
