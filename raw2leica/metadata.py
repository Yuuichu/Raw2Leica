"""Shared metadata rules; deliberately independent of Qt and RAW development."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import math
import os
import shutil
import subprocess
from typing import Protocol
from PIL import Image

DATE_TAGS = ["DateTimeOriginal", "CreateDate", "ModifyDate", "OffsetTime", "OffsetTimeOriginal",
             "OffsetTimeDigitized", "SubSecTime", "SubSecTimeOriginal", "SubSecTimeDigitized"]
EXPOSURE_TAGS = ["ExposureTime", "FNumber", "ISO", "ExposureProgram", "ExposureBiasValue",
                 "MeteringMode", "Flash", "FocalLength", "FocalLengthIn35mmFormat"]
LENS_TAGS = ["LensModel", "LensInfo"]


@dataclass(frozen=True)
class Profile:
    id: str
    display_name: str
    make: str
    model: str
    lens_make: str


class Options(Protocol):
    profile: Profile
    preserve_date: bool
    preserve_exposure: bool
    preserve_gps: bool
    lens_mode: str


def load_profiles() -> list[Profile]:
    profiles = [Profile(**json.loads(p.read_text())) for p in sorted(
        Path(__file__).with_name("profiles").glob("*.json"))]
    return sorted(profiles, key=lambda p: (p.id != "m11p", p.display_name))


def find_exiftool() -> str:
    override = os.environ.get("EXIFTOOL_PATH")
    if override:
        if not Path(override).is_file():
            raise RuntimeError("EXIFTOOL_PATH 指向的 ExifTool 不存在")
        return override
    candidates = [shutil.which("exiftool"),
                  "/opt/homebrew/bin/exiftool", "/usr/local/bin/exiftool",
                  str(Path(__file__).resolve().parents[1] / ".tools/exiftool/bin/exiftool")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise RuntimeError("未找到 ExifTool。macOS 请运行 brew install exiftool；Windows 请将 exiftool.exe 加入 PATH。")


def exiftool(*args: str) -> bytes:
    result = subprocess.run([find_exiftool(), *map(str, args)], capture_output=True, timeout=120)
    if result.returncode:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(message or "ExifTool 执行失败")
    return result.stdout


def read_metadata(path: Path) -> dict:
    return json.loads(exiftool("-j", "-n", "-EXIF:all", "-FileType", "-ImageWidth", "-ImageHeight", str(path)))[0]


def copy_tags(options: Options) -> list[str]:
    tags = []
    if options.preserve_date:
        tags += [f"-EXIF:{t}#" for t in DATE_TAGS]
    if options.preserve_exposure:
        tags += [f"-EXIF:{t}#" for t in EXPOSURE_TAGS]
    if options.preserve_gps:
        tags += ["-GPS:all#"]
    if options.lens_mode == "original":
        tags += [f"-EXIF:{t}#" for t in LENS_TAGS]
    return tags


def write_metadata(source: Path, output: Path, options: Options, size: tuple[int, int], *, color_space: int = 1):
    profile = options.profile
    tags = copy_tags(options)
    args = ["-overwrite_original"]
    if tags:
        args += ["-tagsFromFile", str(source), *tags]
    args += [f"-EXIF:Make={profile.make}", f"-EXIF:Model={profile.model}",
             "-EXIF:Orientation#=1", f"-EXIF:ColorSpace#={color_space}",
             f"-EXIF:ExifImageWidth={size[0]}", f"-EXIF:ExifImageHeight={size[1]}"]
    if options.lens_mode != "remove":
        args += [f"-EXIF:LensMake={profile.lens_make}"]
    exiftool(*args, str(output))


def verify(output: Path, source_metadata: dict, options: Options, size: tuple[int, int], *,
           color_space: int = 1, file_type: str = "JPEG", require_icc: bool = True) -> dict:
    actual = read_metadata(output)
    expected = {"Make": options.profile.make, "Model": options.profile.model, "Orientation": 1,
                "ColorSpace": color_space, "ImageWidth": size[0], "ImageHeight": size[1], "FileType": file_type}
    if file_type in {"HEIC", "HEIF"}:
        # HEIF coding tiles can exceed the displayed crop. Check EXIF dimensions
        # here and the decoded display dimensions below, not the tile dimensions.
        expected.pop("ImageWidth")
        expected.pop("ImageHeight")
        expected.update(ExifImageWidth=size[0], ExifImageHeight=size[1])
    if options.lens_mode != "remove":
        expected["LensMake"] = options.profile.lens_make
    selected = []
    if options.preserve_date:
        selected += DATE_TAGS
    if options.preserve_exposure:
        selected += EXPOSURE_TAGS
    if options.lens_mode == "original":
        selected += LENS_TAGS
    if options.preserve_gps:
        selected += [k for k in source_metadata if k.startswith("GPS") and k != "GPSInfo"]
    for tag in selected:
        if tag in source_metadata:
            expected[tag] = source_metadata[tag]
    def equal(key, value):
        observed = actual.get(key)
        # EXIF rational values may be represented by a different numerator and
        # denominator after copying. Accept only tiny numerical roundoff, never
        # relaxed camera identity, dimensions, orientation or discrete flags.
        if key in selected and isinstance(value, float) and isinstance(observed, (int, float)):
            return math.isclose(observed, value, rel_tol=1e-6, abs_tol=1e-10)
        if key == "LensInfo" and key in selected and isinstance(value, str) and isinstance(observed, str):
            try:
                before, after = list(map(float, value.split())), list(map(float, observed.split()))
                return len(before) == len(after) == 4 and all(
                    math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-10) for a, b in zip(before, after))
            except ValueError:
                return False
        return observed == value
    mismatches = [f"{key}: {actual.get(key)!r} ≠ {value!r}" for key, value in expected.items()
                  if not equal(key, value)]
    if not options.preserve_date:
        mismatches += [f"未清除 {t}" for t in DATE_TAGS if t in actual]
    if not options.preserve_exposure:
        mismatches += [f"未清除 {t}" for t in EXPOSURE_TAGS if t in actual]
    if not options.preserve_gps and any(t.startswith("GPS") for t in actual):
        mismatches.append("未清除 GPS")
    if options.lens_mode != "original" and any(t in actual for t in LENS_TAGS):
        mismatches.append("未清除原始镜头型号")
    if options.lens_mode == "remove" and "LensMake" in actual:
        mismatches.append("未清除 LensMake")
    all_tags = json.loads(exiftool("-j", "-G1", "-a", "-n", str(output)))[0]
    forbidden = ("MakerNotes:", "Sony:", "Canon:", "Nikon:", "FujiFilm:", "Panasonic:", "JUMBF:", "C2PA:")
    if any(k.startswith(forbidden) or k.endswith(":MakerNote") for k in all_tags):
        mismatches.append("发现厂商 MakerNote 或 C2PA 数据")
    with Image.open(output) as image:
        if image.format != "HEIF":
            image.load()
        if image.size != size or (require_icc and not image.info.get("icc_profile")):
            mismatches.append("图像尺寸或 ICC 验证失败")
    if mismatches:
        raise RuntimeError("元数据校验失败：" + "; ".join(mismatches))
    return actual


def reserve_output(folder: Path, stem: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    index = 0
    while True:
        suffix = f"_{index}" if index else ""
        path = folder / f"{stem}{suffix}.jpg"
        if path.with_suffix(".jpg.json").exists():
            index += 1
            continue
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.close(fd)
            return path
        except FileExistsError:
            index += 1
