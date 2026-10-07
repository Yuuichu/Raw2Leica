"""The plugin consumes rendered pixels, regardless of the original developer."""
from dataclasses import replace
import math
import os
import json
from pathlib import Path
import subprocess
import sys
import threading

from PIL import Image, ImageCms
import pytest

from raw2leica.exif_export import (MetadataOptions, export_rendered, jpeg_image_digest,
                                   rendered_info, process_native_export, main)
from raw2leica.metadata import load_profiles, exiftool, read_metadata


@pytest.fixture
def photos(tmp_path):
    source = tmp_path / "源 '照片 $(touch HACKED);.jpg"
    rendered = tmp_path / 'darktable 临时.jpg'
    Image.new('RGB', (90, 60), '#b26135').save(source)
    exiftool('-overwrite_original', '-Make=SONY', '-Model=Original', '-Orientation#=6',
             '-DateTimeOriginal=2026:10:04 12:57:07', '-ExposureTime=1/60', '-ISO=1600',
             '-FNumber=4', '-FocalLength=30', '-LensModel=Real lens', '-LensMake=SONY',
             '-GPSLatitude=31.2', '-GPSLatitudeRef=N', str(source))
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    Image.new('RGB', (40, 60), '#4590a1').save(rendered, quality=92, icc_profile=icc, progressive=True)
    exiftool('-overwrite_original', '-Make=Wrong', '-Model=Wrong', '-Software=darktable',
             '-XMP:GPSLatitude=32.0', '-GPSLatitude=32', '-GPSLatitudeRef=N',
             '-XMP:SerialNumber=private', '-LensModel=Wrong lens', str(rendered))
    return source, rendered, tmp_path / '输出 空间'


@pytest.mark.parametrize('profile', load_profiles(), ids=lambda p: p.id)
def test_all_identities_and_encoded_pixels(photos, profile):
    source, rendered, folder = photos
    originals = [p.read_bytes() for p in (source, rendered)]
    result = export_rendered(source, rendered, folder, MetadataOptions(profile))
    metadata = read_metadata(result)
    assert metadata['Model'] == profile.model
    assert metadata['Orientation'] == 1
    assert (metadata['ImageWidth'], metadata['ImageHeight']) == (40, 60)
    assert metadata['GPSLatitude'] == 31.2  # source rather than rendered
    assert metadata['ISO'] == 1600
    assert 'Software' not in metadata
    assert jpeg_image_digest(result) == jpeg_image_digest(rendered)
    with Image.open(result) as a, Image.open(rendered) as b:
        assert a.tobytes() == b.tobytes()
        assert a.info['icc_profile'] == b.info['icc_profile']
    assert originals == [p.read_bytes() for p in (source, rendered)]
    report = json.loads(result.with_suffix('.jpg.json').read_text())
    assert report['metadata_verified'] and not report['fotos_verified']


@pytest.mark.parametrize('lens', ['compatible', 'original', 'remove'])
@pytest.mark.parametrize('field', ['date', 'exposure', 'gps'])
def test_modes_and_disabled_fields(photos, lens, field):
    source, rendered, folder = photos
    options = replace(MetadataOptions(load_profiles()[0], lens_mode=lens), **{f'preserve_{field}': False})
    result = export_rendered(source, rendered, folder, options)
    meta = read_metadata(result)
    all_meta = json.loads(exiftool('-j', '-G1', '-a', str(result)))[0]
    assert not any(k.startswith(('XMP', 'MakerNotes', 'JUMBF', 'C2PA')) for k in all_meta)
    assert ('LensModel' in meta) == (lens == 'original')
    assert ('LensMake' in meta) == (lens != 'remove')
    absent = {'date': 'DateTimeOriginal', 'exposure': 'ISO', 'gps': 'GPSLatitude'}[field]
    assert absent not in meta


def test_collisions_and_default_directory(photos):
    source, rendered, _ = photos
    options = MetadataOptions(load_profiles()[0])
    a = export_rendered(source, rendered, None, options)
    saved = a.read_bytes()
    b = export_rendered(source, rendered, None, options)
    assert a.parent.name == 'darktable_exported'
    assert b.stem.endswith('_1') and a.read_bytes() == saved
    b.unlink()  # an existing sidecar must also prevent reuse
    c = export_rendered(source, rendered, None, options)
    assert c.stem.endswith('_2')
    assert not list(a.parent.glob('.raw2leica-*'))
    assert not list(a.parent.glob('*.raw2leica-lock'))


@pytest.mark.parametrize('bad', ['missing', 'other', 'orientation', 'corrupt'])
def test_reject_bad_rendered(photos, bad):
    source, rendered, folder = photos
    if bad == 'corrupt':
        rendered.write_bytes(b'broken')
    else:
        icc = b'' if bad == 'missing' else ImageCms.ImageCmsProfile(ImageCms.createProfile('LAB' if bad == 'other' else 'sRGB')).tobytes()
        Image.new('RGB', (40, 60)).save(rendered, icc_profile=icc)
        if bad == 'orientation':
            exiftool('-overwrite_original', '-Orientation#=6', str(rendered))
    with pytest.raises(Exception):
        export_rendered(source, rendered, folder, MetadataOptions(load_profiles()[0]))
    assert not folder.exists()


def test_failure_and_cancellation_do_not_publish(photos, monkeypatch):
    import raw2leica.exif_export as module
    source, rendered, folder = photos
    options = MetadataOptions(load_profiles()[0])
    good = export_rendered(source, rendered, folder, options)
    saved = good.read_bytes()
    def fail(*args, **kwargs):
        raise RuntimeError('forced verification failure')
    monkeypatch.setattr(module, 'verify', fail)
    with pytest.raises(RuntimeError, match='forced'):
        export_rendered(source, rendered, folder, options)
    assert set(p.name for p in folder.iterdir()) == {good.name, good.name+'.json'}
    cancel = threading.Event(); cancel.set()
    with pytest.raises(RuntimeError, match='已取消'):
        export_rendered(source, rendered, folder, options, cancel)
    assert good.read_bytes() == saved


def test_cancellation_after_metadata(photos, monkeypatch):
    import raw2leica.exif_export as module
    source, rendered, folder = photos
    cancel = threading.Event()
    original = module.write_metadata
    def cancelling(*args, **kwargs):
        original(*args, **kwargs)
        cancel.set()
    monkeypatch.setattr(module, 'write_metadata', cancelling)
    with pytest.raises(RuntimeError, match='已取消'):
        export_rendered(source, rendered, folder, MetadataOptions(load_profiles()[0]), cancel)
    assert not list(folder.iterdir())


def test_unwritable_output_and_missing_exiftool(photos, monkeypatch):
    source, rendered, folder = photos
    folder.write_text('this is a file')
    with pytest.raises(FileExistsError):
        export_rendered(source, rendered, folder, MetadataOptions(load_profiles()[0]))
    monkeypatch.setenv('EXIFTOOL_PATH', '/nonexistent/exiftool')
    with pytest.raises(RuntimeError, match='EXIFTOOL_PATH'):
        export_rendered(source, rendered, folder.parent / 'other', MetadataOptions(load_profiles()[0]))


def test_cli_contract_and_lightweight_import(photos, capsys, monkeypatch, tmp_path):
    source, rendered, folder = photos
    monkeypatch.setenv('EXIFTOOL_PATH', str(Path('.tools/exiftool/bin/exiftool').resolve()))
    assert main(['--profiles']) == 0
    assert len(json.loads(capsys.readouterr().out)['profiles']) == 9
    assert main(['--source', str(source), '--rendered', str(rendered), '--output-dir', str(folder), '--no-preserve-gps']) == 0
    assert json.loads(capsys.readouterr().out)['metadata_verified']
    assert main(['--source', str(source)]) == 1
    assert not json.loads(capsys.readouterr().out)['ok']
    check = subprocess.run([sys.executable, '-c', 'import sys; import raw2leica.exif_export; assert not any(x in sys.modules for x in ["PySide6", "rawpy", "numpy", "raw2leica.imaging"])'], capture_output=True)
    assert check.returncode == 0, check.stderr


def test_install_block_preserves_user_content():
    from scripts.install_darktable import BEGIN, END, remove_block, lua_string
    original = 'require "personal"\n' + BEGIN + '\nmanaged\n' + END + '\n-- user tail\n'
    assert remove_block(original) == 'require "personal"\n-- user tail\n'
    assert remove_block('personal') == 'personal'
    with pytest.raises(RuntimeError):
        remove_block(BEGIN)
    assert lua_string('a\nb"\\') == '"a\\010b\\"\\\\"'


def test_concurrent_exports_never_overwrite(photos):
    from concurrent.futures import ThreadPoolExecutor
    source, rendered, folder = photos
    options = MetadataOptions(load_profiles()[0])
    with ThreadPoolExecutor(max_workers=3) as pool:
        outputs = list(pool.map(lambda _: export_rendered(source, rendered, folder, options), range(3)))
    assert len(set(outputs)) == 3
    assert all(p.is_file() and p.with_suffix('.jpg.json').is_file() for p in outputs)
    assert len(list(folder.iterdir())) == 6


def test_publication_failure_removes_report(photos, monkeypatch):
    import raw2leica.exif_export as module
    source, rendered, folder = photos
    original = module.os.link
    def link(a, b):
        if Path(b).suffix == '.jpg':
            raise OSError('forced publication failure')
        original(a, b)
    monkeypatch.setattr(module.os, 'link', link)
    with pytest.raises(OSError, match='publication'):
        export_rendered(source, rendered, folder, MetadataOptions(load_profiles()[0]))
    assert not list(folder.iterdir())


def test_invalid_cli_arguments_return_json(capsys):
    assert main(['--profile', 'invalid']) == 1
    assert not json.loads(capsys.readouterr().out)['ok']


def test_rational_values_are_copied_without_display_rounding(photos):
    from raw2leica.metadata import verify
    source, rendered, folder = photos
    exiftool('-overwrite_original', '-FNumber#=1.779999971', '-FocalLength#=6.764999866',
             '-ExposureTime#=0.000149008818', '-GPSLatitude#=31.20350962965',
             '-GPSAltitude#=3.493522374', str(source))
    options = MetadataOptions(load_profiles()[0])
    output = export_rendered(source, rendered, folder, options)
    original, actual = read_metadata(source), read_metadata(output)
    for key in ('FNumber', 'FocalLength', 'ExposureTime', 'GPSLatitude', 'GPSAltitude'):
        assert math.isclose(actual[key], original[key], rel_tol=1e-6, abs_tol=1e-10)
    assert actual['FNumber'] != 1.8  # print conversion would round it to 1.8
    with pytest.raises(RuntimeError, match='FNumber'):
        verify(output, {**original, 'FNumber': 1.9}, options, (40, 60))


@pytest.mark.skipif(os.getuid() == 0, reason='root bypasses directory write permissions')
def test_readonly_directory_does_not_publish(photos):
    source, rendered, folder = photos
    folder.mkdir()
    folder.chmod(0o500)
    try:
        with pytest.raises(PermissionError):
            export_rendered(source, rendered, folder, MetadataOptions(load_profiles()[0]))
    finally:
        folder.chmod(0o700)
    assert not list(folder.iterdir())


def test_native_export_keeps_darktable_filename_and_encoding(photos):
    source, rendered, folder = photos
    folder.mkdir()
    native = folder / "自定义_'名称_2026_01.JPG"
    native.write_bytes(rendered.read_bytes())
    source_bytes = source.read_bytes()
    result = process_native_export(source, native, MetadataOptions(load_profiles()[0]))
    assert result == native.resolve()
    assert source.read_bytes() == source_bytes
    assert jpeg_image_digest(result) == jpeg_image_digest(rendered)
    report = json.loads(result.with_suffix('.JPG.json').read_text())
    assert report['output'] == str(result)
    assert report['pipeline'] == 'darktable-native-disk-v1'
    assert set(p.name for p in folder.iterdir()) == {native.name, native.name+'.json'}
    # Native overwrite mode can reuse a path and must refresh its managed report.
    native.write_bytes(rendered.read_bytes())
    process_native_export(source, native, MetadataOptions(load_profiles()[1]))
    assert json.loads(result.with_suffix('.JPG.json').read_text())['target'] == load_profiles()[1].model


@pytest.mark.parametrize('kind', ['same', 'hardlink', 'symlink'])
def test_native_export_rejects_source_aliases(photos, kind):
    source, _, folder = photos
    original = source.read_bytes()
    native = source if kind == 'same' else folder.parent / 'alias.jpg'
    if kind == 'hardlink':
        os.link(source, native)
    elif kind == 'symlink':
        native.symlink_to(source)
    with pytest.raises(ValueError):
        process_native_export(source, native, MetadataOptions(load_profiles()[0]))
    assert source.read_bytes() == original and native.exists()


def test_native_verification_failure_cleans_only_failed_export(photos, monkeypatch):
    import raw2leica.exif_export as module
    source, rendered, _ = photos
    good = rendered.with_name('good.jpg')
    good.write_bytes(rendered.read_bytes())
    process_native_export(source, good, MetadataOptions(load_profiles()[0]))
    saved = good.read_bytes()
    def fail(*args, **kwargs):
        raise RuntimeError('forced native verification failure')
    monkeypatch.setattr(module, 'verify', fail)
    with pytest.raises(RuntimeError, match='forced native'):
        process_native_export(source, rendered, MetadataOptions(load_profiles()[0]))
    assert not rendered.exists() and not rendered.with_suffix('.jpg.json').exists()
    assert good.read_bytes() == saved and good.with_suffix('.jpg.json').exists()
    assert not list(good.parent.glob('.raw2leica-native-*'))


def test_native_publication_failure_cleans_report(photos, monkeypatch):
    import raw2leica.exif_export as module
    source, rendered, _ = photos
    replace_file = module.os.replace
    def fail(a, b):
        if Path(b) == rendered:
            raise OSError('forced native publication failure')
        replace_file(a, b)
    monkeypatch.setattr(module.os, 'replace', fail)
    with pytest.raises(OSError, match='publication'):
        process_native_export(source, rendered, MetadataOptions(load_profiles()[0]))
    assert not rendered.exists() and not rendered.with_suffix('.jpg.json').exists()


def test_native_export_preserves_unrelated_sidecar(photos):
    source, rendered, _ = photos
    report = rendered.with_suffix('.jpg.json')
    unrelated = '{"personal":"keep"}'
    report.write_text(unrelated)
    with pytest.raises(ValueError, match='其他 JSON'):
        process_native_export(source, rendered, MetadataOptions(load_profiles()[0]))
    assert not rendered.exists() and report.read_text() == unrelated


def test_native_export_cli(photos, capsys):
    source, rendered, _ = photos
    assert main(['--native-export', '--source', str(source), '--rendered', str(rendered)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['output'] == str(rendered.resolve()) and result['metadata_verified']


def test_rec709_jpeg_preserves_profile_and_never_labels_srgb(photos):
    source, rendered, folder = photos
    icc = Path('tests/fixtures/darktable-rec709.icc').read_bytes()
    Image.new('RGB', (40, 60), '#718b90').save(rendered, icc_profile=icc)
    digest = jpeg_image_digest(rendered)
    output = export_rendered(source, rendered, folder, MetadataOptions(load_profiles()[0]))
    assert read_metadata(output)['ColorSpace'] == 65535
    with Image.open(output) as image:
        assert image.info['icc_profile'] == icc
    assert jpeg_image_digest(output) == digest


@pytest.mark.parametrize('profile', load_profiles(), ids=lambda p: p.id)
@pytest.mark.parametrize('icc', [False, True], ids=['nclx709', 'icc709'])
def test_heif_export_preserves_encoded_items_and_colour(photos, profile, icc):
    from pillow_heif import register_heif_opener
    from raw2leica.heif_data import heif_image_digest
    register_heif_opener()
    source, _, folder = photos
    rendered = folder.parent / '中文 空格 $ HEIF.heif'
    options = {'icc_profile': Path('tests/fixtures/darktable-rec709.icc').read_bytes()} if icc else {}
    Image.new('RGB', (67, 49), '#789f31').save(rendered, format='HEIF', quality=90,
        color_primaries=1, transfer_characteristics=1, matrix_coefficients=1, **options)
    exiftool('-overwrite_original', '-Make=Wrong', '-Model=Wrong', '-XMP:SerialNumber=private', str(rendered))
    original, source_bytes = rendered.read_bytes(), source.read_bytes()
    digest = heif_image_digest(rendered)
    with Image.open(rendered) as image:
        pixels = image.tobytes(); original_icc = image.info.get('icc_profile')
    output = export_rendered(source, rendered, folder, MetadataOptions(profile, False, False, False, 'remove'))
    assert output.suffix == '.heic'
    assert heif_image_digest(output) == digest
    assert source.read_bytes() == source_bytes and rendered.read_bytes() == original
    actual = read_metadata(output)
    assert actual['Model'] == profile.model and actual['ColorSpace'] == 65535
    assert not any(k in actual for k in ['DateTimeOriginal', 'ExposureTime', 'GPSLatitude', 'LensMake'])
    all_tags = json.loads(exiftool('-j', '-G1', '-a', str(output)))[0]
    assert not any(k.startswith(('XMP:', 'MakerNotes:', 'C2PA:', 'JUMBF:')) for k in all_tags)
    with Image.open(output) as image:
        assert image.size == (67, 49) and image.tobytes() == pixels
        assert image.info.get('icc_profile') == original_icc
    assert output.with_suffix('.heic.json').is_file()


def test_native_heif_keeps_actual_extension_and_report(photos):
    from pillow_heif import register_heif_opener
    register_heif_opener()
    source, _, _ = photos
    rendered = source.parent / '原生命名.HIF'
    Image.new('RGB', (67, 49)).save(rendered, format='HEIF', color_primaries=1,
                                   transfer_characteristics=1, matrix_coefficients=1)
    assert process_native_export(source, rendered, MetadataOptions(load_profiles()[0])) == rendered
    report = json.loads(rendered.with_suffix('.HIF.json').read_text())
    assert report['format'] == 'HEIF' and report['metadata_verified']
    assert report['output'] == str(rendered)


def test_heif_10bit_and_lens_info_rational_rounding(photos):
    from pillow_heif import from_bytes
    from raw2leica.heif_data import heif_image_digest
    from raw2leica.metadata import verify
    source, _, folder = photos
    exiftool('-overwrite_original', '-LensInfo#=15.65999985 15.65999985 2.799999952 2.799999952', str(source))
    rendered = folder.parent / '10bit.heic'
    from_bytes('RGB;16', (67, 49), b'\x00\x40'*(67*49*3)).save(rendered,
        quality=90, color_primaries=1, transfer_characteristics=1, matrix_coefficients=1)
    assert read_metadata(rendered)['FileType'] == 'HEIF'
    digest = heif_image_digest(rendered)
    options = MetadataOptions(load_profiles()[0], lens_mode='original')
    output = export_rendered(source, rendered, folder, options)
    assert heif_image_digest(output) == digest
    actual = read_metadata(output)
    assert actual['FileType'] == 'HEIF'
    assert actual['ExifImageWidth'] == 67 and actual['ExifImageHeight'] == 49
    with pytest.raises(RuntimeError, match='LensInfo'):
        verify(output, {**read_metadata(source), 'LensInfo':'16 16 2.8 2.8'}, options,
               (67,49), file_type='HEIF', color_space=65535, require_icc=False)


def test_heif_metadata_does_not_require_pixel_decoder(photos, monkeypatch):
    from pillow_heif import register_heif_opener
    from pillow_heif.as_plugin import HeifImageFile
    register_heif_opener()
    source, _, folder = photos
    rendered = source.parent / 'lossless.heic'
    Image.new('RGB', (67,49)).save(rendered, format='HEIF', quality=-1,
        color_primaries=1, transfer_characteristics=1, matrix_coefficients=1)
    def no_decode(*args, **kwargs):
        raise RuntimeError('pixel decoder unavailable')
    monkeypatch.setattr(HeifImageFile, 'load', no_decode)
    output = export_rendered(source, rendered, folder, MetadataOptions(load_profiles()[0]))
    assert output.is_file()
