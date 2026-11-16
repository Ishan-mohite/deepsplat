#!/usr/bin/env python3
"""Build the .pptx backup of the pitch from the browser deck (index.html).

Each slide is exported layer by layer: one transparent PNG per animated element, rendered by Chrome from the
real page. The layers are placed at their exact position on a 16:9 PowerPoint slide and get a native entrance
animation, in the same order and with the same click steps as in the browser. The demo video is embedded,
starts by itself and the slide moves on when it ends. Speaker notes come from the deck's NOTES.

Text on the slides is therefore a picture (so it looks the same without the brand fonts installed).
To change wording, edit index.html and run this script again.

Usage:  python3 pitch/tools/build_pptx.py [output.pptx] [--keep]
Needs:  google-chrome and Python 3 with Pillow.
"""
import datetime
import subprocess
import itertools
import json
import shutil
import sys
import tempfile
import time
import xml.dom.minidom
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from PIL import Image, ImageChops, ImageStat

HERE = Path(__file__).resolve().parent
DECK = HERE.parent
TEMPLATE = HERE / 'pptx_template'
sys.path.insert(0, str(HERE))

STAGE = (1920, 1080)
SCALE = 2                       # layers are rendered at 2x for sharp projection
EMU = 6350                      # EMU per stage pixel: 1920 px = 12192000 EMU = 13.333 in
PAPER = (241, 238, 230)
VIDEO_PAPER = (241, 237, 229)   # the video's paper tone after decoding, used for its poster frame
VIDEO = DECK / 'assets' / 'pipeline.mp4'
VIDEO_MS = 58133

NS = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')
XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'


# ----------------------------------------------------------------------------- export from the browser deck
def wait_ready(c, query, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        ok = c.js("location.search === " + json.dumps('?' + query) + " && document.readyState === 'complete'"
                  " && document.documentElement.dataset.ready === '1' && Array.from(document.images).every(i => i.complete)"
                  " && (!document.fonts || document.fonts.status === 'loaded')")
        if ok:
            time.sleep(0.12)
            return
        time.sleep(0.05)
    raise RuntimeError('page did not become ready')


def export_layers(work):
    from _cdp import Chrome
    url = (DECK / 'index.html').as_uri()
    c = Chrome(str(work / 'chrome-profile'), port=9347, scale=SCALE)
    try:
        c.cmd('Emulation.setDeviceMetricsOverride', width=STAGE[0], height=STAGE[1], deviceScaleFactor=SCALE, mobile=False)
        c.cmd('Emulation.setDefaultBackgroundColorOverride', color={'r': 0, 'g': 0, 'b': 0, 'a': 0})

        def grab(query, name):
            c.nav('about:blank', wait=0.03)
            c.nav(url + '?' + query, wait=0.1)
            wait_ready(c, query)
            path = work / (name + '.png')
            c.shot(str(path))
            return path

        c.nav(url + '?manifest', wait=0.2)
        wait_ready(c, 'manifest')
        slides = json.loads(c.js("document.getElementById('manifest').textContent"))
        for n, s in enumerate(slides, 1):
            s['n'] = n
            if s.get('video'):
                continue
            s['full'] = grab(f'export={n}&layer=__all', f's{n}__all')
            for layer in s['layers']:
                shot = grab(f"export={n}&layer={layer['id']}", f"s{n}_{layer['id']}")
                im = Image.open(shot).convert('RGBA')
                if im.size != (STAGE[0] * SCALE, STAGE[1] * SCALE):
                    raise RuntimeError(f'unexpected screenshot size {im.size}')
                box = im.getchannel('A').point(lambda v: 255 if v > 2 else 0).getbbox()
                if not box:
                    layer['png'] = None
                    continue
                pad = 2 * SCALE
                x0, y0 = max(0, (box[0] - pad) // SCALE * SCALE), max(0, (box[1] - pad) // SCALE * SCALE)
                x1 = min(im.width, -(-(box[2] + pad) // SCALE) * SCALE)
                y1 = min(im.height, -(-(box[3] + pad) // SCALE) * SCALE)
                out = work / f"layer_s{n}_{layer['id']}.png"
                im.crop((x0, y0, x1, y1)).save(out, optimize=True)
                layer['png'] = out
                layer['box'] = (x0 // SCALE, y0 // SCALE, (x1 - x0) // SCALE, (y1 - y0) // SCALE)
            print(f"  slide {n} ({s['id']}): {sum(1 for l in s['layers'] if l['png'])} layers", flush=True)
        brand_shot = Image.open(grab('export=2&layer=__brand', 'brand')).convert('RGBA')
        bb = brand_shot.getchannel('A').point(lambda v: 255 if v > 2 else 0).getbbox()
        bb = (bb[0] // SCALE * SCALE, bb[1] // SCALE * SCALE, -(-bb[2] // SCALE) * SCALE, -(-bb[3] // SCALE) * SCALE)
        brand = {'png': work / 'layer_brand.png', 'box': (bb[0] // SCALE, bb[1] // SCALE, (bb[2] - bb[0]) // SCALE, (bb[3] - bb[1]) // SCALE)}
        brand_shot.crop(bb).save(brand['png'], optimize=True)
        errors = [m for m in c.console() if m.startswith(('error', 'EXCEPTION'))]
        if errors:
            raise RuntimeError('the page reported errors: ' + '; '.join(errors)[:500])
        return slides, brand
    finally:
        c.close()


def check_composite(slides, brand, work):
    """Stack the exported layers again and compare with the full render: proves positions and completeness."""
    worst = 0.0
    for s in slides:
        if s.get('video'):
            continue
        comp = Image.new('RGBA', (STAGE[0] * SCALE, STAGE[1] * SCALE), PAPER + (255,))
        for layer in s['layers']:
            if layer['png']:
                im = Image.open(layer['png']).convert('RGBA')
                comp.alpha_composite(im, (layer['box'][0] * SCALE, layer['box'][1] * SCALE))
        ref = Image.new('RGBA', comp.size, PAPER + (255,))
        ref.alpha_composite(Image.open(s['full']).convert('RGBA'))
        diff = ImageChops.difference(comp.convert('RGB'), ref.convert('RGB'))
        mean = sum(ImageStat.Stat(diff).mean) / 3
        worst = max(worst, mean)
        comp.convert('RGB').resize(STAGE, Image.LANCZOS).save(work / f"preview_s{s['n']}.jpg", quality=90)
        print(f"  slide {s['n']}: layers vs full render, mean difference {mean:.3f} of 255", flush=True)
    return worst


# ----------------------------------------------------------------------------- OOXML pieces
def pic_xml(spid, name, descr, rid, box):
    x, y, w, h = (v * EMU for v in box)
    return (f'<p:pic><p:nvPicPr><p:cNvPr id="{spid}" name={quoteattr(name)} descr={quoteattr(descr)}/>'
            '<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
            f'<p:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
            f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>')


def video_xml(spid, name, descr, rid_media, rid_video, rid_poster, box=(0, 0) + STAGE):
    x, y, w, h = (round(v * EMU) for v in box)
    return (f'<p:pic><p:nvPicPr><p:cNvPr id="{spid}" name={quoteattr(name)} descr={quoteattr(descr)}>'
            '<a:hlinkClick r:id="" action="ppaction://media"/></p:cNvPr>'
            '<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr>'
            f'<p:nvPr><a:videoFile r:link="{rid_video}"/><p:extLst><p:ext uri="{{DAA4B4D4-6D71-4841-9C94-3DE7FCFB9230}}">'
            f'<p14:media xmlns:p14="http://schemas.microsoft.com/office/powerpoint/2010/main" r:embed="{rid_media}"/>'
            '</p:ext></p:extLst></p:nvPr></p:nvPicPr>'
            f'<p:blipFill><a:blip r:embed="{rid_poster}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
            f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{w}" cy="{h}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>')


def _target(spid):
    return f'<p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl>'


def _show(ids, spid):
    return (f'<p:set><p:cBhvr><p:cTn id="{next(ids)}" dur="1" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst></p:cTn>'
            f'{_target(spid)}<p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst></p:cBhvr>'
            '<p:to><p:strVal val="visible"/></p:to></p:set>')


def _fade(ids, spid, dur):
    return (f'<p:animEffect transition="in" filter="fade"><p:cBhvr><p:cTn id="{next(ids)}" dur="{dur}"/>{_target(spid)}</p:cBhvr></p:animEffect>')


def _move(ids, spid, attr, start, dur):
    return (f'<p:anim calcmode="lin" valueType="num"><p:cBhvr additive="base"><p:cTn id="{next(ids)}" dur="{dur}" decel="100000" fill="hold"/>'
            f'{_target(spid)}<p:attrNameLst><p:attrName>{attr}</p:attrName></p:attrNameLst></p:cBhvr><p:tavLst>'
            f'<p:tav tm="0"><p:val><p:strVal val="{start}"/></p:val></p:tav>'
            f'<p:tav tm="100000"><p:val><p:strVal val="#{attr}"/></p:val></p:tav></p:tavLst></p:anim>')


def _grow(ids, spid, attr, dur):
    return (f'<p:anim calcmode="lin" valueType="num"><p:cBhvr><p:cTn id="{next(ids)}" dur="{dur}" decel="100000" fill="hold"/>'
            f'{_target(spid)}<p:attrNameLst><p:attrName>{attr}</p:attrName></p:attrNameLst></p:cBhvr><p:tavLst>'
            f'<p:tav tm="0"><p:val><p:fltVal val="0"/></p:val></p:tav>'
            f'<p:tav tm="100000"><p:val><p:strVal val="#{attr}"/></p:val></p:tav></p:tavLst></p:anim>')


def effect_xml(ids, spid, anim, delay_ms, node_type):
    """One entrance effect, written the way PowerPoint stores Fade, Float In, Zoom and Wipe."""
    eid = next(ids)
    if anim in ('rise', 'drop'):
        preset, sub = 42, 0
        body = _show(ids, spid) + _fade(ids, spid, 700) + _move(ids, spid, 'ppt_x', '#ppt_x', 700) + \
            _move(ids, spid, 'ppt_y', '#ppt_y+.05' if anim == 'rise' else '#ppt_y-.07', 700)
    elif anim in ('left', 'right'):
        preset, sub = 42, 0
        body = _show(ids, spid) + _fade(ids, spid, 600) + \
            _move(ids, spid, 'ppt_x', '#ppt_x-.03' if anim == 'left' else '#ppt_x+.03', 600) + _move(ids, spid, 'ppt_y', '#ppt_y', 600)
    elif anim in ('zoom', 'pop'):
        preset, sub = 53, 16
        body = _show(ids, spid) + _grow(ids, spid, 'ppt_w', 450) + _grow(ids, spid, 'ppt_h', 450) + _fade(ids, spid, 450)
    elif anim == 'wipe':
        preset, sub = 22, 8
        body = _show(ids, spid) + (f'<p:animEffect transition="in" filter="wipe(left)"><p:cBhvr><p:cTn id="{next(ids)}" dur="900"/>'
                                    f'{_target(spid)}</p:cBhvr></p:animEffect>')
    else:
        preset, sub = 10, 0
        body = _show(ids, spid) + _fade(ids, spid, 700)
    return (f'<p:par><p:cTn id="{eid}" presetID="{preset}" presetClass="entr" presetSubtype="{sub}" fill="hold" nodeType="{node_type}">'
            f'<p:stCondLst><p:cond delay="{int(delay_ms)}"/></p:stCondLst><p:childTnLst>{body}</p:childTnLst></p:cTn></p:par>')


def timing_xml(groups, videos=()):
    """groups: [(starts_by_itself, [(shape id, animation, delay in ms[, video ms]), ...]), ...]; videos: shape ids of media"""
    ids = itertools.count(1)
    root, main = next(ids), next(ids)
    seq = ''
    for auto, effects in groups:
        outer, inner = next(ids), next(ids)
        cond = '<p:cond delay="indefinite"/>' + (f'<p:cond evt="onBegin" delay="0"><p:tn val="{main}"/></p:cond>' if auto else '')
        body = ''
        for k, (spid, anim, delay, *rest) in enumerate(effects):
            node_type = ('afterEffect' if auto else 'clickEffect') if k == 0 else 'withEffect'
            if anim == 'video':
                body += (f'<p:par><p:cTn id="{next(ids)}" presetID="1" presetClass="mediacall" presetSubtype="0" fill="hold" nodeType="{node_type}">'
                         f'<p:stCondLst><p:cond delay="{int(delay)}"/></p:stCondLst><p:childTnLst><p:cmd type="call" cmd="playFrom(0.0)"><p:cBhvr>'
                         f'<p:cTn id="{next(ids)}" dur="{rest[0] if rest else VIDEO_MS}" fill="hold"/>{_target(spid)}</p:cBhvr></p:cmd></p:childTnLst></p:cTn></p:par>')
            else:
                body += effect_xml(ids, spid, anim, delay, node_type)
        seq += (f'<p:par><p:cTn id="{outer}" fill="hold"><p:stCondLst>{cond}</p:stCondLst><p:childTnLst>'
                f'<p:par><p:cTn id="{inner}" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>{body}'
                '</p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>')
    media = ''.join(f'<p:video><p:cMediaNode vol="80000"><p:cTn id="{next(ids)}" fill="hold" display="0"><p:stCondLst><p:cond delay="indefinite"/>'
                    f'</p:stCondLst></p:cTn>{_target(v)}</p:cMediaNode></p:video>' for v in videos)
    return (f'<p:timing><p:tnLst><p:par><p:cTn id="{root}" dur="indefinite" restart="never" nodeType="tmRoot"><p:childTnLst>'
            f'<p:seq concurrent="1" nextAc="seek"><p:cTn id="{main}" dur="indefinite" nodeType="mainSeq"><p:childTnLst>{seq}</p:childTnLst></p:cTn>'
            '<p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>'
            '<p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst></p:seq>'
            f'{media}</p:childTnLst></p:cTn></p:par></p:tnLst></p:timing>')


def slide_xml(name, shapes, timing, advance_ms=None):
    adv = f' advTm="{advance_ms}"' if advance_ms else ''
    return (XML + f'<p:sld {NS}><p:cSld name={quoteattr(name)}>'
            f'<p:bg><p:bgPr><a:solidFill><a:srgbClr val="{"%02X%02X%02X" % PAPER}"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>'
            '<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            f'{shapes}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'
            f'<p:transition spd="med"{adv}><p:fade/></p:transition>{timing}</p:sld>')


def notes_xml(text, number):
    paras = ''.join(f'<a:p><a:r><a:rPr lang="en-US" dirty="0"/><a:t>{escape(t)}</a:t></a:r></a:p>' for t in text)
    return (XML + f'<p:notes {NS}><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/><p:cNvSpPr><a:spLocks noGrp="1" noRot="1" noChangeAspect="1"/></p:cNvSpPr>'
            '<p:nvPr><p:ph type="sldImg"/></p:nvPr></p:nvSpPr><p:spPr/></p:sp>'
            '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            f'<p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/>{paras}</p:txBody></p:sp>'
            '<p:sp><p:nvSpPr><p:cNvPr id="4" name="Slide Number Placeholder 3"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            '<p:nvPr><p:ph type="sldNum" sz="quarter" idx="10"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/>'
            f'<a:p><a:fld id="{{F7021451-1387-4CA6-816F-3879F97B5CBC}}" type="slidenum"><a:rPr lang="en-US"/><a:t>{number}</a:t></a:fld>'
            '<a:endParaRPr lang="en-US"/></a:p></p:txBody></p:sp></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>')


def rels_xml(rels):
    items = ''.join(f'<Relationship Id="{rid}" Type="{typ}" Target="{target}"/>' for rid, typ, target in rels)
    return XML + f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{items}</Relationships>'


# ----------------------------------------------------------------------------- package
def build(slides, brand, out):
    parts = {}           # part name -> bytes
    media = {}           # source path -> media file name

    def add_media(path):
        if path not in media:
            media[path] = f'image{len(media) + 1}.png'
            parts['ppt/media/' + media[path]] = Path(path).read_bytes()
        return '../media/' + media[path]

    poster = Image.new('RGB', STAGE, VIDEO_PAPER)
    poster_path = Path(tempfile.gettempdir()) / 'deepsplat_pitch_video_poster.png'
    poster.save(poster_path)

    for s in slides:
        n = s['n']
        rels = [('rId1', REL + 'slideLayout', '../slideLayouts/slideLayout1.xml'), ('rId2', REL + 'notesSlide', f'../notesSlides/notesSlide{n}.xml')]
        rid = itertools.count(3)
        spid = itertools.count(2)
        shapes, groups, videos, advance = '', [], [], None
        if s.get('video'):
            r_media, r_video, r_poster = f'rId{next(rid)}', f'rId{next(rid)}', f'rId{next(rid)}'
            parts['ppt/media/pipeline.mp4'] = VIDEO.read_bytes()
            rels += [(r_media, 'http://schemas.microsoft.com/office/2007/relationships/media', '../media/pipeline.mp4'),
                     (r_video, REL + 'video', '../media/pipeline.mp4'), (r_poster, REL + 'image', add_media(poster_path))]
            video_spid = next(spid)
            videos.append(video_spid)
            shapes += video_xml(video_spid, 'Demo video', 'Demo video: the pipeline from scan to the crack located in the 3D twin', r_media, r_video, r_poster)
            groups.append((True, [(video_spid, 'video', 0)]))
            advance = VIDEO_MS + 200
        else:
            if s['chrome'] == 'full':
                r = f'rId{next(rid)}'
                rels.append((r, REL + 'image', add_media(brand['png'])))
                shapes += pic_xml(next(spid), 'deepsplat logo', 'deepsplat', r, brand['box'])
            by_step = {}
            for layer in s['layers']:
                if not layer['png']:
                    continue
                r, sid = f'rId{next(rid)}', next(spid)
                rels.append((r, REL + 'image', add_media(layer['png'])))
                shapes += pic_xml(sid, f"{s['id']} · {layer['id']}", layer.get('text') or layer['id'], r, layer['box'])
                by_step.setdefault(layer['step'], []).append((sid, layer['anim'], round(layer['delay'] * 1000)))
                clip = layer.get('clip')
                if clip:                                      # a real video shape on top of the layer's empty video frame
                    src = DECK / clip['src']
                    name = src.name
                    parts['ppt/media/' + name] = src.read_bytes()
                    poster_png = Path(tempfile.gettempdir()) / (src.stem + '_poster.png')
                    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(src), '-frames:v', '1', str(poster_png)], check=True)
                    r_media, r_video, r_poster = f'rId{next(rid)}', f'rId{next(rid)}', f'rId{next(rid)}'
                    rels += [(r_media, 'http://schemas.microsoft.com/office/2007/relationships/media', '../media/' + name),
                             (r_video, REL + 'video', '../media/' + name), (r_poster, REL + 'image', add_media(poster_png))]
                    vsid = next(spid)
                    videos.append(vsid)
                    ms = int(round(float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(src)],
                                                        capture_output=True, text=True, check=True).stdout) * 1000))
                    shapes += video_xml(vsid, f"{s['id']} · video", layer.get('text') or 'video', r_media, r_video, r_poster, clip['box'])
                    d = round(layer['delay'] * 1000)
                    by_step[layer['step']] += [(vsid, layer['anim'], d), (vsid, 'video', d + 500, ms)]
            for st in sorted(by_step):
                groups.append((st == 0, sorted(by_step[st], key=lambda e: e[2])))
        parts[f'ppt/slides/slide{n}.xml'] = slide_xml(s['notes']['name'], shapes, timing_xml(groups, videos), advance).encode()
        parts[f'ppt/slides/_rels/slide{n}.xml.rels'] = rels_xml(rels).encode()
        say = s['notes']['say'].replace('▸', '[CLICK]')
        parts[f'ppt/notesSlides/notesSlide{n}.xml'] = notes_xml([f"{s['notes']['time']}  ·  {s['notes']['name']}", say], n).encode()
        parts[f'ppt/notesSlides/_rels/notesSlide{n}.xml.rels'] = rels_xml([('rId1', REL + 'notesMaster', '../notesMasters/notesMaster1.xml'),
                                                                            ('rId2', REL + 'slide', f'../slides/slide{n}.xml')]).encode()

    count = len(slides)
    # Static parts from the template (the team's earlier deck), reduced to one blank layout.
    master = (TEMPLATE / 'slideMaster1.xml').read_text()
    a, b = master.index('<p:sldLayoutIdLst>'), master.index('</p:sldLayoutIdLst>')
    master = master[:a] + '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/>' + master[b:]
    parts['ppt/slideMasters/slideMaster1.xml'] = master.encode()
    parts['ppt/slideMasters/_rels/slideMaster1.xml.rels'] = rels_xml([('rId1', REL + 'slideLayout', '../slideLayouts/slideLayout1.xml'),
                                                                       ('rId2', REL + 'theme', '../theme/theme1.xml')]).encode()
    parts['ppt/slideLayouts/slideLayout1.xml'] = (TEMPLATE / 'slideLayout1.xml').read_bytes()
    parts['ppt/slideLayouts/_rels/slideLayout1.xml.rels'] = rels_xml([('rId1', REL + 'slideMaster', '../slideMasters/slideMaster1.xml')]).encode()
    parts['ppt/notesMasters/notesMaster1.xml'] = (TEMPLATE / 'notesMaster1.xml').read_bytes()
    parts['ppt/notesMasters/_rels/notesMaster1.xml.rels'] = rels_xml([('rId1', REL + 'theme', '../theme/theme1.xml')]).encode()
    for name in ('presProps.xml', 'viewProps.xml', 'tableStyles.xml'):
        parts['ppt/' + name] = (TEMPLATE / name).read_bytes()
    parts['ppt/theme/theme1.xml'] = (TEMPLATE / 'theme1.xml').read_bytes()

    pres = (TEMPLATE / 'presentation.xml').read_text()
    a, b = pres.index('<p:sldIdLst>'), pres.index('</p:sldIdLst>') + len('</p:sldIdLst>')
    pres = pres[:a] + '<p:sldIdLst>' + ''.join(f'<p:sldId id="{255 + n}" r:id="rId{n + 1}"/>' for n in range(1, count + 1)) + '</p:sldIdLst>' + pres[b:]
    a, b = pres.index('<p:notesMasterIdLst>'), pres.index('</p:notesMasterIdLst>') + len('</p:notesMasterIdLst>')
    pres = pres[:a] + f'<p:notesMasterIdLst><p:notesMasterId r:id="rId{count + 2}"/></p:notesMasterIdLst>' + pres[b:]
    # The template's section list names slides of the old deck; write one for these slides instead.
    sections = [('Opening', '{db9909d3-ba31-1a65-e656-88255b80ddd8}', ('title',)),
                ('Problem and idea', '{fe184b43-aa99-a07d-6296-e989606132a0}', ('problem', 'idea')),
                ('Demo, change tracking and proof', '{f063df71-9546-3f59-1577-5dc08eedb875}', ('demo', 'change', 'proof')),
                ('What is next and the team', '{8db2faca-f843-395e-511b-eb130dba3846}', ('next', 'team'))]
    listed = [sid for _, _, ids in sections for sid in ids]
    if sorted(listed) != sorted(s['id'] for s in slides):
        raise RuntimeError('the section list in build_pptx.py does not match the slides in index.html')
    sec = ''.join(f'<p14:section name={quoteattr(name)} id="{guid}"><p14:sldIdLst>'
                  + ''.join(f'<p14:sldId id="{255 + s["n"]}"/>' for s in slides if s['id'] in ids) + '</p14:sldIdLst></p14:section>'
                  for name, guid, ids in sections)
    a, b = pres.index('<p:extLst>'), pres.index('</p:extLst>') + len('</p:extLst>')
    pres = (pres[:a] + '<p:extLst><p:ext uri="{521415D9-36F7-43E2-AB2F-B90AF26B5E84}">'
            f'<p14:sectionLst xmlns:p14="http://schemas.microsoft.com/office/powerpoint/2010/main">{sec}</p14:sectionLst></p:ext>'
            '<p:ext uri="{EFAFB233-063F-42B5-8137-9DF3F51BA10A}"><p15:sldGuideLst xmlns:p15="http://schemas.microsoft.com/office/powerpoint/2012/main"/></p:ext>'
            '</p:extLst>' + pres[b:])
    parts['ppt/presentation.xml'] = pres.encode()
    parts['ppt/_rels/presentation.xml.rels'] = rels_xml(
        [('rId1', REL + 'slideMaster', 'slideMasters/slideMaster1.xml')]
        + [(f'rId{n + 1}', REL + 'slide', f'slides/slide{n}.xml') for n in range(1, count + 1)]
        + [(f'rId{count + 2}', REL + 'notesMaster', 'notesMasters/notesMaster1.xml'), (f'rId{count + 3}', REL + 'presProps', 'presProps.xml'),
           (f'rId{count + 4}', REL + 'viewProps', 'viewProps.xml'), (f'rId{count + 5}', REL + 'theme', 'theme/theme1.xml'),
           (f'rId{count + 6}', REL + 'tableStyles', 'tableStyles.xml')]).encode()

    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    parts['docProps/core.xml'] = (XML + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>deepsplat · Jury pitch · Leukerbad Robotics Carnival Hack 2026</dc:title>'
        '<dc:creator>deepsplat team</dc:creator><cp:lastModifiedBy>deepsplat team</cp:lastModifiedBy><cp:revision>1</cp:revision>'
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
        '</cp:coreProperties>').encode()
    parts['docProps/app.xml'] = (XML + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><TotalTime>0</TotalTime><Words>0</Words>'
        '<Application>Microsoft Office PowerPoint</Application><PresentationFormat>On-screen Show (16:9)</PresentationFormat>'
        f'<Paragraphs>0</Paragraphs><Slides>{count}</Slides><Notes>{count}</Notes><HiddenSlides>0</HiddenSlides><MMClips>1</MMClips>'
        '<ScaleCrop>false</ScaleCrop><Company>deepsplat</Company><LinksUpToDate>false</LinksUpToDate><SharedDoc>false</SharedDoc>'
        '<HyperlinksChanged>false</HyperlinksChanged><AppVersion>16.0000</AppVersion></Properties>').encode()
    parts['_rels/.rels'] = rels_xml([('rId1', REL + 'extended-properties', 'docProps/app.xml'),
                                     ('rId2', 'http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties', 'docProps/core.xml'),
                                     ('rId3', REL + 'officeDocument', 'ppt/presentation.xml')]).encode()

    P = 'application/vnd.openxmlformats-officedocument.presentationml.'
    overrides = [('/ppt/presentation.xml', P + 'presentation.main+xml'), ('/ppt/slideMasters/slideMaster1.xml', P + 'slideMaster+xml'),
                 ('/ppt/slideLayouts/slideLayout1.xml', P + 'slideLayout+xml'), ('/ppt/notesMasters/notesMaster1.xml', P + 'notesMaster+xml'),
                 ('/ppt/presProps.xml', P + 'presProps+xml'), ('/ppt/viewProps.xml', P + 'viewProps+xml'), ('/ppt/tableStyles.xml', P + 'tableStyles+xml'),
                 ('/ppt/theme/theme1.xml', 'application/vnd.openxmlformats-officedocument.theme+xml'),
                 ('/docProps/core.xml', 'application/vnd.openxmlformats-package.core-properties+xml'),
                 ('/docProps/app.xml', 'application/vnd.openxmlformats-officedocument.extended-properties+xml')]
    overrides += [(f'/ppt/slides/slide{n}.xml', P + 'slide+xml') for n in range(1, count + 1)]
    overrides += [(f'/ppt/notesSlides/notesSlide{n}.xml', P + 'notesSlide+xml') for n in range(1, count + 1)]
    parts['[Content_Types].xml'] = (XML + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="png" ContentType="image/png"/><Default Extension="mp4" ContentType="video/mp4"/>'
        + ''.join(f'<Override PartName="{name}" ContentType="{ct}"/>' for name, ct in overrides) + '</Types>').encode()

    problems = check_package(parts)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in ['[Content_Types].xml'] + sorted(k for k in parts if k != '[Content_Types].xml'):
            z.writestr(zipfile.ZipInfo(name, date_time=time.localtime()[:6]), parts[name],
                       zipfile.ZIP_STORED if name.endswith(('.mp4', '.png')) else zipfile.ZIP_DEFLATED)
    return problems


def check_package(parts):
    """Stdlib checks: well-formed XML, every relationship target exists, every shape used by an animation exists."""
    import posixpath
    import re
    problems = []
    for name, data in parts.items():
        if name.endswith(('.xml', '.rels')):
            try:
                xml.dom.minidom.parseString(data)
            except Exception as e:      # noqa: BLE001
                problems.append(f'{name}: not well-formed XML ({e})')
    for name, data in parts.items():
        if not name.endswith('.rels'):
            continue
        base = posixpath.dirname(posixpath.dirname(name))
        for target in re.findall(r'Target="([^"]+)"', data.decode()):
            full = posixpath.normpath(posixpath.join(base, target))
            if full not in parts:
                problems.append(f'{name}: target {target} is missing')
    types = parts['[Content_Types].xml'].decode()
    for name in parts:
        if name == '[Content_Types].xml' or name.endswith(('.rels', '.png', '.mp4')):
            continue
        if f'PartName="/{name}"' not in types:
            problems.append(f'{name}: no content type')
    for name, data in parts.items():
        if not re.fullmatch(r'ppt/slides/slide\d+\.xml', name):
            continue
        text = data.decode()
        shape_ids = re.findall(r'<p:cNvPr id="(\d+)"', text)
        if len(shape_ids) != len(set(shape_ids)):
            problems.append(f'{name}: duplicate shape ids')
        for spid in set(re.findall(r'<p:spTgt spid="(\d+)"', text)):
            if spid not in shape_ids:
                problems.append(f'{name}: animation targets missing shape {spid}')
        node_ids = re.findall(r'<p:cTn id="(\d+)"', text)
        if len(node_ids) != len(set(node_ids)):
            problems.append(f'{name}: duplicate timing ids')
        rels = parts[name.replace('slides/', 'slides/_rels/') + '.rels'].decode()
        for rid in set(re.findall(r'r:(?:embed|link)="(rId\d+)"', text)):
            if f'Id="{rid}"' not in rels:
                problems.append(f'{name}: relationship {rid} is missing')
    pres = parts['ppt/presentation.xml'].decode()
    slide_ids = set(re.findall(r'<p:sldId id="(\d+)"', pres))
    for sid in re.findall(r'<p14:sldId id="(\d+)"', pres):
        if sid not in slide_ids:
            problems.append(f'presentation.xml: a section lists slide id {sid}, which does not exist')
    pres_rels = parts['ppt/_rels/presentation.xml.rels'].decode()
    for rid in set(re.findall(r'r:id="(rId\d+)"', pres)):
        if f'Id="{rid}"' not in pres_rels:
            problems.append(f'presentation.xml: relationship {rid} is missing')
    return problems


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    out = Path(args[0]).resolve() if args else DECK / 'deepsplat_jury_pitch.pptx'
    if not shutil.which('google-chrome'):
        sys.exit('google-chrome is needed to render the slides')
    work = Path(tempfile.mkdtemp(prefix='deepsplat-pptx-'))
    try:
        print('Exporting layers from the browser deck …', flush=True)
        slides, brand = export_layers(work)
        print('Checking the layers against the full slides …', flush=True)
        worst = check_composite(slides, brand, work)
        print('Writing', out, flush=True)
        problems = build(slides, brand, out)
        effects = sum(len(s['layers']) for s in slides if not s.get('video'))
        print(f'{len(slides)} slides, {effects} animated layers, {out.stat().st_size / 1e6:.1f} MB')
        if worst > 1.0:
            problems.append(f'layers differ from the full render (mean {worst:.2f})')
        for p in problems:
            print('PROBLEM:', p)
        if '--keep' in sys.argv:
            print('Work files kept in', work)
        return 1 if problems else 0
    finally:
        if '--keep' not in sys.argv:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
