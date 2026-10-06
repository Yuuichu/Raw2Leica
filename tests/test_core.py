from pathlib import Path
from dataclasses import replace
import threading

import pytest
from PIL import Image
from raw2leica.core import (Options, Cancelled, convert, compatibility_test, discover,
                            load_profiles, read_metadata, exiftool)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / '源照片.jpg'
    image = Image.new('RGB', (60, 40), '#b26135')
    image.save(path, quality=98)
    exiftool('-overwrite_original', '-Make=SONY', '-Model=ILCE-7RM6', '-Software=Sony Software',
             '-DateTimeOriginal=2026:10:04 12:57:07', '-OffsetTimeOriginal=+08:00',
             '-ExposureTime=1/60', '-FNumber=4', '-ISO=1600', '-FocalLength=30',
             '-LensMake=SONY', '-LensModel=FE 20-70mm F4 G',
             '-GPSLatitude=31.2', '-GPSLatitudeRef=N', '-GPSLongitude=121.5',
             '-GPSLongitudeRef=E', '-Orientation#=6', str(path))
    return path


@pytest.fixture
def options(tmp_path):
    return Options(load_profiles()[0], output_dir=tmp_path / '导出')


def test_identity_orientation_whitelist_and_original_unchanged(source, options):
    original = source.read_bytes()
    result = convert(source, options)
    m = read_metadata(result)
    assert m['Make'] == 'Leica Camera AG'
    assert m['Model'] == 'LEICA M11-P'
    assert (m['ImageWidth'], m['ImageHeight']) == (40, 60)
    assert m['Orientation'] == 1
    assert m['DateTimeOriginal'] == '2026:10:04 12:57:07'
    assert m['OffsetTimeOriginal'] == '+08:00'
    assert m['ISO'] == 1600
    assert m['GPSLatitude'] == 31.2
    assert 'Software' not in m and 'LensModel' not in m
    assert source.read_bytes() == original
    assert result.with_suffix('.jpg.json').exists()


def test_privacy_and_lens_modes(source, options):
    result = convert(source, replace(options, preserve_date=False, preserve_exposure=False,
                                     preserve_gps=False, lens_mode='remove'))
    m = read_metadata(result)
    assert not any(k.startswith('GPS') for k in m)
    assert all(k not in m for k in ['DateTimeOriginal', 'ISO', 'LensMake', 'LensModel', 'FocalLength'])
    result = convert(source, replace(options, lens_mode='original'))
    assert read_metadata(result)['LensModel'] == 'FE 20-70mm F4 G'


def test_output_collision_never_overwrites(source, options):
    first = convert(source, options)
    data = first.read_bytes()
    second = convert(source, options)
    assert second != first and second.name.endswith('_1.jpg')
    assert first.read_bytes() == data


def test_cancel_cleans_unfinished_outputs(source, options):
    cancelled = threading.Event()
    def progress(state, _):
        if state == '写入元数据':
            cancelled.set()
    with pytest.raises(Cancelled):
        convert(source, options, cancelled, progress)
    assert list(options.output_dir.iterdir()) == []


def test_cancel_before_read(source, options):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        convert(source, options, cancel)
    assert not options.output_dir.exists()


def test_bad_image_leaves_no_output(tmp_path, options):
    bad = tmp_path / 'bad.arw'
    bad.write_bytes(b'invalid raw')
    with pytest.raises(Exception):
        convert(bad, options)
    assert not options.output_dir.exists()


def test_discovery_deduplicates_and_recurses(source, tmp_path):
    nested = tmp_path / 'nested'
    nested.mkdir()
    raw = nested / 'image.ARW'
    raw.write_bytes(b'test')
    (nested / 'ignore.txt').write_text('test')
    assert discover([source, tmp_path]) == sorted([source, raw], key=str)


def test_compatibility_has_identical_decoded_pixels(source, options):
    outputs = compatibility_test(source, options, threading.Event(), lambda *_: None)
    assert len(outputs) == 6
    pixels = []
    for path in outputs:
        with Image.open(path) as image:
            pixels.append(image.tobytes())
    assert len(set(pixels)) == 1
    models = {read_metadata(p)['Model'] for p in outputs}
    assert models == {'LEICA M11-P', 'LEICA M11', 'LEICA Q3', 'LEICA Q3 43', 'LEICA SL3', 'LEICA M EV1'}


def test_resize_after_crop_and_orientation_updates_exif(source, options):
    # Source is 60x40 with orientation=6, so oriented image is 40x60.
    result = convert(source, replace(options, crop_box=(.25, .25, .75, .75), max_edge=15))
    m = read_metadata(result)
    assert (m['ImageWidth'], m['ImageHeight']) == (10, 15)
    assert (m['ExifImageWidth'], m['ExifImageHeight']) == (10, 15)
    import json
    report = json.loads(result.with_suffix('.jpg.json').read_text())
    assert report['crop_box'] == [.25, .25, .75, .75]
    assert report['max_edge'] == 15
    assert not report['lens_correction_applied']


def test_resize_does_not_upscale_and_preserves_small_images(source, options):
    result = convert(source, replace(options, max_edge=4000))
    m = read_metadata(result)
    assert (m['ImageWidth'], m['ImageHeight']) == (40, 60)


def test_transform_crop_keeps_correct_pixels():
    from raw2leica.core import transform_image
    image = Image.new('RGB', (100, 60), 'red')
    image.paste('blue', (50, 0, 100, 60))
    cropped = transform_image(image, crop_box=(.5, 0, 1, 1))
    assert cropped.size == (50, 60) and cropped.getpixel((0, 0)) == (0, 0, 255)
    resized = transform_image(image, max_edge=30, crop_box=(.5, 0, 1, 1))
    assert resized.size == (25, 30)
    assert image.size == (100, 60)  # never mutates the original


@pytest.mark.parametrize('box', [(0,0,0,1), (-.1,0,1,1), (0,0,1.1,1), (0,0,float('nan'),1)])
def test_invalid_crop_rejected(options, box):
    with pytest.raises(ValueError):
        replace(options, crop_box=box)


def test_compatibility_respects_crop_and_size(source, options):
    outputs = compatibility_test(source, replace(options, crop_box=(.25,.25,.75,.75), max_edge=15),
                                 threading.Event(), lambda *_: None)
    pixels = []
    for output in outputs:
        with Image.open(output) as image:
            assert image.size == (10, 15)
            pixels.append(image.tobytes())
    assert len(set(pixels)) == 1


def test_square_crop_with_odd_dimension_is_exact_square():
    from raw2leica.core import transform_image
    image = Image.new('RGB', (4000,2667), 'green')
    width_fraction = 2667/4000
    box=((1-width_fraction)/2,0,(1+width_fraction)/2,1)
    result = transform_image(image, max_edge=2048, crop_box=box)
    assert result.size == (2048,2048)
