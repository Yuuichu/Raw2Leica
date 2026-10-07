"""Conversion engine. No UI, no modification of source files."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import math
import json
import os
import shutil
import tempfile
import threading

from PIL import Image, ImageCms
from .imaging import prepare_image, exposure_gain, BasicAdjustments
from .metadata import (Profile, load_profiles, DATE_TAGS, EXPOSURE_TAGS, LENS_TAGS,
                       find_exiftool, exiftool, read_metadata, copy_tags,
                       write_metadata, verify, reserve_output)

RAW_EXTENSIONS = {".arw", ".sr2", ".srf", ".cr2", ".cr3", ".crw", ".nef", ".nrw",
                  ".raf", ".rw2", ".rwl", ".orf", ".pef", ".ptx", ".3fr", ".fff",
                  ".iiq", ".srw", ".dng", ".raw", ".mos", ".mrw", ".kdc", ".dcr"}
JPEG_EXTENSIONS = {".jpg", ".jpeg"}
SUPPORTED_EXTENSIONS = RAW_EXTENSIONS | JPEG_EXTENSIONS
@dataclass(frozen=True)
class Options:
    profile: Profile
    quality: int = 95
    output_dir: Path | None = None
    preserve_date: bool = True
    preserve_exposure: bool = True
    preserve_gps: bool = True
    lens_mode: str = "compatible"  # compatible / original / remove
    exposure_ev: float = 0.
    adjustments: BasicAdjustments = BasicAdjustments()
    max_edge: int | None = None  # maximum export edge; never upscale
    crop_box: tuple[float, float, float, float] | None = None  # normalized x0,y0,x1,y1, after orientation

    def __post_init__(self):
        exposure_gain(self.exposure_ev)
        if not 1 <= self.quality <= 100:
            raise ValueError("JPEG 质量必须为 1–100")
        if self.lens_mode not in {"compatible", "original", "remove"}:
            raise ValueError("未知镜头元数据模式")
        if self.max_edge is not None and (not isinstance(self.max_edge, int) or self.max_edge < 1):
            raise ValueError("导出长边必须为正整数")
        if self.crop_box is not None:
            if len(self.crop_box) != 4 or not all(math.isfinite(v) for v in self.crop_box):
                raise ValueError("无效裁剪范围")
            x0, y0, x1, y1 = self.crop_box
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise ValueError("裁剪范围必须位于图像内")


class Cancelled(Exception):
    pass


def check_cancel(cancel: threading.Event):
    if cancel.is_set():
        raise Cancelled("已取消")


def discover(inputs: list[Path], cancel: threading.Event | None = None) -> list[Path]:
    """Recursive, deterministic discovery without following directory symlinks."""
    found: set[Path] = set()
    for item in inputs:
        item = item.expanduser().resolve()
        if item.is_dir():
            for root, dirs, files in os.walk(item, followlinks=False):
                if cancel:
                    check_cancel(cancel)
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for name in sorted(files):
                    path = Path(root) / name
                    if path.suffix.lower() in SUPPORTED_EXTENSIONS and not name.startswith("."):
                        found.add(path.resolve())
        elif item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS:
            found.add(item)
    return sorted(found, key=str)


def _orient(image: Image.Image, orientation: int) -> Image.Image:
    transforms = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
                  4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
                  6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
                  8: Image.Transpose.ROTATE_90}
    return image.transpose(transforms[orientation]) if orientation in transforms else image


def develop(source: Path, metadata: dict, exposure_ev: float = 0., adjustments=None) -> Image.Image:
    prepared = prepare_image(source, metadata)
    return prepared.render(exposure_ev, adjustments)


def crop_bounds(size: tuple[int, int], box: tuple[float, float, float, float]):
    """Round extents once so locked square crops remain exactly square in pixels."""
    w, h = size
    x0, y0, x1, y1 = box
    cw = min(w, max(1, round((x1 - x0) * w)))
    ch = min(h, max(1, round((y1 - y0) * h)))
    left = min(w - cw, max(0, round(x0 * w)))
    top = min(h - ch, max(0, round(y0 * h)))
    return left, top, left + cw, top + ch


def transform_image(image: Image.Image, max_edge: int | None = None,
                    crop_box: tuple[float, float, float, float] | None = None) -> Image.Image:
    """Crop in oriented coordinates, then downsample. Caller owns the returned image."""
    if crop_box is not None:
        image = image.crop(crop_bounds(image.size, crop_box))
    else:
        image = image.copy()
    if max_edge is not None and max(image.size) > max_edge:
        scale = max_edge / max(image.size)
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        resized = image.resize(size, Image.Resampling.LANCZOS)
        image.close()
        image = resized
    return image


def encode(source: Path, metadata: dict, destination: Path, quality: int,
           *, max_edge: int | None = None, crop_box=None, exposure_ev=0., adjustments=None):
    original = develop(source, metadata, exposure_ev, adjustments)
    try:
        image = transform_image(original, max_edge, crop_box)
    finally:
        original.close()
    try:
        size = image.size
        icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        image.save(destination, format="JPEG", quality=quality, subsampling=0, icc_profile=icc)
        return size
    finally:
        image.close()


def export_encoded(source: Path, encoded: Path, source_metadata: dict, options: Options,
                   size: tuple[int, int], cancel: threading.Event, progress: Callable[[str, int], None],
                   stem: str | None = None) -> Path:
    folder = options.output_dir or source.parent
    output = reserve_output(folder, stem or f"{source.stem}_Leica")
    temp: Path | None = None
    report_temp: Path | None = None
    committed = False
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", prefix=".raw2leica-", dir=folder, delete=False) as f:
            temp = Path(f.name)
        shutil.copyfile(encoded, temp)
        check_cancel(cancel)
        progress("写入元数据", 75)
        write_metadata(source, temp, options, size)
        check_cancel(cancel)
        progress("校验输出", 90)
        actual = verify(temp, source_metadata, options, size)
        report = {"source": str(source), "source_camera": source_metadata.get("Model", "未知"),
                  "output": str(output), "target": options.profile.model, "quality": options.quality,
                  "dimensions": size, "exposure_ev": options.exposure_ev, "basic_adjustments": vars(options.adjustments), "develop_pipeline": "linear-srgb-v2", "max_edge": options.max_edge, "crop_box": options.crop_box,
                  "lens_correction_applied": False, "metadata_verified": True, "fotos_verified": False,
                  "metadata": {k: v for k, v in actual.items() if k != "SourceFile"}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", prefix=".raw2leica-", dir=folder,
                                         encoding="utf-8", delete=False) as f:
            report_temp = Path(f.name)
            json.dump(report, f, ensure_ascii=False, indent=2)
        check_cancel(cancel)
        os.replace(report_temp, output.with_suffix(".jpg.json"))
        os.replace(temp, output)
        committed = True
        progress("完成 · EXIF 已校验", 100)
        return output
    finally:
        if temp:
            temp.unlink(missing_ok=True)
        if report_temp:
            report_temp.unlink(missing_ok=True)
        if not committed:
            output.unlink(missing_ok=True)
            output.with_suffix(".jpg.json").unlink(missing_ok=True)


def convert(source: Path, options: Options, cancel: threading.Event | None = None,
            progress: Callable[[str, int], None] | None = None) -> Path:
    source = source.resolve()
    cancel = cancel or threading.Event()
    progress = progress or (lambda *_: None)
    check_cancel(cancel)
    progress("读取照片", 5)
    metadata = read_metadata(source)
    progress("开发 RAW" if source.suffix.lower() not in JPEG_EXTENSIONS else "处理 JPEG", 20)
    with tempfile.TemporaryDirectory(prefix="raw2leica-") as folder:
        encoded = Path(folder) / "base.jpg"
        size = encode(source, metadata, encoded, options.quality,
                      max_edge=options.max_edge, crop_box=options.crop_box, exposure_ev=options.exposure_ev, adjustments=options.adjustments)
        check_cancel(cancel)
        progress("写入 JPEG", 65)
        return export_encoded(source, encoded, metadata, options, size, cancel, progress)


def compatibility_test(source: Path, options: Options, cancel: threading.Event,
                       progress: Callable[[str, int], None]) -> list[Path]:
    """Encode once, change only identity fields: JPEG compressed pixels remain identical."""
    from dataclasses import replace
    check_cancel(cancel)
    metadata = read_metadata(source)
    progress("生成测试基图", 10)
    outputs = []
    with tempfile.TemporaryDirectory(prefix="raw2leica-test-") as folder:
        encoded = Path(folder) / "base.jpg"
        size = encode(source, metadata, encoded, options.quality,
                      max_edge=options.max_edge, crop_box=options.crop_box, exposure_ev=options.exposure_ev, adjustments=options.adjustments)
        profiles = [p for p in load_profiles() if p.id in {"m11p", "m11", "q3", "q343", "sl3", "mev1"}]
        for index, profile in enumerate(profiles):
            check_cancel(cancel)
            progress(f"生成 {profile.display_name}", int(index / len(profiles) * 100))
            outputs.append(export_encoded(source, encoded, metadata, replace(options, profile=profile),
                                          size, cancel, lambda *_: None, f"{source.stem}_{profile.id}"))
    progress("完成 · 6 种身份", 100)
    return outputs
