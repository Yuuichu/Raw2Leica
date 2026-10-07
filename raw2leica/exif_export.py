"""Metadata-only export for already rendered darktable JPEG/HEIF files."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading

from PIL import Image, ImageCms
from .heif_data import heif_image_digest
from .metadata import Profile, load_profiles, exiftool, read_metadata, write_metadata, verify


@dataclass(frozen=True)
class MetadataOptions:
    profile: Profile
    preserve_date: bool = True
    preserve_exposure: bool = True
    preserve_gps: bool = True
    lens_mode: str = "compatible"

    def __post_init__(self):
        if self.lens_mode not in {"compatible", "original", "remove"}:
            raise ValueError("未知镜头元数据模式")


def jpeg_image_digest(path: Path) -> str:
    """Hash JPEG coding segments and scans, excluding APP/COM metadata only."""
    data = path.read_bytes()
    if data[:2] != b'\xff\xd8':
        raise ValueError("输入必须为 JPEG")
    digest = hashlib.sha256(data[:2])
    pos = 2
    while pos < len(data):
        start = pos
        if data[pos] != 255:
            raise ValueError("无效 JPEG 标记")
        while pos < len(data) and data[pos] == 255:
            pos += 1
        marker = data[pos]
        pos += 1
        if marker == 0xD9:
            digest.update(data[start:pos])
            return digest.hexdigest()
        length = int.from_bytes(data[pos:pos + 2], 'big')
        if length < 2 or pos + length > len(data):
            raise ValueError("JPEG 数据不完整")
        pos += length
        if marker == 0xDA:
            # Scan ends at an unescaped, non-restart marker. Support progressive JPEG.
            scan = pos
            while True:
                scan = data.find(b'\xff', scan)
                if scan < 0 or scan + 1 >= len(data):
                    raise ValueError("JPEG 扫描数据不完整")
                following = scan + 1
                while data[following] == 255:
                    following += 1
                code = data[following]
                if code == 0 or 0xD0 <= code <= 0xD7:
                    scan = following + 1
                else:
                    break
            pos = scan
        if not (0xE0 <= marker <= 0xEF or marker == 0xFE):
            digest.update(data[start:pos])
    raise ValueError("JPEG 缺少结束标记")


def is_srgb(icc: bytes | None) -> bool:
    if not icc:
        return False
    profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
    name = ImageCms.getProfileDescription(profile).strip().lower()
    if "srgb" not in name or profile.profile.xcolor_space.strip() != "RGB":
        return False
    reference = ImageCms.createProfile("sRGB")
    for attr in ("red_colorant", "green_colorant", "blue_colorant"):
        actual, expected = getattr(profile.profile, attr), getattr(reference, attr)
        if actual is None or any(abs(a-b) > .005 for a, b in zip(actual[0], expected[0])):
            return False
    # Matching primaries alone would also accept a linear/Rec.709 transfer
    # curve mislabeled as sRGB. Compare a small RGB probe through LCMS.
    probe = Image.new("RGB", (9, 1))
    samples = [(v, v, v) for v in (16, 32, 64, 128, 192, 240)] + [(64, 128, 192), (192, 64, 128), (128, 192, 64)]
    probe.putdata(samples)
    transformed = ImageCms.profileToProfile(probe, profile, ImageCms.ImageCmsProfile(reference), outputMode="RGB")
    if any(abs(a-b) > 2 for a, b in zip(probe.tobytes(), transformed.tobytes())):
        return False
    return True


def rendered_info(path: Path) -> tuple[tuple[int, int], bytes | None]:
    # Register lazily so JPEG-only environments still run without HEIF support.
    if path.read_bytes()[:2] != b'\xff\xd8':
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError as exc:
            raise ValueError("HEIF 解码依赖缺失；请重新运行插件安装器") from exc
    with Image.open(path) as image:
        if image.format not in {"JPEG", "HEIF"}:
            raise ValueError("请选择 JPEG 或 HEIF/HEIC 导出格式")
        if image.format == "JPEG":
            image.load()
        if image.getexif().get(274, 1) != 1:
            raise ValueError("导出照片必须已完成方向变换（Orientation=1）")
        icc = image.info.get("icc_profile")
        if icc:
            profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            if profile.profile.xcolor_space.strip() != "RGB":
                raise ValueError("导出 ICC 必须与 RGB 图像一致")
        elif image.format == "JPEG":
            raise ValueError("导出 JPEG 缺少 ICC；请选择导出色彩配置文件")
        elif not image.info.get("nclx_profile"):
            raise ValueError("导出 HEIF 缺少 ICC/NCLX 色彩信息")
        return image.size, icc


def image_digest(path: Path) -> str:
    return jpeg_image_digest(path) if path.read_bytes()[:2] == b'\xff\xd8' else heif_image_digest(path)


def check_environment(output_dir: Path | None = None) -> None:
    exiftool("-ver")
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=output_dir) as probe:
            probe.write(b"probe")


def export_rendered(source: Path, rendered: Path, output_dir: Path | None,
                    options: MetadataOptions, cancel: threading.Event | None = None) -> Path:
    source, rendered = source.resolve(), rendered.resolve()
    folder = (output_dir or source.parent / "darktable_exported").expanduser().resolve()
    cancelled = cancel or threading.Event()

    def check_cancel():
        if cancelled.is_set():
            raise RuntimeError("已取消")

    check_cancel()
    if not source.is_file():
        raise ValueError("源照片不存在")
    size, icc = rendered_info(rendered)
    original_digest = image_digest(rendered)
    jpeg = rendered.read_bytes()[:2] == b"\xff\xd8"
    extension = ".jpg" if jpeg else ".heic"
    color_space = 1 if is_srgb(icc) else 65535
    file_type = "JPEG" if jpeg else read_metadata(rendered)["FileType"]
    metadata = read_metadata(source)
    check_environment(folder)
    with tempfile.TemporaryDirectory(prefix=".raw2leica-", dir=folder) as work:
        temp = Path(work) / ("image" + extension)
        shutil.copyfile(rendered, temp)
        check_cancel()
        # Start with a clean metadata slate, keeping the actual exported ICC bytes and HEIF structural colour properties.
        exiftool("-overwrite_original", "-all=", "-tagsFromFile", str(rendered),
                 "-ICC_Profile", str(temp))
        write_metadata(source, temp, options, size, color_space=color_space)
        check_cancel()
        actual = verify(temp, metadata, options, size, color_space=color_space,
                        file_type=file_type, require_icc=jpeg)
        _, output_icc = rendered_info(temp)
        if output_icc != icc or image_digest(temp) != original_digest:
            raise RuntimeError("导出修改了 ICC、色彩属性或编码图像数据")
        report_temp = Path(work) / "report.json"
        # Hard-link publication is atomic and refuses collisions. A companion lock
        # coordinates our own exporters without exposing a placeholder JPEG.
        index = 0
        while True:
            check_cancel()
            suffix = f"_{index}" if index else ""
            output = folder / f"{source.stem}_Leica{suffix}{extension}"
            report = output.with_suffix(extension + ".json")
            lock = output.with_suffix(extension + ".raw2leica-lock")
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                index += 1
                continue
            os.close(fd)
            published_report = False
            published_image = False
            try:
                if output.exists() or report.exists():
                    index += 1
                    continue
                payload = {"source": str(source), "output": str(output),
                           "target": options.profile.model, "dimensions": size,
                           "pipeline": "darktable-jpeg-metadata-v1", "metadata_verified": True,
                           "fotos_verified": False, "image_sha256": original_digest,
                           "format": "JPEG" if jpeg else "HEIF", "exif_color_space": color_space,
                           **({"jpeg_image_sha256": original_digest} if jpeg else {}),
                           "options": {"profile": options.profile.id,
                                       "preserve_date": options.preserve_date,
                                       "preserve_exposure": options.preserve_exposure,
                                       "preserve_gps": options.preserve_gps, "lens_mode": options.lens_mode},
                           "metadata": {k: v for k, v in actual.items() if k != "SourceFile"}}
                report_temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                check_cancel()
                os.link(report_temp, report)
                published_report = True
                os.link(temp, output)
                published_image = True
                return output
            except FileExistsError:
                index += 1
            finally:
                if published_report and not published_image:
                    report.unlink(missing_ok=True)
                lock.unlink(missing_ok=True)


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def process_native_export(source: Path, rendered: Path, options: MetadataOptions,
                          cancel: threading.Event | None = None) -> Path:
    """Validate privately, then replace only the image owned by a native export.

    darktable has already resolved its path variables and conflict policy. This
    endpoint must only receive that freshly rendered file, never a source photo.
    On failure discard the unverified export, keeping unrelated sidecars intact.
    """
    source = source.expanduser().resolve()
    rendered = rendered.expanduser().absolute()
    if rendered.is_symlink():
        raise ValueError("原生导出文件不能是符号链接")
    rendered = rendered.resolve()
    if not source.is_file() or not rendered.is_file():
        raise ValueError("源照片或原生导出文件不存在")
    if rendered == source or os.path.samefile(source, rendered):
        raise ValueError("原生导出路径与源照片相同；请选择其他导出路径")
    report = rendered.with_suffix(rendered.suffix + '.json')
    managed_report = False
    try:
        if report.exists():
            old = json.loads(report.read_text(encoding='utf-8'))
            managed_report = (old.get('pipeline') in {'darktable-jpeg-metadata-v1', 'darktable-native-disk-v1'}
                              and old.get('output') == str(rendered))
            if not managed_report:
                raise ValueError("原生导出路径旁已有其他 JSON 文件；请修改命名模板")
        with tempfile.TemporaryDirectory(prefix='.raw2leica-native-', dir=rendered.parent) as work:
            validated = export_rendered(source, rendered, Path(work), options, cancel)
            staged_report = validated.with_suffix(validated.suffix + '.json')
            payload = json.loads(staged_report.read_text(encoding='utf-8'))
            payload.update(output=str(rendered), pipeline='darktable-native-disk-v1')
            staged_report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            if cancel is not None and cancel.is_set():
                raise RuntimeError("已取消")
            os.replace(staged_report, report)
            managed_report = True
            os.replace(validated, rendered)
        return rendered
    except Exception:
        # Native storage created this file before invoking the blocking Lua hook.
        # Never leave it looking like a completed Leica export when validation fails.
        rendered.unlink(missing_ok=True)
        if managed_report:
            report.unlink(missing_ok=True)
        raise


def main(argv=None) -> int:
    parser = JsonArgumentParser(description="darktable 已渲染 JPEG/HEIF 的 Leica EXIF 导出")
    parser.add_argument("--profiles", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--rendered", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--native-export", action="store_true",
                        help="处理原生存储刚生成的 JPEG/HEIF，保留原生路径；失败删除该导出副本")
    parser.add_argument("--profile", default="m11p", choices=[p.id for p in load_profiles()])
    for field in ("date", "exposure", "gps"):
        parser.add_argument(f"--preserve-{field}", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--lens-mode", choices=["compatible", "original", "remove"], default="compatible")
    log_dir = Path.home() / "Library/Logs/RAW2LEICA"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        from logging.handlers import RotatingFileHandler
        logging.basicConfig(handlers=[RotatingFileHandler(log_dir / "exif-export.log", maxBytes=2_000_000,
                                                          backupCount=2, encoding="utf-8")], level=logging.INFO)
        args = parser.parse_args(argv)
        if args.profiles:
            result = {"profiles": [vars(p) for p in load_profiles()]}
        elif args.check:
            check_environment(args.output_dir)
            result = {"ready": True}
        else:
            if not args.source or not args.rendered:
                raise ValueError("需要 --source 和 --rendered")
            profile = next(p for p in load_profiles() if p.id == args.profile)
            options = MetadataOptions(profile, args.preserve_date, args.preserve_exposure,
                                      args.preserve_gps, args.lens_mode)
            if args.native_export and args.output_dir:
                raise ValueError("--native-export 使用原生导出路径，不能指定 --output-dir")
            output = (process_native_export(args.source, args.rendered, options) if args.native_export
                      else export_rendered(args.source, args.rendered, args.output_dir, options))
            result = {"output": str(output), "report": str(output.with_suffix(output.suffix + '.json')),
                      "metadata_verified": True, "fotos_verified": False}
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0
    except Exception as exc:
        logging.exception("EXIF export failed")
        error = str(exc)
        print(json.dumps({"ok": False, "error": error}, ensure_ascii=False))
        print(f"{error}\n日志：{log_dir / 'exif-export.log'}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
