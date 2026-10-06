"""Shared, high precision exposure path for previews and final exports.

Exposure is an independently implemented linear gain, inspired by darktable's
scene-linear exposure model. No darktable/Capture One source code is copied.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import io
import math

import numpy as np
from PIL import Image, ImageCms, ImageOps
import rawpy


def srgb_to_linear(value):
    value = np.asarray(value, dtype=np.float32)
    return np.where(value <= .04045, value / 12.92, ((value + .055) / 1.055) ** 2.4)


def linear_to_srgb(value):
    value = np.clip(np.asarray(value, dtype=np.float32), 0., 1.)
    return np.where(value <= .0031308, value * 12.92, 1.055 * value ** (1/2.4) - .055)


def exposure_gain(ev: float):
    if not math.isfinite(ev) or not -4 <= ev <= 4:
        raise ValueError('曝光补偿必须在 -4.00 至 +4.00 EV 之间')
    return 2. ** ev


def exposure_lut(ev: float, base_gain=1.):
    linear = np.arange(65536, dtype=np.float32) / 65535.
    encoded = linear_to_srgb(linear * (base_gain * exposure_gain(ev)))
    return np.round(encoded * 255).astype(np.uint8)



@dataclass(frozen=True)
class BasicAdjustments:
    contrast: float = 0.
    brightness: float = 0.
    saturation: float = 0.
    highlights: float = 0.
    shadows: float = 0.
    whites: float = 0.
    blacks: float = 0.

    def __post_init__(self):
        for name, value in vars(self).items():
            if not math.isfinite(value) or not -100 <= value <= 100:
                raise ValueError(f'{name} 必须在 -100 至 +100 之间')

    def active(self):
        return any(vars(self).values())


def adjust_rgb(linear, adjustments):
    """Independent luminance-weighted tone controls; work before output clipping.

    Positive highlights recovers bright tones; positive shadows opens dark tones.
    Whites/blacks instead raise their respective endpoints. Saturation uses RGB
    chroma around luminance. Strip processing in render bounds export memory.
    """
    a = adjustments
    lum = linear @ np.array([.2126, .7152, .0722], dtype=np.float32)
    t = np.clip(lum, 0., 1.)
    shadow = (1-t)**3
    highlight = t**3
    stops = (a.brightness/100 * 1.5 * 4*t*(1-t)
             + a.shadows/100 * 2*shadow - a.highlights/100 * 2*highlight
             + a.whites/100 * highlight + a.blacks/100 * shadow)
    target = lum * np.exp2(stops)
    # Smooth contrast about middle gray; zero and true black remain fixed.
    if a.contrast:
        target = .18 * np.power(np.maximum(target, 0)/.18, np.exp2(a.contrast/100*.6))
    ratio = np.divide(target, lum, out=np.ones_like(lum), where=lum>1e-8)
    rgb = linear * ratio[...,None]
    if a.saturation:
        rgb = target[...,None] + (rgb-target[...,None]) * (1+a.saturation/100)
    return np.clip(rgb, 0., None)

def raw_base_gain(pixels):
    """Freeze a whole-image 99th-percentile reference before crop or adjustment.

    Channel histograms are accumulated in strips to bound temporary memory.
    This is a neutral baseline, not an attempt to reproduce C1's auto exposure.
    """
    white = 1
    count = pixels.shape[0] * pixels.shape[1]
    for channel in range(3):
        hist = np.zeros(8192, dtype=np.int64)
        for row in range(0, pixels.shape[0], 256):
            codes = (pixels[row:row+256,:,channel] >> 3).ravel()
            hist += np.bincount(codes, minlength=8192)
        position = int(np.searchsorted(np.cumsum(hist), max(1, math.ceil(count * .99))))
        white = max(white, position * 8)
    # Avoid extreme amplification of almost-black frames.
    return min(16., 65535. / max(white, 4096))


def orient_array(pixels, orientation):
    if orientation == 2:
        return pixels[:,::-1]
    if orientation == 3:
        return pixels[::-1,::-1]
    if orientation == 4:
        return pixels[::-1]
    if orientation == 5:
        return pixels.transpose(1,0,2)
    if orientation == 6:
        return np.rot90(pixels, -1)
    if orientation == 7:
        return pixels.transpose(1,0,2)[::-1,::-1]
    if orientation == 8:
        return np.rot90(pixels, 1)
    return pixels


@dataclass
class PreparedImage:
    pixels: np.ndarray  # uint16, linear RGB in sRGB primaries; can be a view
    base_gain: float
    source_size: tuple[int,int]
    is_raw: bool

    def render(self, ev=0., adjustments=None):
        adjustments = adjustments or BasicAdjustments()
        gain = self.base_gain * exposure_gain(ev)
        if not adjustments.active():
            return Image.fromarray(exposure_lut(ev, self.base_gain)[self.pixels])
        output = np.empty(self.pixels.shape, dtype=np.uint8)
        for row in range(0, self.pixels.shape[0], 128):
            linear = self.pixels[row:row+128].astype(np.float32) * (gain/65535.)
            output[row:row+128] = np.round(linear_to_srgb(adjust_rgb(linear, adjustments))*255).astype(np.uint8)
        return Image.fromarray(output)


def reduce_linear(pixels, max_edge):
    h,w = pixels.shape[:2]
    if max(w,h) <= max_edge:
        return np.ascontiguousarray(pixels)
    scale = max_edge/max(w,h)
    size = (max(1,round(w*scale)),max(1,round(h*scale)))
    channels = []
    for channel in range(3):
        plane = Image.fromarray(pixels[:,:,channel].astype(np.float32))
        try:
            resized = plane.resize(size, Image.Resampling.LANCZOS)
            channels.append(np.clip(np.asarray(resized),0,65535).astype(np.uint16))
            resized.close()
        finally:
            plane.close()
    return np.stack(channels,axis=-1)


def prepare_image(source: Path, metadata: dict, *, preview_edge=None):
    is_raw = source.suffix.lower() not in {'.jpg','.jpeg'}
    if not is_raw:
        with Image.open(source) as original:
            image = ImageOps.exif_transpose(original)
            profile = image.info.get('icc_profile')
            if profile:
                image = ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                                                 ImageCms.createProfile('sRGB'), outputMode='RGB')
            else:
                image = image.convert('RGB')
            source_size = image.size
            rgb = np.asarray(image)
            decode = np.round(srgb_to_linear(np.arange(256,dtype=np.float32)/255)*65535).astype(np.uint16)
            pixels = decode[rgb]
            image.close()
        gain = 1.
    else:
        with rawpy.imread(str(source)) as raw:
            sizes = raw.sizes
            pixels = raw.postprocess(use_camera_wb=True,output_color=rawpy.ColorSpace.sRGB,
                output_bps=16, gamma=(1.,1.), no_auto_bright=True, user_flip=0,
                demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD)
            gain = raw_base_gain(pixels)
            if pixels.shape[:2] == (sizes.height,sizes.width):
                x,y,w,h = sizes.crop_left_margin,sizes.crop_top_margin,sizes.crop_width,sizes.crop_height
                if w>0 and h>0 and x>=0 and y>=0 and x+w<=sizes.width and y+h<=sizes.height:
                    pixels = pixels[y:y+h,x:x+w]
            orientation = int(metadata.get('Orientation',{3:3,5:8,6:6}.get(sizes.flip,1)))
            pixels = orient_array(pixels,orientation)
            source_size = (pixels.shape[1],pixels.shape[0])
    if preview_edge is not None:
        pixels = reduce_linear(pixels,preview_edge)
    return PreparedImage(pixels,gain,source_size,is_raw)
