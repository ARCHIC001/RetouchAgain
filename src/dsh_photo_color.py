"""Validate actual ICC colour behaviour at the model attachment boundary."""
import io
import json
import sys
from PIL import Image, ImageCms


_checked=[]

def validate(profile):
    for previous,facts in _checked:
        if previous==profile: return dict(facts)
    if not profile or len(profile)>4*1024*1024:
        raise ValueError('Missing or oversized ICC profile')
    source=ImageCms.ImageCmsProfile(io.BytesIO(profile))
    name=ImageCms.getProfileName(source).strip()
    if 'srgb' not in name.lower() or source.profile.xcolor_space.strip()!='RGB':
        raise ValueError('Expected an RGB sRGB ICC profile, got '+name)
    # A description alone does not establish the primaries or transfer curve.
    # Exercise a colour cube through LittleCMS against its standard sRGB profile.
    levels=range(0,256,17)
    samples=[(r,g,b) for r in levels for g in levels for b in levels]
    probe=Image.new('RGB',(len(samples),1));probe.putdata(samples)
    converted=ImageCms.profileToProfile(probe,source,ImageCms.createProfile('sRGB'),
                                       outputMode='RGB',renderingIntent=0,flags=0)
    errors=[abs(a-b) for before,after in zip(samples,converted.getdata()) for a,b in zip(before,after)]
    if max(errors)>2 or sum(errors)/len(errors)>.25:
        raise ValueError('ICC colour behaviour is not sRGB: '+name)
    facts={'profile':name,'colour_space':'sRGB','max_probe_error':max(errors)}
    if len(_checked)>=8: _checked.pop(0)
    _checked.append((profile,facts))
    return dict(facts)


if __name__=='__main__':
    try:
        print(json.dumps(validate(sys.stdin.buffer.read(4*1024*1024+1))))
    except Exception as exc:
        print(str(exc),file=sys.stderr)
        raise SystemExit(1)
