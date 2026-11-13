"""Typed source preparation for actual RAW and already-developed image inputs."""
import json
import os
import subprocess
import sys
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parent))
from raw_render_contract import inspect_dng, CONTRACT

SCHEMA='managed-photo-source/v1'
ROOT=Path(__file__).resolve().parent.parent
CONVERTER=Path(os.environ.get('VISHARNESS_DNG_CONVERTER',str(ROOT/'external/adobe/Adobe DNG Converter.exe')))
RAW_EXTENSIONS={'.nef','.nrw','.cr2','.cr3','.arw','.srf','.sr2','.raf','.rw2','.orf','.pef','.srw','.3fr','.iiq','.mos','.kdc','.mrw','.x3f','.rwl','.dng'}
DISPLAY_EXTENSIONS={'.jpg','.jpeg','.png','.tif','.tiff'}


def _write(path,value):
    with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)


def _convert(source,folder,name,preview):
    folder.mkdir(parents=True,exist_ok=False)
    destination=folder/name
    command=[str(CONVERTER),'-u','-dng1.4',preview,'-d',str(folder),'-o',name,str(source)]
    result=subprocess.run(command,capture_output=True,timeout=300,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    _write(folder/'conversion.json',{'source':str(source),'destination':str(destination),'command':command,
        'exit_code':result.returncode,'stderr':result.stderr.decode('utf-8','replace')[-10000:],
        'stdout':result.stdout.decode('utf-8','replace')[-4000:],'hash_verification':False})
    if result.returncode or not destination.is_file():raise ValueError('No qualified full-RAW compatible conversion is available for this source')
    return destination


def classify_source(path):
    path=Path(path).resolve()
    if not path.is_file():raise ValueError('Source file missing')
    if path.suffix.lower() in RAW_EXTENSIONS:
        with path.open('rb') as stream:signature=stream.read(16)
        if signature.startswith((b'\xff\xd8',b'\x89PNG')):raise ValueError('RAW extension contains an already-developed image')
        return {'input_class':'raw','extension':path.suffix.lower(),'scope':'Decoder qualification required; extension is not proof of actual RAW decoding'}
    if path.suffix.lower() in DISPLAY_EXTENSIONS:
        from photo_input_contract import inspect_input
        # Do not decode blindly in classification: inspect_source handles explicit
        # untagged/alpha/linear declarations, then qualifies the actual content.
        with Image.open(path) as image:
            if image.format=='TIFF' and (image.getexif().get(50706) or image.getexif().get(262) in (32803,34892)):
                raise ValueError('Display-image extension contains DNG RAW data')
        return {'input_class':'developed','extension':path.suffix.lower()}
    raise ValueError('Unsupported source extension; no guessed decoder or colour space')


def prepare_source(path,output_dir,*,declared_space=None,alpha_background=None):
    source=Path(path).resolve();out=Path(output_dir).resolve()
    if out.exists():raise ValueError('Source preparation requires a new isolated directory')
    classification=classify_source(source);out.mkdir(parents=True)
    report={'schema':SCHEMA,'original_source':str(source),'classification':classification,
        'source_modified':False,'hash_verification':False,'paid_model_calls':0,'backend_decode_qualified':False}
    if classification['input_class']=='developed':
        from photo_input_contract import to_srgb_backend_source
        converted=to_srgb_backend_source(source,out/'managed-source.tif',declared_space=declared_space,alpha_background=alpha_background)
        report.update(backend_source=converted['destination'],source_kind='display-referred',
            raw_display_tone_applied=False,developed_input_contract=converted)
    else:
        if declared_space is not None or alpha_background is not None:
            raise ValueError('RAW colour/opacity comes from its typed metadata, not a display-space override')
        if source.suffix.lower()=='.dng':
            # First try the original only if the entire clean managed contract is
            # present; otherwise use independent conversion and clean XMP copies.
            try:
                facts=inspect_dng(source);destination=source
            except ValueError:
                intermediate=_convert(source,out/'uncompressed',source.stem+'.dng','-p0')
                import tifffile
                cleared=[]
                with tifffile.TiffFile(intermediate,mode='r+b') as tif:
                    def clean(page):
                        if 700 in page.tags:page.tags[700].overwrite(b'');cleared.append(page.offset)
                        if page.pages:
                            for child in page.pages:clean(child)
                    for page in tif.pages:clean(page)
                _write(out/'clean-derivation.json',{'source_original':str(source),'intermediate':str(intermediate),
                    'cleared':'XMP user development packet only on independent derivative; no author recipe disclosed',
                    'ifd_offsets':cleared,'source_modified':False,'hash_verification':False})
                destination=_convert(intermediate,out/'clean',source.stem+'.dng','-p2')
                try:facts=inspect_dng(destination)
                except ValueError as exc:
                    # Some very wide previews are tiled: preserve their data and
                    # derive one documented single-JPEG source reference.
                    if 'display reference' not in str(exc).lower():raise
                    from adapt_panorama_display_reference import adapt
                    adapted=out/'display-adapted'/(source.stem+'.dng');adapt(destination,adapted)
                    destination=adapted;facts=inspect_dng(destination)
        else:
            destination=_convert(source,out/'compatible-raw',source.stem+'.dng','-p2')
            facts=inspect_dng(destination)
        report.update(backend_source=str(destination),source_kind=facts['source_kind'],raw_contract=facts,
            raw_render_contract=CONTRACT,raw_display_tone_applied=True,
            note='Source preparation does not prove backend decode. Require kind=raw, previewOnly=None, full ordered frame and actual edit/export qualification.')
    _write(out/'source-input-contract.json',report)
    return report
