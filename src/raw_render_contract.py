"""Fail-closed CFA/LinearRaw metadata and fixed source-display reference.

Metadata inspection never decodes RAW pixels, reads digest values, or changes the
source. The display reference is not editable pixels or an author target.
"""
import io
import math
import re
import struct
import warnings
from fractions import Fraction
from pathlib import Path
from PIL import Image
from dsh_photo_color import validate as validate_srgb_profile
from photo_image_contract import normalize_orientation, resolved_orientation

CONTRACT='raw-managed-source/v3'
# Metadata admission is not a claim that this backend decodes every codec/sample.
LINEAR_COMPRESSIONS={1:'uncompressed',7:'JPEG',8:'Deflate',34892:'lossy JPEG',52546:'JPEG XL'}
MAX_IFDS=128
MAX_DEPTH=10
MAX_ENTRIES=4096
MAX_TOTAL_ENTRIES=16384
MAX_METADATA_BYTES=16*1024*1024
MAX_REFERENCE_BYTES=128*1024*1024
MAX_PIXELS=150_000_000
MAX_SIDE=100_000
RAW_TYPES={32803:'CFA',34892:'LinearRaw'}
CAMERA_TAGS={271:'Make',272:'Model',305:'Software',50708:'UniqueCameraModel',
    50721:'ColorMatrix1',50722:'ColorMatrix2',50723:'CameraCalibration1',50724:'CameraCalibration2',
    50727:'AnalogBalance',50728:'AsShotNeutral',50729:'AsShotWhiteXY',50730:'BaselineExposure',
    50778:'CalibrationIlluminant1',50779:'CalibrationIlluminant2',50964:'ForwardMatrix1',50965:'ForwardMatrix2'}
# Explicit whitelist: digest fields (including RawImageDigest/NewRawImageDigest)
# are not read, decoded, compared or copied, even if their offsets are malformed.
READ_TAGS={254,256,257,258,259,262,273,274,277,278,279,284,322,323,324,325,
    330,338,339,34665,34853,40965,700,33421,33422,50706,50710,50711,50713,50714,
    50715,50716,50717,50718,50719,50720,50829,50940,50966,50967,50970,50975,
    51125,52547}|set(CAMERA_TAGS)
TYPE_FORMAT={1:('B',1),2:('s',1),3:('H',2),4:('I',4),5:('II',8),
    6:('b',1),7:('B',1),8:('h',2),9:('i',4),10:('ii',8),
    11:('f',4),12:('d',8),13:('I',4),16:('Q',8),17:('q',8),18:('Q',8)}


def _values(value):
    return list(value) if isinstance(value,(tuple,list,bytes)) else [value]


def _numbers(value,name,count=None,lo=None,hi=None,integer=False):
    values=_values(value)
    if count is not None and len(values)!=count:raise ValueError(name+': wrong value count')
    result=[]
    for value in values:
        if isinstance(value,bool) or not isinstance(value,(int,float,Fraction)) or not math.isfinite(value):
            raise ValueError(name+': non-finite or non-numeric value')
        if lo is not None and value<lo or hi is not None and value>hi:
            raise ValueError(name+': value out of range')
        if integer and value!=int(value):raise ValueError(name+': integer required')
        result.append(int(value) if integer else value)
    return result


def _integer(value,name,lo=0,hi=2**32-1):
    return _numbers(value,name,1,lo,hi,True)[0]


def _dimensions(tags):
    width=_integer(tags.get(256,0),'ImageWidth',1,MAX_SIDE)
    height=_integer(tags.get(257,0),'ImageLength',1,MAX_SIDE)
    if width*height>MAX_PIXELS:raise ValueError('DNG dimensions exceed pixel capacity')
    return width,height


def _read_ifds(path,include_clean_flags=True):
    """Bounded classic-TIFF metadata walk, DFS children then main-chain next.

    Backend reader ignores next links on child IFDs. We still audit those chains
    but mark them unreachable, refusing hidden image IFDs instead of selecting a
    different raw than the backend. BigTIFF is deliberately not admitted yet.
    """
    size=path.stat().st_size
    records=[];visited=set();budget=0;entries_total=0
    with path.open('rb') as stream:
        def read_at(offset,count):
            if offset<0 or count<0 or offset+count>size:raise ValueError('TIFF metadata/strip outside file')
            stream.seek(offset);data=stream.read(count)
            if len(data)!=count:raise ValueError('Truncated TIFF metadata')
            return data
        header=read_at(0,8)
        if header[:2] not in (b'II',b'MM'):raise ValueError('DNG is not TIFF')
        endian='<' if header[:2]==b'II' else '>'
        if struct.unpack(endian+'H',header[2:4])[0]!=42:raise ValueError('Unsupported DNG TIFF container (require classic TIFF)')
        first=struct.unpack(endian+'I',header[4:8])[0]
        def walk(offset,depth,parent=None,relation='main',reachable=True):
            nonlocal budget,entries_total
            while offset:
                if depth>MAX_DEPTH or len(records)>=MAX_IFDS:raise ValueError('TIFF IFD traversal limit exceeded')
                if offset in visited:raise ValueError('TIFF IFD cycle or repeated directory offset')
                if offset<8:raise ValueError('Invalid TIFF IFD offset')
                visited.add(offset)
                n=struct.unpack(endian+'H',read_at(offset,2))[0]
                entries_total+=n
                if not n or n>MAX_ENTRIES or entries_total>MAX_TOTAL_ENTRIES:raise ValueError('TIFF IFD entry limit exceeded')
                table=read_at(offset+2,n*12+4);tags={}
                for pos in range(0,n*12,12):
                    tag,typ,count=struct.unpack(endian+'HHI',table[pos:pos+8])
                    if tag not in READ_TAGS or tag==700 and not include_clean_flags:continue
                    if tag in tags:raise ValueError('Duplicate contracted TIFF tag')
                    if typ not in TYPE_FORMAT or not count:raise ValueError('Unsupported contracted TIFF value type/count')
                    fmt,unit=TYPE_FORMAT[typ];length=count*unit
                    if typ!=2 and tag!=700 and count>200_000:raise ValueError('TIFF numeric value count limit exceeded')
                    budget+=length
                    if budget>MAX_METADATA_BYTES:raise ValueError('TIFF metadata byte budget exceeded')
                    raw=table[pos+8:pos+8+length] if length<=4 else read_at(struct.unpack(endian+'I',table[pos+8:pos+12])[0],length)
                    if typ==2:value=raw.rstrip(b'\0').decode('utf-8','strict')
                    elif typ in (1,7):value=raw
                    elif typ in (5,10):
                        values=[]
                        for start in range(0,length,8):
                            num,den=struct.unpack(endian+fmt,raw[start:start+8])
                            if den==0:raise ValueError('Zero denominator in DNG rational')
                            values.append(Fraction(num,den))
                        value=tuple(values)
                    else:value=struct.unpack(endian+fmt*count,raw)
                    tags[tag]=value
                record={'offset':offset,'index':len(records),'parent_offset':parent,
                    'relation':relation,'backend_reachable':reachable,'tags':tags}
                records.append(record)
                children=_numbers(tags.get(330,()),'SubIFDs',lo=8,hi=size-2,integer=True)
                for child in children:walk(child,depth+1,offset,'subifd',reachable)
                for pointer in (34665,34853,40965):
                    if pointer in tags:walk(_integer(tags[pointer],'IFD pointer',8,size-2),depth+1,offset,str(pointer),reachable)
                next_offset=struct.unpack(endian+'I',table[n*12:n*12+4])[0]
                if not next_offset:break
                # Only the root's next chain participates in backend all_ifds().
                if depth:reachable=False
                offset=next_offset;relation='next'
        walk(first,0)
    if not records:raise ValueError('DNG has no IFDs')
    backend_index=0
    for record in records:
        if not record['backend_reachable'] and 262 in record['tags']:
            raise ValueError('Image on child next-IFD chain is unsupported by backend; blocked')
        if record['backend_reachable']:
            record['index']=backend_index;backend_index+=1
        else:record['index']=None
    return records


def _half_up(value):
    """Rust f64::round for validated nonnegative coordinates, never bankers' round."""
    exact=value if isinstance(value,Fraction) else Fraction(str(value))
    return (2*exact.numerator+exact.denominator)//(2*exact.denominator)


def _plain(values):return [int(v) if v==int(v) else float(v) for v in values]


def _camera_tags(primary,raw):
    result={}
    for tag,name in CAMERA_TAGS.items():
        value=raw.get(tag,primary.get(tag))
        if value is None:continue
        if tag in (271,272,305,50708):
            if not isinstance(value,str) or len(value)>4096:raise ValueError('Invalid camera text tag '+name)
            result[name]=value;continue
        count=9 if 'Matrix' in name or 'Calibration' in name and tag in (50723,50724) else None
        values=_numbers(value,name,count,lo=-1000,hi=1000)
        if tag in (50727,50728):values=_numbers(value,name,3,lo=1e-9,hi=1000)
        if tag==50729:values=_numbers(value,name,2,lo=0,hi=1)
        if tag==50730:values=_numbers(value,name,1,lo=-32,hi=32)
        if count==9:
            a,b,c,d,e,f,g,h,i=values
            if abs(a*(e*i-f*h)-b*(d*i-f*g)+c*(d*h-e*g))<=1e-12:raise ValueError('Singular camera matrix '+name)
        result[name]=_plain(values)
    if not any(name in result for name in ('ColorMatrix1','ColorMatrix2')) or 'AsShotNeutral' not in result:
        raise ValueError('Missing contracted DNG camera matrix/AsShotNeutral')
    return result


def _sample_layout(raw,kind):
    channels=_integer(raw.get(277,1),'SamplesPerPixel',1,4)
    bits=_numbers(raw.get(258,()),'BitsPerSample',channels,1,64,True)
    formats=_numbers(raw.get(339,(1,)*channels),'SampleFormat',channels,1,3,True)
    if len(set(bits))!=1 or len(set(formats))!=1:raise ValueError('Mixed RAW sample types are unsupported')
    planar=_integer(raw.get(284,1),'PlanarConfiguration',1,2)
    if planar!=1:raise ValueError('Planar RAW storage unsupported; require interleaved samples')
    if 338 in raw:raise ValueError('ExtraSamples/inline alpha unsupported; alpha requires an independent IFD')
    compression=_integer(raw.get(259,1),'Compression',1,65535)
    if kind=='CFA':
        if compression!=1 or channels!=1 or bits!=[16] or formats!=[1]:
            raise ValueError('Selected backend IFD requires full uncompressed unsigned 16-bit CFA')
    else:
        if channels!=3:raise ValueError('LinearRaw requires three camera-colour samples; four-channel/inline alpha unqualified')
        if not (formats[0]==1 and bits[0]==16 or formats[0]==3 and bits[0] in (16,24,32)):
            raise ValueError('Unsupported LinearRaw SampleFormat/BitsPerSample')
        if compression not in LINEAR_COMPRESSIONS:raise ValueError('Unsupported LinearRaw compression metadata')
    return {'samples_per_pixel':channels,'bits_per_sample':bits,'sample_format':formats,
        'sample_type':'IEEE floating point' if formats[0]==3 else 'unsigned integer',
        'planar_configuration':planar,'compression':compression,
        'compression_name':LINEAR_COMPRESSIONS.get(compression,'uncompressed'),
        'demosaic_required':kind=='CFA','raw_pixel_decode_performed':False,
        'decode_qualification':'required separately on this exact selected IFD',
        'floating_sample_scope':('Metadata only: NaN/Inf/negative/over-range pixels are not scanned; backend may clamp invalid samples'
            if formats[0]==3 else 'Unsigned sample storage; no RAW sample values inspected')}


def geometry_from_directories(records):
    """Select exactly the backend RAW IFD; CFA and LinearRaw share ordered geometry.

    Metadata qualification never substitutes for actual backend full RAW decode.
    An unsupported winning IFD blocks; a smaller CFA/LinearRaw is no fallback.
    """
    if not records:raise ValueError('DNG has no IFDs')
    primary=records[0]['tags'];candidates=[]
    for record in records:
        tags=record['tags']
        if _integer(tags.get(254,0),'NewSubfileType')==0 and _integer(tags.get(262,0),'PhotometricInterpretation') in RAW_TYPES:
            width,height=_dimensions(tags)
            if not record.get('backend_reachable',True):raise ValueError('Hidden RAW IFD unsupported by backend')
            candidates.append((width*height,record))
    if not candidates:raise ValueError('Missing full-resolution CFA/LinearRaw IFD')
    # Rust Iterator::max_by_key returns the last maximum, not Python's first.
    selected=max(enumerate(candidates),key=lambda item:(item[1][0],item[0]))[1][1]
    raw=selected['tags'];width,height=_dimensions(raw)
    kind=RAW_TYPES[_integer(raw[262],'PhotometricInterpretation')]
    sample_layout=_sample_layout(raw,kind)
    orientation=_integer(primary.get(274,1),'Orientation',1,8)
    if 274 in raw and _integer(raw[274],'RAW Orientation',1,8)!=orientation:raise ValueError('Primary/RAW orientation conflict')
    active=_numbers(raw.get(50829,(0,0,height,width)),'ActiveArea',4,lo=0,integer=True)
    top,left,bottom,right=active
    if not (top<bottom<=height and left<right<=width):raise ValueError('ActiveArea outside RAW frame')
    aw,ah=right-left,bottom-top
    if (50719 in raw)!=(50720 in raw):raise ValueError('DefaultCropOrigin/Size must be paired')
    origin=_numbers(raw.get(50719,(0,0)),'DefaultCropOrigin',2,lo=0,hi=MAX_SIDE)
    crop=_numbers(raw.get(50720,(aw,ah)),'DefaultCropSize',2,lo=1,hi=MAX_SIDE)
    # Check the continuous DNG crop first. A rounding allowance is NOT a frame
    # allowance: even 0.01 pixel outside the active image is rejected.
    if origin[0]+crop[0]>aw or origin[1]+crop[1]>ah:raise ValueError('Default crop outside ActiveArea')
    x,y=map(_half_up,origin);cw,ch=map(_half_up,crop)
    clipped=[min(cw,aw-x),min(ch,ah-y)]
    if min(clipped)<=0:raise ValueError('Empty developed crop after rounding')
    scale=_numbers(raw.get(50718,(1,1)),'DefaultScale',2,lo=1e-9,hi=1000)
    if scale!=[1,1]:raise ValueError('Unsupported DefaultScale; blocked rather than changing frame')
    user=_numbers(raw.get(51125,(0,0,1,1)),'DefaultUserCrop',4,lo=0,hi=1)
    if user!=[0,0,1,1]:raise ValueError('Unsupported DefaultUserCrop; blocked rather than changing frame')
    for tag,name in ((50975,'RowInterleaveFactor'),(52547,'ColumnInterleaveFactor')):
        if _integer(raw.get(tag,1),name)!=1:raise ValueError('Unsupported '+name)
    cfa=None
    if kind=='CFA':
        if _integer(raw.get(50711,1),'CFALayout')!=1:raise ValueError('Unsupported CFALayout')
        rows,cols=_numbers(raw.get(33421,()),'CFARepeatPatternDim',2,1,16,True)
        pattern=_numbers(raw.get(33422,()),'CFAPattern',rows*cols,0,2,True)
        planes=_numbers(raw.get(50710,(0,1,2)),'CFAPlaneColor',3,0,2,True)
        if len(set(planes))!=3:raise ValueError('Unsupported CFAPlaneColor')
        if any(tags.get(305)=='LightCraft' for tags in (raw,primary)):raise ValueError('Legacy sensor-origin CFA anchor unsupported')
        sensor_pattern=[pattern[((r-top)%rows)*cols+(c-left)%cols] for r in range(rows) for c in range(cols)]
        cfa={'repeat_dimensions':[rows,cols],'pattern':pattern,'plane_color':planes,
            'anchor':'ActiveArea top-left','anchor_sensor_xy':[left,top],'sensor_origin_pattern':sensor_pattern}
    channels=sample_layout['samples_per_pixel'];floating=sample_layout['sample_format'][0]==3
    limit=3.4028234663852886e38 if floating else 65535
    br,bc=_numbers(raw.get(50713,(1,1)),'BlackLevelRepeatDim',2,1,16,True)
    black=_numbers(raw.get(50714,(0,)*(br*bc*channels)),'BlackLevel',br*bc*channels,-limit if floating else 0,limit)
    white=_numbers(raw.get(50717,((1 if floating else 65535),)*channels),'WhiteLevel',channels,1e-30,limit)
    if any(black[i]>=white[i%channels] for i in range(len(black))):raise ValueError('BlackLevel must be below channel WhiteLevel')
    deltas={}
    for tag,name,length in ((50715,'BlackLevelDeltaH',aw),(50716,'BlackLevelDeltaV',ah)):
        if tag in raw:deltas[name]=_plain(_numbers(raw[tag],name,length,-limit,limit))
    camera=_camera_tags(primary,raw)
    expected=list(reversed(clipped)) if orientation in (5,6,7,8) else list(clipped)
    result={'status':'qualified','qualification_scope':'bounded metadata only; backend full decode still required',
        'source_kind':kind,'sample_layout':sample_layout,'demosaic_required':kind=='CFA',
        'selected_ifd_offset':selected['offset'],'selected_ifd_index':selected['index'],
        'selected_ifd_type':kind,'selection_rule':'backend DFS, NewSubfileType=0, largest CFA/LinearRaw; last equal-area IFD',
        'raw_dimensions':[width,height],'orientation':orientation,'orientation_source':'IFD0',
        'orientation_swaps_axes':orientation in (5,6,7,8),'active_area':active,
        'default_crop_origin':_plain(origin),'default_crop_size':_plain(crop),
        'rounded_crop_origin':[x,y],'rounded_crop_size':[cw,ch],'clipped_crop_size':clipped,
        'crop_sensor_rectangle':[left+x,top+y,*clipped],
        'default_scale':_plain(scale),'default_user_crop':_plain(user),
        'rounding':{'rule':'nonnegative half-up per origin/size, then clip to ActiveArea',
            'clip_pixels':[cw-clipped[0],ch-clipped[1]],'continuous_crop_in_bounds':True,
            'frame_tolerance_pixels':0,'dimension_tolerance_pixels':0,
            'note':'Only predetermined rounding can lose one pixel at the active edge; no output +/-1 permission'},
        'developed_dimensions_before_orientation':clipped,'expected_developed_dimensions':expected,
        'black_level_repeat_dimensions':[br,bc],'black_level':_plain(black),
        'white_level':int(white[0]) if kind=='CFA' else _plain(white),
        'white_levels':_plain(white),'black_level_deltas':deltas,'camera_tags':camera,'ifd_count':len(records),
        'raw_pixel_decode_performed':False,'hash_verification':False}
    if cfa is not None:result['cfa']=cfa
    return result


def inspect_dng_geometry(path):
    """Read only bounded whitelisted DNG metadata; no RAW/preview pixel decode."""
    path=Path(path);records=_read_ifds(path,include_clean_flags=False)
    geometry=geometry_from_directories(records)
    raw=next(r['tags'] for r in records if r['offset']==geometry['selected_ifd_offset'])
    # A geometry-only file can omit storage tags (synthetic metadata fixtures).
    # If a real layout is present, audit bounds without opening pixel payloads.
    if any(tag in raw for tag in (273,279,324,325)):
        geometry['raw_storage']=_validate_raw_storage(path,raw,geometry)
    return geometry


def _check_preview_image(image):
    profile=image.info.get('icc_profile')
    if profile:validate_srgb_profile(profile)
    exif=image.getexif();colour=exif.get_ifd(34665).get(40961)
    if colour not in (None,1,65535):raise ValueError('Embedded JPEG EXIF conflicts with declared sRGB reference')
    interop=exif.get_ifd(40965)
    if interop.get(1) in ('R03',b'R03',b'R03\0'):raise ValueError('Embedded JPEG declares AdobeRGB, not the contracted sRGB')


def _decode_reference(data,container_orientation):
    """Full verify/load with optional ICC (DNG PreviewColorSpace declares sRGB)."""
    if not data or len(data)>MAX_REFERENCE_BYTES:raise ValueError('Missing or oversized display reference')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as preview:
                fmt=preview.format;stored=list(preview.size)
                if fmt not in ('JPEG','PNG') or preview.mode!='RGB':raise ValueError('Display reference requires RGB JPEG/PNG')
                if preview.width*preview.height>MAX_PIXELS:raise ValueError('Display reference exceeds pixel capacity')
                preview.verify()
            if fmt=='JPEG' and not data.endswith(b'\xff\xd9'):raise ValueError('Truncated display reference JPEG')
            if fmt=='PNG' and data[-12:-4]!=b'\0\0\0\0IEND':raise ValueError('Truncated display reference PNG')
            with Image.open(io.BytesIO(data)) as preview:
                preview.load();orientation=resolved_orientation(preview,container_orientation)
                _check_preview_image(preview)
                reference=normalize_orientation(preview,container_orientation).convert('RGB')
        return reference,{'stored_dimensions':stored,'dimensions':list(reference.size),
            'orientation':orientation,'pixel_decode_complete':True,'format':fmt}
    except (OSError,SyntaxError) as exc:raise ValueError('Invalid display reference pixels') from exc


def _storage(path,tags):
    positions=tags.get(273,tags.get(324,()))
    counts=tags.get(279,tags.get(325,()))
    positions=_numbers(positions,'Pixel offsets',lo=8,integer=True)
    counts=_numbers(counts,'Pixel byte counts',lo=1,integer=True)
    if not positions or len(positions)!=len(counts):raise ValueError('Missing/mismatched DNG pixel layout')
    size=path.stat().st_size
    if any(p+n>size for p,n in zip(positions,counts)):raise ValueError('DNG pixel strip outside file')
    return positions,counts


def _validate_raw_storage(path,tags,geometry):
    positions,counts=_storage(path,tags)
    width,height=geometry['raw_dimensions'];layout=geometry['sample_layout']
    uncompressed=layout['compression']==1
    bits_per_pixel=sum(layout['bits_per_sample'])
    if 324 in tags or 325 in tags:
        if 273 in tags or 279 in tags:raise ValueError('Ambiguous strip/tile RAW layout')
        tw=_integer(tags.get(322,0),'TileWidth',1,MAX_SIDE)
        th=_integer(tags.get(323,0),'TileLength',1,MAX_SIDE)
        expected_count=math.ceil(width/tw)*math.ceil(height/th)
        if len(counts)!=expected_count:raise ValueError('RAW tile count differs from declared dimensions')
        if uncompressed and any(count!=math.ceil(tw*bits_per_pixel/8)*th for count in counts):
            raise ValueError('Uncompressed RAW tile byte count differs from declared dimensions/channels/bit depth')
        storage='tiles'
    else:
        rows=_integer(tags.get(278,height),'RowsPerStrip',1,MAX_SIDE)
        if len(counts)!=math.ceil(height/rows):raise ValueError('RAW strip count differs from declared dimensions')
        expected=[math.ceil(width*bits_per_pixel/8)*min(rows,height-y) for y in range(0,height,rows)]
        if uncompressed and counts!=expected:
            raise ValueError('Uncompressed RAW strip byte count differs from declared dimensions/channels/bit depth')
        storage='strips'
    intervals=sorted(zip(positions,counts))
    if any(p+n>q for (p,n),(q,_) in zip(intervals,intervals[1:])):
        raise ValueError('Overlapping RAW pixel strips/tiles')
    return {'layout':storage,'segment_count':len(counts),'bounds_and_nonoverlap_verified':True,
        'uncompressed_sample_byte_counts_verified':uncompressed,
        'compressed_byte_count_formula_applied':False,'raw_pixel_decode_performed':False,
        'qualification_scope':'metadata layout only; compressed streams and floating sample validity require actual decode'}


def _reference_geometry(preview,geometry):
    """Classify shape without resizing or granting any candidate crop permission."""
    actual=preview;expected=geometry['expected_developed_dimensions']
    raw=geometry['raw_dimensions'];active=geometry['active_area']
    aw,ah=active[3]-active[1],active[2]-active[0]
    if geometry['orientation_swaps_axes']:raw=list(reversed(raw));aw,ah=ah,aw
    def shape_match(frame):
        factor=min(actual[0]/frame[0],actual[1]/frame[1])
        return all(a in {math.floor(v*factor),_half_up(v*factor),math.ceil(v*factor)} for a,v in zip(actual,frame))
    if shape_match(expected):status='default_crop_resize_explained';basis=expected
    elif shape_match([aw,ah]):status='active_area_resize_explained';basis=[aw,ah]
    elif shape_match(raw):status='full_sensor_resize_explained';basis=raw
    else:status='source_fit_only';basis=None
    # This is the backend tone-fit eligibility, not output-frame tolerance.
    fit_error=abs(actual[0]/actual[1]/(expected[0]/expected[1])-1)
    if fit_error>.02:raise ValueError('Source display reference aspect exceeds backend 2% fit eligibility')
    return {'status':status,'reference_dimensions':actual,'explained_by_dimensions':basis,
        'source_tone_fit_aspect_error':fit_error,'source_tone_fit_limit':.02,
        'candidate_crop_permission':False,'reference_resized':False}


def _clean_develop_flags(records):
    """Read only the two admission flags; never expose/interpret an author recipe."""
    found={name:[] for name in ('HasSettings','HasCrop')}
    for record in records:
        if 700 not in record['tags']:continue
        xml=record['tags'][700]
        xml=xml.decode('utf-8','strict') if isinstance(xml,bytes) else str(xml)
        for name in found:
            found[name].extend(re.findall(r'crs:'+name+r'\s*=\s*[\"\']([^\"\']+)',xml))
            found[name].extend(re.findall(r'<crs:'+name+r'\s*>\s*([^<]+)\s*</crs:'+name+r'\s*>',xml))
    if any(not values or any(value.strip().lower()!='false' for value in values) for values in found.values()):
        raise ValueError('DNG display reference has unverified prior develop settings/crop')
    return {'HasSettings':False,'HasCrop':False,'source':'explicit false flags in this DNG XMP; no external provenance override'}


def _profile_tone_curve(primary,raw):
    value=raw.get(50940,primary.get(50940))
    if value is None:return None
    values=_numbers(value,'ProfileToneCurve',lo=0,hi=1)
    if len(values)<4 or len(values)%2 or len(values)>8192:raise ValueError('Invalid ProfileToneCurve point count')
    points=list(zip(values[::2],values[1::2]))
    if points[0]!=(0,0) or points[-1]!=(1,1):raise ValueError('ProfileToneCurve requires normalized endpoints')
    if any(x>=nx or y>ny for (x,y),(nx,ny) in zip(points,points[1:])):
        raise ValueError('ProfileToneCurve must have increasing x and nondecreasing y')
    return {'type':'dng-profile-tone-curve','tag':50940,'points':[_plain(p) for p in points],
        'source':'this DNG source profile, not author develop XMP','metadata_validated':True,
        'backend_application_verified':False}


def _inspect(path,with_pixels):
    path=Path(path);records=_read_ifds(path);geometry=geometry_from_directories(records)
    primary=records[0]['tags'];raw=next(r['tags'] for r in records if r['offset']==geometry['selected_ifd_offset'])
    clean_flags=_clean_develop_flags(records)
    curve=_profile_tone_curve(primary,raw)
    storage=_validate_raw_storage(path,raw,geometry)
    geometry['raw_storage']=storage
    minimum=max(384,math.ceil(.1*max(geometry['developed_dimensions_before_orientation'])))
    previews=[];images=[];unselected=[]
    for record in records:
        directory=record['tags']
        if not record['backend_reachable'] or 262 not in directory:continue
        photo=_integer(directory[262],'PhotometricInterpretation')
        # LinearRaw and its reduced copies are never display pixels. DNG opacity
        # masks (Photometric=4/NewSubfileType bit 2) are independent, not RGB refs.
        if photo in RAW_TYPES or photo==4 or _integer(directory.get(254,0),'NewSubfileType')&4:continue
        if photo not in (2,6):continue
        if max(_dimensions(directory))<minimum:continue
        colour=_integer(directory.get(50970,primary.get(50970,0)),'PreviewColorSpace')
        if colour!=2:raise ValueError('RAW display reference must explicitly declare DNG PreviewColorSpace=2 (sRGB)')
        positions,counts=_storage(path,directory)
        if _integer(directory.get(259,1),'Preview Compression')!=7 or len(positions)!=1:
            unselected.append({'ifd_offset':record['offset'],'dimensions':list(_dimensions(directory)),
                'reason':'Tiled/non-single-JPEG reference retained but not usable by current backend tone fit'})
            continue
        if counts[0]>MAX_REFERENCE_BYTES:raise ValueError('Oversized display reference')
        container_orientation=_integer(directory.get(274,geometry['orientation']),'Preview Orientation',1,8)
        if container_orientation!=geometry['orientation']:raise ValueError('Preview IFD/RAW orientation conflict')
        with path.open('rb') as stream:stream.seek(positions[0]);data=stream.read(counts[0])
        image,decoded=_decode_reference(data,container_orientation)
        if decoded['stored_dimensions']!=list(_dimensions(directory)):raise ValueError('DNG preview dimensions differ from its declared IFD')
        if decoded['format']!='JPEG':raise ValueError('DNG JPEG strip contains a non-JPEG display reference')
        reference_geometry=_reference_geometry(list(image.size),geometry)
        application=directory.get(50966,primary.get(50966,''))
        if application!='Adobe DNG Converter':
            if application!='Visharness source reference adapter':
                raise ValueError('Display reference has no contracted rendering provenance')
            import json
            provenance_file=path.parent/'source-display-derivation.json'
            if not provenance_file.is_file():raise ValueError('Derived display reference provenance missing')
            provenance=json.loads(provenance_file.read_text(encoding='utf-8'))
            if (provenance.get('schema')!='derived-source-display-reference/v1' or
                Path(provenance.get('destination','')).resolve()!=path.resolve() or
                provenance.get('derived_reference_dimensions')!=decoded['stored_dimensions'] or
                provenance.get('source_colour')!='DNG PreviewColorSpace=2 (sRGB)' or
                provenance.get('source_modified') is not False or
                provenance.get('original_linearraw_byte_ranges_preserved') is not True):
                raise ValueError('Derived display reference provenance does not match this source')
        previews.append({'dimensions':decoded['stored_dimensions'],'oriented_dimensions':list(image.size),
            'colour_space':'sRGB','preview_colour_space_tag':colour,'application':application,
            'application_version':directory.get(50967,primary.get(50967,'')),
            'ifd_offset':record['offset'],'ifd_index':record['index'],'orientation':container_orientation,
            'pixel_decode_complete':True,'reference_geometry':reference_geometry})
        if with_pixels:images.append(image)
    if not previews and curve is None:
        raise ValueError('Missing qualified source display reference; coarse thumbnail cannot define RAW starting tone')
    best=max(range(len(previews)),key=lambda i:math.prod(previews[i]['dimensions'])) if previews else None
    if with_pixels and best is None:raise ValueError('ProfileToneCurve defines source display but provides no reference image pixels')
    display={'type':'clean-adobe-srgb-reference' if best is not None else 'dng-profile-tone-curve',
        'profile_tone_curve':curve,'reference_ifd_index':previews[best]['ifd_index'] if best is not None else None,
        'clean_provenance':clean_flags,'backend_application_verified':False}
    prerequisites={'geometry':{'type':'ordered-selected-ifd-geometry','metadata_validated':True},
        'source_colour':{'type':'dng-camera-matrix-and-as-shot-neutral','metadata_validated':True},
        'sample_storage':{'type':'raw-sample-storage','metadata_validated':True,**storage},
        'source_display':display,
        'backend_decode':{'type':'full-cfa-decode' if geometry['source_kind']=='CFA' else 'full-linear-camera-rgb-decode',
            'required':True,'verified':False,'demosaic_required':geometry['demosaic_required'],
            'selected_ifd_index':geometry['selected_ifd_index']}}
    facts={'schema':CONTRACT,'source_kind':geometry['source_kind'],'raw_dimensions':geometry['raw_dimensions'],
        'sample_layout':geometry['sample_layout'],'prerequisites':prerequisites,'raw_storage':storage,
        'source_display':display,
        'default_crop_dimensions':geometry['developed_dimensions_before_orientation'],
        'black_level':geometry['black_level'],'white_level':geometry['white_level'],
        'selected_ifd_offset':geometry['selected_ifd_offset'],'selected_ifd_index':geometry['selected_ifd_index'],
        'selected_ifd_type':geometry['selected_ifd_type'],'orientation':geometry['orientation'],
        'expected_developed_dimensions':geometry['expected_developed_dimensions'],'geometry':geometry,
        'reference_geometry':previews[best]['reference_geometry'] if best is not None else None,
        'display_reference_minimum_long_edge':minimum,'display_references':previews,'unselected_display_references':unselected,
        'clean_develop_flags':clean_flags,
        'render_intent':('Full CFA decode' if geometry['source_kind']=='CFA' else 'Full LinearRaw camera-RGB decode without repeat demosaicing')+
            ', DNG camera colour and baseline exposure, fixed source display transform, then user edits and tagged sRGB output',
        'embedded_preview_role':'Source view-transform reference only, never editable pixels or author target',
        'raw_pixel_decode_performed':False,'hash_verification':False}
    if geometry['source_kind']=='CFA':facts['cfa_dimensions']=geometry['raw_dimensions']
    return facts,images[best] if with_pixels else None


def inspect_dng(path):
    """Legacy facts plus strict ordered geometry; fully verify reference pixels."""
    return _inspect(path,False)[0]


def display_reference(path):
    """Upright decoded source reference, never resized and never RAW pixels."""
    facts,image=_inspect(path,True)
    return image,facts
