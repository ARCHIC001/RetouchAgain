"""Canonical display grid derived from the independent upright source canvas."""
from PIL import Image
from photo_image_contract import assert_scaled_geometry


def display_grid(source_dimensions,long_edge=512):
    width,height=source_dimensions
    if min(width,height,long_edge)<=0:raise ValueError('Invalid canonical display geometry')
    factor=min(1.,long_edge/max(width,height))
    # This grid is defined once from full upright dimensions, never recursively
    # rounded from an already resized preview. No image allocation is needed.
    return [max(1,int(width*factor+.5)),max(1,int(height*factor+.5))]


def canonical_preview(image,source_dimensions,long_edge=512):
    # Intermediate full-raster-derived images can have one-pixel aspect rounding.
    # Prove that independently, then sample both products onto a declared grid.
    observed=list(image.size)
    assert_scaled_geometry(source_dimensions,observed,max(observed))
    target=display_grid(source_dimensions,long_edge)
    natural=image.copy();natural.thumbnail((long_edge,long_edge),Image.Resampling.LANCZOS)
    if any(abs(a-b)>1 for a,b in zip(natural.size,target)):
        raise ValueError('Display geometry differs by more than predetermined one-pixel rounding')
    result=image.resize(tuple(target),Image.Resampling.LANCZOS) if list(image.size)!=target else image.copy()
    result.info.clear()
    return result,{'source_upright_dimensions':list(source_dimensions),'input_dimensions':observed,
        'natural_thumbnail_dimensions':list(natural.size),'canonical_display_dimensions':target,
        'one_pixel_rounding_normalised':list(natural.size)!=target,
        'rule':'Same independent source canvas -> one fixed display grid; aspect eligibility verified before resampling',
        'artistic_crop_or_rotation':False}
