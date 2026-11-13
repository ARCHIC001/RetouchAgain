"""Stage-only display derivatives; no RAW/settings/native IO or LAB scores.

The only filesystem effects are new PNGs inside folder. Grid coordinates and
black label/neutral line styling are metadata for a viewer, never burned into
photographic pixels. Source/target roles and independent geometry are preserved.
"""
import io
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms

from photo_image_contract import inspect_image, read_srgb
from photo_phase_scheduler import PHASES


_SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()


def stage_observation_metadata(phase, observation_id, grid=False):
    """Public metadata to place in observation['stage_observation']. No file IO."""
    if phase not in PHASES:
        raise ValueError('Unknown observation phase')
    if not isinstance(observation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', observation_id):
        raise ValueError('Observation ID must be a safe filename identifier')
    if type(grid) is not bool:
        raise ValueError('grid must be boolean')
    result = {'phase': phase, 'observation_id': observation_id,
              'view_mode': 'grayscale' if phase == 'tone' else 'color',
              'image_contract': 'upright-RGB8-sRGB-PNG-max512',
              'source_pixels_mutated': False, 'autolevel': False,
              'gray_method': 'linear-sRGB-Y-0.2126-0.7152-0.0722-reencoded-sRGB' if phase == 'tone' else None,
              'grid': None}
    if grid:
        result['grid'] = {'rows': 4, 'columns': 4, 'burned_into_pixels': False,
                          'line_rgb': [128, 128, 128], 'label_rgb': [0, 0, 0],
                          'coordinate_space': 'normalized_each_view_independently',
                          'registration_claim': False,
                          'cells': [{'id': chr(65 + row) + str(col + 1),
                                     'box': [col / 4, row / 4, (col + 1) / 4, (row + 1) / 4]}
                                    for row in range(4) for col in range(4)]}
    return result


def _grayscale(image):
    rgb = np.asarray(image, dtype=np.float64) / 255.0
    linear = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
    luminance = linear @ np.array([.2126, .7152, .0722])
    encoded = np.where(luminance <= .0031308, luminance * 12.92,
                       1.055 * luminance ** (1 / 2.4) - .055)
    gray = np.floor(np.clip(encoded, 0, 1) * 255 + .5).astype(np.uint8)
    return Image.fromarray(np.repeat(gray[:, :, None], 3, axis=2))


def derive_stage_views(views, phase, folder, observation_id, grid=False):
    """Return exactly two NEW (path, original_role) tuples; never overwrite sources.

    RGB8 JPEG/PNG inputs require real ICC declarations. The existing image contract
    validates decoding/declarations and applies orientation once, then LittleCMS
    actually converts to sRGB before fixed linear-luminance grayscale in tone.
    Other stages retain color (apart from required color conversion/downscaling).
    Invalid second inputs are detected before either output is written.
    """
    stage_observation_metadata(phase, observation_id, grid)
    if not isinstance(views, (list, tuple)) or len(views) != 2:
        raise ValueError('Exactly two input (path, role) views required')
    prepared, sources = [], []
    for view in views:
        if (not isinstance(view, (list, tuple)) or len(view) != 2
                or not isinstance(view[1], str) or not view[1].strip()):
            raise ValueError('Each view needs an explicit path and original photographic role')
        source = Path(view[0]).resolve()
        image = read_srgb(source, required=True)
        image.thumbnail((512, 512), Image.Resampling.LANCZOS)
        if phase == 'tone':
            image = _grayscale(image)
        # Build clean pixels so arbitrary source EXIF/text cannot leak orientation
        # or change declarations on the derived model attachment.
        clean = Image.frombytes('RGB', image.size, image.tobytes())
        buffer = io.BytesIO()
        clean.save(buffer, format='PNG', icc_profile=_SRGB)
        prepared.append((buffer.getvalue(), view[1]))
        sources.append(source)
    folder = Path(folder).resolve()
    destinations = [folder / (observation_id + '-stage-' + str(index) + '.png') for index in range(2)]
    if any(path in sources or path.exists() for path in destinations):
        raise FileExistsError('Stage derivative must be a new file, never an original or existing view')
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        for path, (data, _) in zip(destinations, prepared):
            with path.open('xb') as handle:
                written.append(path)
                handle.write(data)
            inspect_image(path, require_srgb=True, require_upright=True,
                          expected_format='PNG', max_side=512)
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return [(str(path), role) for path, (_, role) in zip(destinations, prepared)]
