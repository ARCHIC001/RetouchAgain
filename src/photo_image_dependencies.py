"""Activate the approved fixed image dependency bundle, never install at runtime."""
from importlib import metadata
from pathlib import Path
import sys

VERSIONS = {'tifffile': '2026.10.15', 'imagecodecs': '2026.10.10'}


def activate_image_dependencies(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    bundle = root / 'external/python-image-deps-20261113'
    if not bundle.is_dir() or bundle.is_symlink():
        raise ValueError('Approved image dependency bundle unavailable; do not auto-install')
    for name, version in VERSIONS.items():
        if not (bundle / name / '__init__.py').is_file():
            raise ValueError('Approved dependency source missing: ' + name)
        found = {d.metadata['Name'].casefold().replace('-', '_'): d.version
                 for d in metadata.distributions(path=[str(bundle)])}
        if found.get(name) != version:
            raise ValueError('Approved dependency version mismatch: ' + name)
    if str(bundle) not in sys.path:
        sys.path.append(str(bundle))
    return {'path': str(bundle), 'versions': dict(VERSIONS), 'runtime_install': False}
