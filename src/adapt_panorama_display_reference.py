"""Add a usable fixed display reference without rewriting LinearRaw pixels."""
import argparse
import gc
import json
import shutil
import sys
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parent))
import lab_timing_experiment as lab
import tifffile
import numpy as np


def adapt(source,destination):
    source=Path(source);destination=Path(destination)
    if destination.exists():raise ValueError('Existing adapted source must not be overwritten')
    with tifffile.TiffFile(source) as tif:
        candidates=[]
        def walk(page):
            if page.photometric in (2,6) and page.imagewidth>1024 and page.samplesperpixel==3:
                colour=page.tags.get(50970) or tif.pages[0].tags.get(50970)
                if not colour or colour.value!=2:raise ValueError('Source display reference is not explicitly sRGB')
                candidates.append(page)
            if page.pages:
                for sub in page.pages:walk(sub)
        for page in tif.pages:walk(page)
        reference=max(candidates,key=lambda p:p.imagewidth*p.imagelength)
        offset=reference.offset;dimensions=[reference.imagewidth,reference.imagelength]
        values=reference.asarray()
        if values.dtype!=np.uint8 or values.shape[-1]!=3:raise ValueError('Display reference is not RGB8')
        image=Image.fromarray(values);del values;gc.collect()
        orientation=int(tif.pages[0].tags.get(274).value) if tif.pages[0].tags.get(274) else 1
    image.thumbnail((4096,4096),Image.Resampling.LANCZOS)
    destination.parent.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(source,destination)
    application='Visharness source reference adapter'
    # A single JPEG strip is a display reference readable by the backend; the
    # full LinearRaw and opacity IFD byte ranges remain in the copied file.
    tifffile.imwrite(destination,np.asarray(image),append='force',photometric='rgb',compression='jpeg',
        compressionargs={'level':100},rowsperstrip=image.height,metadata=None,
        extratags=[(254,'I',1,1,False),(274,'H',1,orientation,False),(50970,'I',1,2,False),
            (50966,'s',0,application,False),(50967,'s',0,'1',False)],software=application)
    provenance={'schema':'derived-source-display-reference/v1','source':str(source),'destination':str(destination),
        'source_reference_ifd':offset,'source_reference_dimensions':dimensions,'derived_reference_dimensions':list(image.size),
        'resampling':'Pillow LANCZOS, aspect preserving; no exposure, tone, crop or pixel replacement of LinearRaw data',
        'source_colour':'DNG PreviewColorSpace=2 (sRGB)','role':'Fixed source display reference only, not editable RAW samples or author target',
        'appended_single_jpeg_strip':True,'original_linearraw_byte_ranges_preserved':True,
        'source_modified':False,'hash_verification':False,'paid_model_calls':0}
    lab.write(destination.parent/'source-display-derivation.json',provenance)
    print(json.dumps(provenance,ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source');parser.add_argument('destination');args=parser.parse_args();adapt(args.source,args.destination)
