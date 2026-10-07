"""Digest HEIF encoded image items without EXIF/XMP or offset tables."""
import hashlib
from pathlib import Path


def boxes(data, start=0, end=None):
    end = len(data) if end is None else end
    while start < end:
        if start + 8 > end:
            raise ValueError('HEIF box 不完整')
        size = int.from_bytes(data[start:start+4], 'big')
        kind = data[start+4:start+8]
        header = 8
        if size == 1:
            size = int.from_bytes(data[start+8:start+16], 'big'); header = 16
        elif size == 0:
            size = end-start
        if size < header or start+size > end:
            raise ValueError('HEIF box 尺寸无效')
        yield kind, start+header, start+size
        start += size


def heif_image_digest(path: Path) -> str:
    data = path.read_bytes()
    meta = next((data[a+4:b] for k, a, b in boxes(data) if k == b'meta'), None)
    if meta is None:
        raise ValueError('HEIF 缺少图像项目表')
    children = {k: meta[a:b] for k, a, b in boxes(meta)}
    info, loc = children[b'iinf'], children[b'iloc']
    types = {}
    for kind, a, b in boxes(info, 6 if info[0] == 0 else 8):
        if kind != b'infe':
            continue
        item = info[a:b]; version = item[0]
        if version not in (2, 3):
            raise ValueError('暂不支持此 HEIF 项目表版本')
        width = 2 if version == 2 else 4
        types[int.from_bytes(item[4:4+width], 'big')] = item[6+width:10+width]
    version = loc[0]
    if version not in (0, 1, 2):
        raise ValueError('暂不支持此 HEIF 地址表版本')
    offset_size, length_size = loc[4] >> 4, loc[4] & 15
    base_size, index_size = loc[5] >> 4, loc[5] & 15 if version else 0
    pos = 6
    def take(n):
        nonlocal pos
        if pos+n > len(loc):
            raise ValueError('HEIF 地址表不完整')
        value = int.from_bytes(loc[pos:pos+n], 'big'); pos += n
        return value
    count = take(4 if version == 2 else 2)
    digest = hashlib.sha256()
    images = 0
    for _ in range(count):
        item_id = take(4 if version == 2 else 2)
        method = take(2) & 15 if version else 0
        reference = take(2)
        base = take(base_size)
        extents = take(2)
        kind = types.get(item_id)
        image = kind in (b'hvc1', b'grid', b'iovl', b'jpeg', b'av01')
        if image:
            if reference or method not in (0, 1):
                raise ValueError('HEIF 图像项目引用不支持')
            digest.update(kind); images += 1
        for _ in range(extents):
            if index_size: take(index_size)
            offset, length = take(offset_size), take(length_size)
            if image:
                content = data if method == 0 else children[b'idat']
                start = base+offset
                if not length or start+length > len(content):
                    raise ValueError('HEIF 编码图像数据不完整')
                digest.update(content[start:start+length])
    if not images:
        raise ValueError('HEIF 缺少可验证的编码图像')
    # Properties include codec configuration, colour declarations and transforms.
    digest.update(children.get(b'iprp', b''))
    return digest.hexdigest()
