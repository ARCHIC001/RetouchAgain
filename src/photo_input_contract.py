"""Colour-managed *source* input, separate from the strict RGB8 model attachment.

No RAW imports, downloads, backend calls, implicit sRGB, source writes or hashes.
TIFF/PNG16 use the already-installed optional codecs, never Pillow's RGB16 path.
A named declaration means the caller takes responsibility for untagged pixels.
Alpha backgrounds are explicit sRGB code values in [0,1], composed in linear light.
"""
from __future__ import annotations

import ctypes as C
import importlib
import io
from pathlib import Path
import struct
import sys
import warnings
import zlib

import numpy as np
from PIL import Image, ImageCms

sys.path.insert(0, str(Path(__file__).resolve().parent))
from photo_image_contract import (MAX_BYTES, MAX_PIXELS, ORIENTATIONS,
    orientation_value, resolved_orientation, inspect_image)

SCHEMA = 'photo-source-input/v1'
MAX_ICC_BYTES = 4 * 1024 * 1024
_SPACES = {'srgb', 'display-p3', 'adobe-rgb', 'linear-srgb',
           'linear-display-p3', 'linear-adobe-rgb'}
_PRIMARIES = {
    'srgb': ((.64, .33), (.30, .60), (.15, .06)),
    'display-p3': ((.68, .32), (.265, .69), (.15, .06)),
    'adobe-rgb': ((.64, .33), (.21, .71), (.15, .06)),
}


def _optional(name):
    try:
        return importlib.import_module(name)
    except ImportError:
        # Existing local dependency bundle, not a runtime install or download.
        bundle = Path.home() / 'AppData/Local/Temp/paired-photos-raw-deps'
        if not (bundle / name / '__init__.py').is_file():
            raise ValueError(f'Unsupported: optional local {name} dependency unavailable')
        if str(bundle) not in sys.path:
            sys.path.append(str(bundle))
        try:
            return importlib.import_module(name)
        except ImportError as exc:
            raise ValueError(f'Unsupported: optional local {name} dependency unavailable') from exc


def _space(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('declared_space must be an explicit supported space name')
    aliases = {'s-rgb': 'srgb', 'adobergb': 'adobe-rgb', 'adobe-rgb-1998': 'adobe-rgb',
               'adobergb1998': 'adobe-rgb', 'linearsrgb': 'linear-srgb',
               'gray-srgb': 'srgb', 'linear-gray': 'linear-srgb'}
    value = value.lower().strip().replace('_', '-').replace(' ', '-')
    value = aliases.get(value, value)
    if value not in _SPACES:
        raise ValueError('Unsupported declared_space: ' + value)
    return value


def _matte(value):
    if value is None:
        return None
    if isinstance(value, str):
        if value not in ('black', 'white'):
            raise ValueError('Alpha background name must be black or white')
        value = (0., 0., 0.) if value == 'black' else (1., 1., 1.)
    try:
        result = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError('Alpha background must be three sRGB values in [0,1]') from exc
    if result.shape != (3,) or not np.isfinite(result).all() or (result < 0).any() or (result > 1).any():
        raise ValueError('Alpha background must be three sRGB values in [0,1]')
    return result


def _decode_transfer(x):
    return np.where(x <= .04045, x / 12.92, ((np.maximum(x, 0) + .055) / 1.055) ** 2.4)


def _encode_transfer(x):
    return np.where(x <= .0031308, x * 12.92, 1.055 * np.maximum(x, 0) ** (1 / 2.4) - .055)


def _rgb_matrix(name):
    xy = np.array(_PRIMARIES[name], dtype=np.float64)
    columns = np.array([xy[:, 0] / xy[:, 1], np.ones(3),
                        (1 - xy.sum(axis=1)) / xy[:, 1]])
    white = np.array([.3127 / .3290, 1., (1 - .3127 - .3290) / .3290])
    return columns * np.linalg.solve(columns, white)


def _orient(array, orientation):
    # Shared EXIF validation and transpose enum; numpy retains 16/64-bit samples.
    operation = ORIENTATIONS.get(orientation_value(orientation))
    if operation == Image.Transpose.FLIP_LEFT_RIGHT: return array[:, ::-1]
    if operation == Image.Transpose.ROTATE_180: return array[::-1, ::-1]
    if operation == Image.Transpose.FLIP_TOP_BOTTOM: return array[::-1]
    if operation == Image.Transpose.TRANSPOSE: return array.swapaxes(0, 1)
    if operation == Image.Transpose.ROTATE_270: return np.rot90(array, -1)
    if operation == Image.Transpose.TRANSVERSE: return array.swapaxes(0, 1)[::-1, ::-1]
    if operation == Image.Transpose.ROTATE_90: return np.rot90(array, 1)
    return array


def _dimensions(width, height):
    if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
        raise ValueError('Source dimensions exceed pixel capacity')


def _png_chunks(data):
    pos = 8; chunks = {}; seen_idat = False
    while pos + 12 <= len(data):
        length, kind = struct.unpack('>I4s', data[pos:pos + 8])
        end = pos + length + 12
        if end > len(data): raise ValueError('Truncated PNG')
        if kind in (b'acTL', b'fcTL', b'fdAT'): raise ValueError('Animated PNG is unsupported')
        if kind in (b'IHDR', b'iCCP', b'sRGB', b'tRNS'):
            if kind in chunks: raise ValueError('Duplicate PNG declaration')
            if kind != b'IHDR' and seen_idat: raise ValueError('Late PNG declaration')
            chunks[kind] = data[pos + 8:pos + 8 + length]
        if kind == b'IDAT': seen_idat = True
        if kind == b'IEND':
            if length or end != len(data) or not seen_idat: raise ValueError('Invalid PNG terminal structure')
            return chunks
        pos = end
    raise ValueError('PNG is missing terminal IEND')


def _png_icc(chunks):
    if b'iCCP' not in chunks: return None
    value = chunks[b'iCCP']; zero = value.find(b'\0')
    if zero < 1 or zero > 79 or value[zero + 1:zero + 2] != b'\0':
        raise ValueError('Invalid PNG ICC declaration')
    dec = zlib.decompressobj()
    try:
        profile = dec.decompress(value[zero + 2:], MAX_ICC_BYTES + 1)
    except zlib.error as exc:
        raise ValueError('Malformed compressed ICC') from exc
    if len(profile) > MAX_ICC_BYTES or not dec.eof or dec.unused_data or dec.unconsumed_tail:
        raise ValueError('Invalid or oversized compressed ICC')
    return profile


def _read_source(path):
    path = Path(path).resolve()
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('Missing or oversized source file')
    data = path.read_bytes()
    if len(data) > MAX_BYTES: raise ValueError('Oversized source file')
    fmt = ('PNG' if data.startswith(b'\x89PNG\r\n\x1a\n') else
           'JPEG' if data.startswith(b'\xff\xd8') else
           'TIFF' if data[:4] in (b'II*\0', b'MM\0*', b'II+\0', b'MM\0+') else None)
    suffixes = {'PNG': ('.png',), 'JPEG': ('.jpg', '.jpeg'), 'TIFF': ('.tif', '.tiff')}
    if fmt is None or path.suffix.lower() not in suffixes[fmt]:
        raise ValueError('Unsupported source format or content/suffix contradiction; RAW must be routed separately')
    profile = None; alpha = None; alpha_kind = None; explicit_srgb = False; exif_colour = None; adobe_hint = False
    if fmt == 'TIFF':
        tiff = _optional('tifffile')
        with tiff.TiffFile(io.BytesIO(data)) as container:
            if len(container.pages) != 1: raise ValueError('Multipage TIFF is unsupported; select a source explicitly')
            page = container.pages[0]
            _dimensions(page.imagewidth, page.imagelength)
            if any(tag in page.tags for tag in (33421, 33422, 50706)) or int(page.photometric) == 32803:
                raise ValueError('RAW/CFA TIFF requires separate RAW source route')
            orientation = orientation_value(int(page.tags[274].value)) if 274 in page.tags else 1
            photo = int(page.photometric)
            model = {0: 'GRAY', 1: 'GRAY', 2: 'RGB', 5: 'CMYK'}.get(photo)
            if model is None: raise ValueError('Unsupported TIFF photometric interpretation')
            if model == 'CMYK' and 332 in page.tags and page.tags[332].value != 1:
                raise ValueError('Unsupported TIFF inkset')
            profile = bytes(page.tags[34675].value) if 34675 in page.tags else None
            array = page.asarray()
            if int(page.planarconfig) == 2 and array.ndim == 3: array = np.moveaxis(array, 0, -1)
            if array.ndim == 2: array = array[..., None]
            channels = {'GRAY': 1, 'RGB': 3, 'CMYK': 4}[model]
            if array.ndim != 3 or array.shape[:2] != (page.imagelength, page.imagewidth):
                raise ValueError('Unsupported TIFF sample shape')
            extras = tuple(int(x) for x in page.extrasamples)
            if array.shape[-1] == channels + 1 and extras in ((1,), (2,)):
                alpha = array[..., -1:]; array = array[..., :channels]
                alpha_kind = 'associated' if extras == (1,) else 'unassociated'
            elif array.shape[-1] != channels or extras:
                raise ValueError('Unsupported/ambiguous TIFF extra samples')
            bits = page.bitspersample
            if isinstance(bits, tuple) or bits not in (8, 16, 32, 64):
                raise ValueError('Unsupported TIFF sample precision')
            white_zero = photo == 0
            decoder = 'tifffile (native source sample precision)'
    else:
        chunks = _png_chunks(data) if fmt == 'PNG' else None
        if fmt == 'JPEG' and not data.endswith(b'\xff\xd9'): raise ValueError('Truncated JPEG')
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                _dimensions(*image.size)
                if getattr(image, 'n_frames', 1) != 1: raise ValueError('Multiframe source is unsupported')
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                orientation = resolved_orientation(image)
                exif_colour = image.getexif().get_ifd(34665).get(40961)
                interop = image.getexif().get_ifd(40965).get(1)
                adobe_hint = interop in ('R03', b'R03', b'R03\x00')
                profile = image.info.get('icc_profile')
                if chunks is not None:
                    profile = _png_icc(chunks)
                    if b'sRGB' in chunks:
                        if len(chunks[b'sRGB']) != 1 or chunks[b'sRGB'][0] > 3: raise ValueError('Invalid PNG sRGB declaration')
                        explicit_srgb = True
                    if b'IHDR' not in chunks or len(chunks[b'IHDR']) != 13: raise ValueError('Invalid PNG header')
                    bits, colour_type = chunks[b'IHDR'][8:10]
                else: bits = 8
                if fmt == 'PNG' and bits == 16:
                    array = _optional('imagecodecs').png_decode(data)
                    if array.dtype != np.uint16: raise ValueError('PNG16 decoder did not preserve uint16 samples')
                    if array.ndim == 2: array = array[..., None]
                    model = 'GRAY' if colour_type in (0, 4) else 'RGB'
                    expected = 1 if model == 'GRAY' else 3
                    if array.shape[-1] == expected + 1:
                        alpha = array[..., -1:]; array = array[..., :expected]; alpha_kind = 'unassociated'
                    elif array.shape[-1] != expected or colour_type in (4, 6) or b'tRNS' in chunks:
                        raise ValueError('Unsupported PNG16 transparency decode')
                    decoder = 'imagecodecs.png_decode (uint16 preserved)'
                else:
                    image.load()
                    if image.mode == 'P' or 'transparency' in image.info:
                        image = image.convert('RGBA')
                    if image.mode not in ('1', 'L', 'LA', 'RGB', 'RGBA', 'CMYK'):
                        raise ValueError('Unsupported source pixel mode: ' + image.mode)
                    if image.mode == '1': image = image.convert('L')
                    model = 'CMYK' if image.mode == 'CMYK' else 'GRAY' if image.mode in ('L', 'LA') else 'RGB'
                    array = np.asarray(image)
                    if array.ndim == 2: array = array[..., None]
                    if image.mode in ('LA', 'RGBA'):
                        alpha = array[..., -1:]; array = array[..., :-1]; alpha_kind = 'unassociated'
                    decoder = 'Pillow (8-bit decoded samples)'
                    bits = 8
                white_zero = False
    if array.dtype.kind not in ('u', 'f') or (array.dtype.kind == 'u' and array.dtype.itemsize not in (1, 2)):
        raise ValueError('Unsupported source dtype: ' + str(array.dtype))
    if array.dtype.kind == 'u' and array.dtype.itemsize * 8 != bits:
        raise ValueError('Source bit depth and decoded dtype disagree')
    if not np.isfinite(array).all() or (alpha is not None and not np.isfinite(alpha).all()):
        raise ValueError('Non-finite source/alpha samples are unsupported')
    array = _orient(array, orientation)
    alpha = _orient(alpha, orientation) if alpha is not None else None
    stored = [array.shape[0], array.shape[1]] if orientation in (5, 6, 7, 8) else [array.shape[1], array.shape[0]]
    facts = {'schema': SCHEMA, 'path': str(path), 'format': fmt, 'bytes': len(data),
             'stored_dimensions': stored, 'dimensions': [array.shape[1], array.shape[0]],
             'orientation': orientation, 'orientation_applied': orientation != 1,
             'orientation_policy': 'shared EXIF 1..8, exactly once; output metadata stripped',
             'color_model': model, 'sample_dtype': str(array.dtype), 'bits_per_sample': bits,
             'decoder': decoder, 'pixel_decode_complete': True, 'alpha': alpha_kind,
             'icc_present': profile is not None, 'png_srgb_declaration': explicit_srgb,
             'hash_verification': False}
    return array, alpha, profile, white_zero, exif_colour, adobe_hint, facts


class _xyY(C.Structure):
    _fields_ = [('x', C.c_double), ('y', C.c_double), ('Y', C.c_double)]


class _Triple(C.Structure):
    _fields_ = [('Red', _xyY), ('Green', _xyY), ('Blue', _xyY)]


class _LCMS:
    """Small native double-output bridge; no eight-bit intermediate at the CMS."""
    def __init__(self):
        candidates = [Path(sys.executable).parent.parent / 'native/poppler/Library/bin/lcms2.dll',
                      Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/lcms2.dll']
        lib = None
        for path in candidates:
            if path.is_file():
                try:
                    lib = C.CDLL(str(path)); self.path = str(path); break
                except OSError: pass
        if lib is None: raise ValueError('Unsupported: existing LittleCMS2 DLL unavailable; no high-depth ICC fallback to RGB8')
        self.lib = lib
        specs = {'cmsOpenProfileFromMem': (C.c_void_p, [C.c_void_p, C.c_uint32]),
                 'cmsCloseProfile': (C.c_int, [C.c_void_p]),
                 'cmsBuildGamma': (C.c_void_p, [C.c_void_p, C.c_double]),
                 'cmsFreeToneCurve': (None, [C.c_void_p]),
                 'cmsCreateRGBProfile': (C.c_void_p, [C.POINTER(_xyY), C.POINTER(_Triple), C.POINTER(C.c_void_p)]),
                 'cmsCreateTransform': (C.c_void_p, [C.c_void_p, C.c_uint32, C.c_void_p, C.c_uint32, C.c_uint32, C.c_uint32]),
                 'cmsDeleteTransform': (None, [C.c_void_p]),
                 'cmsDoTransform': (None, [C.c_void_p, C.c_void_p, C.c_void_p, C.c_uint32]),
                 'cmsReadTag': (C.c_void_p, [C.c_void_p, C.c_uint32]),
                 'cmsIsToneCurveLinear': (C.c_int, [C.c_void_p]),
                 'cmsIsMatrixShaper': (C.c_int, [C.c_void_p]),
                 'cmsGetEncodedCMMversion': (C.c_uint32, [])}
        for name, (ret, arg) in specs.items():
            fn = getattr(lib, name); fn.restype = ret; fn.argtypes = arg
        self.version = int(lib.cmsGetEncodedCMMversion())

    def transform(self, array, profile, model, *, require_linear=False, execute=True):
        lib = self.lib; buffer = C.create_string_buffer(profile)
        source = lib.cmsOpenProfileFromMem(buffer, len(profile))
        if not source: raise ValueError('Invalid/unsupported ICC profile')
        curve = output = transform = None
        try:
            if require_linear:
                tags = ('rTRC', 'gTRC', 'bTRC') if model == 'RGB' else ('kTRC',)
                if model == 'CMYK' or (model == 'RGB' and not lib.cmsIsMatrixShaper(source)):
                    raise ValueError('Float ICC must be a trusted linear matrix/TRC RGB or gray profile')
                for tag in tags:
                    ptr = lib.cmsReadTag(source, int.from_bytes(tag.encode(), 'big'))
                    if not ptr or not lib.cmsIsToneCurveLinear(ptr):
                        raise ValueError('Float ICC must have explicitly linear TRCs; encoded/unknown float spaces unsupported')
            white = _xyY(.3127, .3290, 1.)
            primaries = _Triple(*(_xyY(x, y, 1.) for x, y in _PRIMARIES['srgb']))
            curve = lib.cmsBuildGamma(None, 1.)
            if not curve: raise ValueError('Unable to build linear sRGB output profile')
            output = lib.cmsCreateRGBProfile(C.byref(white), C.byref(primaries), (C.c_void_p * 3)(curve, curve, curve))
            channels = {'RGB': 3, 'GRAY': 1, 'CMYK': 4}[model]
            pixel_type = {'RGB': 4, 'GRAY': 3, 'CMYK': 6}[model]
            is_float = array.dtype.kind == 'f'
            input_format = (pixel_type << 16) | (channels << 3) | (0 if is_float else array.dtype.itemsize)
            if is_float: input_format |= 1 << 22
            # TYPE_RGB_DBL; relative colorimetric, no BPC, no optimiser/cache.
            output_format = (1 << 22) | (4 << 16) | (3 << 3)
            transform = lib.cmsCreateTransform(source, input_format, output, output_format, 1, 0x100 | 0x40)
            if not transform: raise ValueError('Unsupported ICC transform for source sample type/model')
            if not execute: return None
            values = np.ascontiguousarray(array, dtype=np.float64 if is_float else array.dtype.newbyteorder('='))
            result = np.empty((*values.shape[:2], 3), dtype=np.float64)
            lib.cmsDoTransform(transform, values.ctypes.data, result.ctypes.data, values.shape[0] * values.shape[1])
            if not np.isfinite(result).all(): raise ValueError('ICC conversion produced non-finite samples')
            return result
        finally:
            if transform: lib.cmsDeleteTransform(transform)
            if output: lib.cmsCloseProfile(output)
            if curve: lib.cmsFreeToneCurve(curve)
            lib.cmsCloseProfile(source)


def _profile_facts(profile, model):
    if not isinstance(profile, bytes) or not 132 <= len(profile) <= MAX_ICC_BYTES:
        raise ValueError('Invalid or oversized ICC profile')
    if int.from_bytes(profile[:4], 'big') != len(profile) or profile[36:40] != b'acsp':
        raise ValueError('Malformed ICC header/length')
    if profile[12:16] not in (b'mntr', b'scnr', b'prtr', b'spac') or profile[20:24] not in (b'XYZ ', b'Lab '):
        raise ValueError('Unsupported ICC profile class/PCS')
    if profile[16:20].decode('ascii', errors='replace').strip() != model:
        raise ValueError('ICC color model conflicts with decoded samples')
    count = int.from_bytes(profile[128:132], 'big')
    if count > 4096 or 132 + count * 12 > len(profile): raise ValueError('Malformed ICC tag table')
    for i in range(count):
        _, offset, size = struct.unpack('>4sII', profile[132 + i * 12:144 + i * 12])
        if offset < 132 + count * 12 or size < 8 or offset + size > len(profile):
            raise ValueError('Malformed ICC tag bounds')
    try:
        parsed = ImageCms.ImageCmsProfile(io.BytesIO(profile))
        name = ImageCms.getProfileName(parsed).strip()
    except Exception as exc:
        raise ValueError('Malformed ICC profile') from exc
    return {'profile_name': name, 'profile_bytes': len(profile), 'profile_color_space': model,
            'profile_validation': 'header/tag-bounds + LittleCMS profile/transform admission; name is descriptive only'}


def _prepare(path, declared_space, alpha_background):
    array, alpha, profile, white_zero, exif_colour, adobe_hint, facts = _read_source(path)
    declaration = _space(declared_space); matte = _matte(alpha_background)
    model = facts['color_model']; is_float = array.dtype.kind == 'f'
    if alpha is not None and matte is None:
        raise ValueError('Alpha source requires explicit alpha_background (sRGB matte in [0,1])')
    if alpha is None and matte is not None: raise ValueError('alpha_background supplied for source without alpha')
    if alpha is not None and model == 'CMYK': raise ValueError('Unsupported CMYK alpha source')
    if exif_colour not in (None, 1, 65535): raise ValueError('Unsupported EXIF color-space declaration')
    cms = None
    if profile is not None:
        facts.update(_profile_facts(profile, model))
        if declaration is not None:
            raise ValueError('Embedded ICC is authoritative; do not override it with declared_space')
        if facts['png_srgb_declaration'] or exif_colour == 1:
            from dsh_photo_color import validate
            validate(profile)  # Behavior-based, not a profile-name shortcut.
        cms = _LCMS()
        if adobe_hint:
            if model != 'RGB': raise ValueError('EXIF AdobeRGB hint conflicts with source model')
            samples = np.array([[[r, g, b] for r in (0, 64, 128, 192, 255)
                for g in (0, 64, 128, 192, 255) for b in (0, 64, 128, 192, 255)]], dtype=np.uint8)
            actual = cms.transform(samples, profile, model)
            expected = (samples / 255.) ** (563 / 256) @ (np.linalg.inv(_rgb_matrix('srgb')) @ _rgb_matrix('adobe-rgb')).T
            if np.max(np.abs(actual - expected)) > .006:
                raise ValueError('Embedded ICC color behavior conflicts with EXIF AdobeRGB hint')
        cms.transform(array, profile, model, require_linear=is_float, execute=False)
        facts.update({'color_authority': 'embedded-icc', 'conversion_engine': 'LittleCMS2 native -> linear sRGB double',
                      'lcms_dll': cms.path, 'lcms_version_encoded': cms.version,
                      'rendering_intent': 'relative-colorimetric', 'black_point_compensation': False})
    else:
        if declaration is None and facts['png_srgb_declaration']: declaration = 'srgb'
        if declaration is None: raise ValueError('Untagged source requires declared_space; never default to sRGB')
        if model == 'CMYK': raise ValueError('CMYK source requires a real embedded CMYK ICC profile')
        if model == 'GRAY' and declaration not in ('srgb', 'linear-srgb'):
            raise ValueError('Gray declaration must be srgb or linear-srgb')
        if is_float and not declaration.startswith('linear-'):
            raise ValueError('Float source requires explicit linear space or trusted embedded linear ICC')
        if (exif_colour == 1 or facts['png_srgb_declaration']) and declaration != 'srgb':
            raise ValueError('Explicit sRGB metadata conflicts with declared_space')
        if adobe_hint and declaration != 'adobe-rgb': raise ValueError('AdobeRGB EXIF conflicts with declared_space')
        facts.update({'color_authority': 'png-sRGB-chunk' if facts['png_srgb_declaration'] else 'caller-declared-space',
                      'declared_space': declaration, 'conversion_engine': 'float64 declared primaries/D65 matrix + exact named TRC'})
    if alpha is not None:
        scale = 1. if is_float else float(np.iinfo(array.dtype).max)
        alpha = alpha.astype(np.float64) / scale
        if (alpha < 0).any() or (alpha > 1).any(): raise ValueError('Alpha samples outside [0,1]')
    facts.update({'supported': True, 'alpha_background_srgb': matte.tolist() if matte is not None else None,
                  'float_policy': 'finite linear; out-of-display-range values clipped only at preview encoding' if is_float else None,
                  'source_modified': False, 'model_attachment_contract_unchanged': True})
    return array, alpha, profile, white_zero, declaration, matte, cms, facts


def inspect_input(path, *, declared_space=None, alpha_background=None) -> dict:
    """Fully decode and validate source eligibility; raises ValueError if unsupported."""
    return _prepare(path, declared_space, alpha_background)[-1]


def _linear_source(path, declared_space, alpha_background):
    array, alpha, profile, white_zero, declaration, matte, cms, facts = _prepare(path, declared_space, alpha_background)
    scale = float(np.iinfo(array.dtype).max) if array.dtype.kind == 'u' else 1.
    if white_zero or facts['alpha'] == 'associated':
        array = array.astype(np.float64) / scale
        if white_zero: array = 1. - array
        if facts['alpha'] == 'associated':
            array = np.divide(array, alpha, out=np.zeros_like(array), where=alpha != 0)
        scale = 1.
    if profile is not None:
        linear = cms.transform(array, profile, facts['color_model'])
    else:
        values = array.astype(np.float64) / scale
        name = declaration.removeprefix('linear-')
        if declaration.startswith('linear-'): linear = values
        elif name in ('srgb', 'display-p3'): linear = _decode_transfer(values)
        else: linear = np.maximum(values, 0) ** (563 / 256)
        if facts['color_model'] == 'GRAY': linear = np.repeat(linear, 3, axis=2)
        elif name != 'srgb': linear = linear @ (np.linalg.inv(_rgb_matrix('srgb')) @ _rgb_matrix(name)).T
    if alpha is not None:
        linear = linear * alpha + _decode_transfer(matte)[None, None, :] * (1. - alpha)
    return linear, facts, {'source_precision_preserved_until_color_conversion': True,
        'cms_input_dtype': str(array.dtype) if profile is not None else None,
        'working_space': 'linear-sRGB D65 float64',
        'alpha_compositing': 'linear-sRGB over explicit sRGB matte' if alpha is not None else None,
        'alpha_unassociated_before_conversion': facts['alpha'] == 'associated',
        'gamut_policy': 'relative-colorimetric then explicit display clipping; no HDR tone mapping',
        'output_exif_removed': True, 'output_icc': 'actual sRGB', 'source_modified': False,
        'destination_overwritten': False, 'hash_verification': False}


def to_srgb_preview(path, dest, *, declared_space=None, alpha_background=None, max_side=512) -> dict:
    """Write a new upright RGB8 sRGB PNG/JPEG, with conversion provenance.

    max_side is a long-edge bound, never an instruction to upscale. No overwrite.
    """
    if isinstance(max_side, bool) or not isinstance(max_side, int) or not 1 <= max_side <= 512:
        raise ValueError('Model preview max_side must be an integer in 1..512')
    source = Path(path).resolve(); dest = Path(dest).resolve()
    if dest == source or dest.exists(): raise ValueError('Destination must be new and must not overwrite source/output')
    fmt = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG'}.get(dest.suffix.lower())
    if fmt is None: raise ValueError('Preview destination must be .png/.jpg/.jpeg')
    linear, facts, provenance = _linear_source(source, declared_space, alpha_background)
    original_size = [linear.shape[1], linear.shape[0]]
    factor = min(1., max_side / max(original_size))
    size = tuple(max(1, round(x * factor)) for x in original_size)
    resized = list(size) != original_size
    if resized:
        linear = np.stack([np.asarray(Image.fromarray(linear[..., c].astype(np.float32)).resize(size, Image.Resampling.LANCZOS), dtype=np.float64) for c in range(3)], axis=2)
    clipped = int(np.count_nonzero((linear < 0) | (linear > 1)))
    encoded = np.rint(_encode_transfer(np.clip(linear, 0, 1)) * 255).astype(np.uint8)
    image = Image.fromarray(encoded)
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    created = False
    try:
        with dest.open('xb') as stream:
            created = True
            options = {'icc_profile': srgb}
            if fmt == 'JPEG': options.update(quality=95, subsampling=0)
            image.save(stream, format=fmt, **options)
        output = inspect_image(dest, require_srgb=True, require_upright=True, max_side=max_side)
    except Exception:
        if created: dest.unlink(missing_ok=True)
        raise
    provenance.update({'resize': 'linear-light LANCZOS float32 channel planes' if resized else 'none',
        'pre_resize_dimensions': original_size, 'clipped_output_channels': clipped,
        'quantization': 'one final rounded RGB8 quantization after color conversion/compositing/resize'})
    return {'schema': SCHEMA, 'source': facts, 'destination': str(dest), 'output': output,
            'conversion_provenance': provenance}


def to_srgb_backend_source(path, dest, *, declared_space=None, alpha_background=None) -> dict:
    """Write a full-canvas display-referred RGB16 sRGB TIFF for the backend.

    Already-developed input: the caller must not apply any RAW display-tone fit.
    No resize, HDR tone mapping or 8-bit intermediate; only final uint16 encoding.
    """
    source = Path(path).resolve(); dest = Path(dest).resolve()
    if dest == source or dest.exists(): raise ValueError('Destination must be new and must not overwrite source/output')
    if dest.suffix.lower() not in ('.tif', '.tiff'):
        raise ValueError('Backend source destination must be .tif/.tiff')
    linear, facts, provenance = _linear_source(source, declared_space, alpha_background)
    clipped = int(np.count_nonzero((linear < 0) | (linear > 1)))
    encoded = np.rint(_encode_transfer(np.clip(linear, 0, 1)) * 65535).astype(np.uint16)
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    tiff = _optional('tifffile'); created = False
    try:
        with dest.open('xb') as stream:
            created = True
            tiff.imwrite(stream, encoded, photometric='rgb', iccprofile=srgb,
                         metadata=None, extratags=[(274, 'H', 1, 1, False)])
        output = inspect_input(dest)
        if output['bits_per_sample'] != 16 or output['sample_dtype'] != 'uint16' or output['dimensions'] != facts['dimensions']:
            raise ValueError('Backend source readback precision/geometry mismatch')
        with tiff.TiffFile(dest) as readback:
            if not np.array_equal(readback.pages[0].asarray(), encoded):
                raise ValueError('Backend source TIFF sample readback mismatch')
            if bytes(readback.pages[0].tags[34675].value) != srgb:
                raise ValueError('Backend source TIFF ICC readback mismatch')
    except Exception:
        if created: dest.unlink(missing_ok=True)
        raise
    provenance.update({'resize': 'none', 'clipped_output_channels': clipped,
        'quantization': 'one final rounded RGB16 quantization after color conversion/compositing',
        'backend_interpretation': 'already-developed display-referred sRGB; do not apply RAW display-tone fit',
        'output_transfer': 'sRGB encoded, not linear', 'raw_display_tone_applied': False,
        'sample_readback_exact': True, 'icc_readback_exact': True})
    return {'schema': SCHEMA, 'source': facts, 'destination': str(dest), 'output': output,
            'conversion_provenance': provenance}
