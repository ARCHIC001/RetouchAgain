"""Apply a DNG's actual independent opacity plane to display output, not a crop.

The full canvas is preserved. RGB/JPEG outputs use an explicit white sRGB matte,
composited in linear light. Undefined stitching pixels never become photo content.
"""
import io
import sys
import uuid
from pathlib import Path
import numpy as np
from PIL import Image, ImageCms
from photo_image_contract import inspect_image
from raw_render_contract import inspect_dng_geometry


def _tiff():
    import tifffile
    return tifffile


def opacity_plane(source):
    source=Path(source)
    if source.suffix.lower()!='.dng':return None,None
    tifffile=_tiff()
    with tifffile.TiffFile(source) as tif:
        masks=[]
        def walk(page):
            if int(page.photometric)==4 and (not page.tags.get(254) or int(page.tags[254].value)&4):masks.append(page)
            if page.pages:
                for child in page.pages:walk(child)
        for page in tif.pages:walk(page)
        if not masks:return None,None
        if len(masks)!=1:raise ValueError('Ambiguous independent DNG opacity planes')
        page=masks[0]
        geometry=inspect_dng_geometry(source)
        if [page.imagewidth,page.imagelength]!=geometry['raw_dimensions'] or page.samplesperpixel!=1 or page.bitspersample not in (8,16):
            raise ValueError('DNG opacity plane has unsupported size/channel/precision')
        alpha=page.asarray()
        left,top,width,height=geometry['crop_sensor_rectangle']
        alpha=alpha[top:top+height,left:left+width]
        orientation=geometry['orientation']
        if orientation==2:alpha=alpha[:,::-1]
        elif orientation==3:alpha=alpha[::-1,::-1]
        elif orientation==4:alpha=alpha[::-1]
        elif orientation==5:alpha=alpha.T
        elif orientation==6:alpha=np.rot90(alpha,3)
        elif orientation==7:alpha=alpha.T[::-1,::-1]
        elif orientation==8:alpha=np.rot90(alpha)
        return alpha,{'scope':'Actual independent DNG opacity plane; not a semantic/guessed mask',
            'source_ifd_offset':page.offset,'bits_per_sample':page.bitspersample,
            'upright_dimensions':[alpha.shape[1],alpha.shape[0]],'orientation':orientation,
            'matte_srgb':[1,1,1],'compositing':'linear-sRGB, explicit white display/JPEG background',
            'full_canvas_preserved':True,'hash_verification':False}


def apply_opacity(source,rendered,fmt):
    alpha,facts=opacity_plane(source)
    if alpha is None:return None
    rendered=Path(rendered)
    inspect_image(rendered,require_srgb=True,require_upright=True,expected_format='PNG' if fmt=='png' else 'JPEG')
    with Image.open(rendered) as image:
        image.load()
        if list(image.size)!=facts['upright_dimensions']:raise ValueError('Full render/opacity geometry mismatch')
        values=np.array(image,dtype=np.uint8)
    scale=float(np.iinfo(alpha.dtype).max)
    for y in range(0,values.shape[0],64):
        rgb=values[y:y+64].astype(np.float64)/255
        linear=np.where(rgb<=.04045,rgb/12.92,((rgb+.055)/1.055)**2.4)
        a=alpha[y:y+64].astype(np.float64)[...,None]/scale
        composed=linear*a+(1-a)
        encoded=np.where(composed<=.0031308,composed*12.92,1.055*np.maximum(composed,0)**(1/2.4)-.055)
        values[y:y+64]=np.rint(np.clip(encoded,0,1)*255).astype(np.uint8)
    temporary=rendered.with_name(rendered.name+'.opacity-'+uuid.uuid4().hex)
    profile=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    image=Image.fromarray(values)
    options={'icc_profile':profile}
    if fmt=='jpeg':options.update(quality=100,subsampling=0)
    image.save(temporary,format='PNG' if fmt=='png' else 'JPEG',**options)
    inspect_image(temporary,require_srgb=True,require_upright=True,expected_format='PNG' if fmt=='png' else 'JPEG')
    temporary.replace(rendered)
    return facts
