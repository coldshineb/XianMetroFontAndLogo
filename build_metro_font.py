import sys, os, json, pickle, time, subprocess
import numpy as np

script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)
sys.path.insert(0, os.path.join(script_dir, ".deps"))

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.ttLib import TTFont, TTCollection
from fontTools.ttLib.tables.O_S_2f_2 import Panose
from fontTools.ttLib.tables.D_S_I_G_ import table_D_S_I_G_
from fontTools.ttLib.tables._g_a_s_p import table__g_a_s_p

sys.stdout.reconfigure(encoding='utf-8')

print("Loading metadata and glyph cache...")
with open("character_instances_meta.json", "r", encoding="utf-8") as f:
    records = json.load(f)

with open("glyph_cache.pkl", "rb") as f:
    raw_cache = pickle.load(f)

glyph_cache = {}
for k, v in raw_cache.items():
    glyph_cache[k] = v
    glyph_cache[k.replace('/', '\\')] = v
    glyph_cache[k.replace('\\', '/')] = v

def extract_path(val):
    if isinstance(val, dict):
        return val.get("out_path", "")
    elif isinstance(val, str):
        return val
    return ""

overrides_path = "selected_font_variants.json"
user_overrides = {"global_overrides": {}, "line_overrides": {}}
if os.path.exists(overrides_path):
    try:
        with open(overrides_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "global_overrides" in data or "line_overrides" in data:
                user_overrides = data
            else:
                user_overrides["global_overrides"] = data
        print(f"Loaded user overrides: {len(user_overrides.get('global_overrides', {}))} global, {len(user_overrides.get('line_overrides', {}))} line groups.")
    except Exception as e:
        print(f"Notice: could not parse {overrides_path}: {e}")

def get_line_order(line_name):
    import re
    m = re.search(r'(\d+)', str(line_name))
    if m:
        return int(m.group(1))
    if "西户" in str(line_name):
        return 20
    return 80

char_groups = {}
for r in records:
    c = r["char"]
    char_groups.setdefault(c, []).append(r)

default_base_mapping = {}
for c, insts in char_groups.items():
    insts_sorted = sorted(insts, key=lambda x: (get_line_order(x["line"]), abs(x.get("height", 234.0) - 234.0), x["station"]))
    default_base_mapping[c] = insts_sorted[0]

active_base_mapping = dict(default_base_mapping)
global_ov = user_overrides.get("global_overrides", {})
for c, item in global_ov.items():
    target_p = extract_path(item)
    if c in char_groups and target_p:
        matched = [inst for inst in char_groups[c] if inst["out_path"].replace('/', '\\') == target_p.replace('/', '\\')]
        if matched:
            active_base_mapping[c] = matched[0]

potrace_bin = os.path.join(script_dir, "potrace_bin", "potrace-1.16.win64", "potrace.exe")

def vectorize_symbol(png_path, adv_width=500, y_center=370):
    from svg.path import parse_path, Move, Line, CubicBezier
    from PIL import Image
    import xml.etree.ElementTree as ET
    img = Image.open(png_path)
    arr = np.array(img)
    bin_arr = np.where(arr > 120, 0, 255).astype(np.uint8)
    h_img, w_img = arr.shape
    import tempfile
    td = os.path.join(tempfile.gettempdir(), "_metro_temp_punct")
    os.makedirs(td, exist_ok=True)
    bmp_p = os.path.join(td, f"tmp_{adv_width}_{os.getpid()}.bmp")
    svg_p = os.path.join(td, f"tmp_{adv_width}_{os.getpid()}.svg")
    Image.fromarray(bin_arr).save(bmp_p)
    subprocess.run([potrace_bin, "-s", "-a", "1.1", "-t", "2", "-o", svg_p, bmp_p], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    tree = ET.parse(svg_p)
    root = tree.getroot()
    paths = root.findall('.//{http://www.w3.org/2000/svg}path') or root.findall('.//path')
    contours, all_pts = [], []
    for p_elem in paths:
        d = p_elem.attrib.get('d', '')
        if not d: continue
        p = parse_path(d)
        contour = []
        for seg in p:
            if isinstance(seg, Move):
                pt = (seg.start.real * 0.1, seg.start.imag * 0.1)
                contour.append(('move', pt))
                all_pts.append(pt)
            elif isinstance(seg, Line):
                pt = (seg.end.real * 0.1, seg.end.imag * 0.1)
                contour.append(('line', pt))
                all_pts.append(pt)
            elif isinstance(seg, CubicBezier):
                c1 = (seg.control1.real * 0.1, seg.control1.imag * 0.1)
                c2 = (seg.control2.real * 0.1, seg.control2.imag * 0.1)
                end = (seg.end.real * 0.1, seg.end.imag * 0.1)
                contour.append(('cubic', c1, c2, end))
                all_pts.extend([c1, c2, end])
        contours.append(contour)
        
    try:
        os.remove(bmp_p)
        os.remove(svg_p)
    except: pass
    
    xs = [pt[0] for pt in all_pts]
    ys = [pt[1] for pt in all_pts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    raw_w = max_x - min_x
    raw_h = max_y - min_y
    
    scale = 765.0 / 234.0
    scaled_w = raw_w * scale
    scaled_h = raw_h * scale
    offset_x = (adv_width - scaled_w) / 2.0 - min_x * scale
    offset_y = y_center - ((min_y + max_y) / 2.0) * scale
    
    pen = T2CharStringPen(width=adv_width, glyphSet=None)
    for contour in contours:
        for cmd_item in contour:
            cmd = cmd_item[0]
            if cmd == 'move':
                pt = cmd_item[1]
                pen.moveTo((round(pt[0] * scale + offset_x), round(pt[1] * scale + offset_y)))
            elif cmd == 'line':
                pt = cmd_item[1]
                pen.lineTo((round(pt[0] * scale + offset_x), round(pt[1] * scale + offset_y)))
            elif cmd == 'cubic':
                c1, c2, end = cmd_item[1], cmd_item[2], cmd_item[3]
                pen.curveTo(
                    (round(c1[0] * scale + offset_x), round(c1[1] * scale + offset_y)),
                    (round(c2[0] * scale + offset_x), round(c2[1] * scale + offset_y)),
                    (round(end[0] * scale + offset_x), round(end[1] * scale + offset_y))
                )
        pen.closePath()
    lsb = round((adv_width - scaled_w) / 2.0)
    return pen.getCharString(), adv_width, lsb

def make_common_glyphs():
    cstrings = {}
    m = {}
    gorder = ['.notdef', 'space']
    cm = {32: 'space'}

    notdef_pen = T2CharStringPen(width=1000, glyphSet=None)
    notdef_pen.moveTo((100, 100))
    notdef_pen.lineTo((100, 800))
    notdef_pen.lineTo((900, 800))
    notdef_pen.lineTo((900, 100))
    notdef_pen.closePath()
    notdef_pen.moveTo((150, 150))
    notdef_pen.lineTo((850, 150))
    notdef_pen.lineTo((850, 750))
    notdef_pen.lineTo((150, 750))
    notdef_pen.closePath()
    cstrings['.notdef'] = notdef_pen.getCharString()
    m['.notdef'] = (1000, 100)

    space_pen = T2CharStringPen(width=500, glyphSet=None)
    cstrings['space'] = space_pen.getCharString()
    m['space'] = (500, 0)

    # 1. 真实书法中点 (间隔号)
    dot_png = os.path.join(script_dir, "extracted_instances", "符号", "点_建筑科技大学李家村.png")
    cs_dot_500, _, lsb_d500 = vectorize_symbol(dot_png, adv_width=500)
    cs_dot_1000, _, lsb_d1000 = vectorize_symbol(dot_png, adv_width=1000)

    cstrings['middot'] = cs_dot_500
    gorder.append('middot')
    m['middot'] = (500, lsb_d500)
    cm[0x00B7] = 'middot'  # ·
    cm[0x2022] = 'middot'  # •
    cm[0x002E] = 'middot'  # .

    cstrings['middot_full'] = cs_dot_1000
    gorder.append('middot_full')
    m['middot_full'] = (1000, lsb_d1000)
    cm[0x30FB] = 'middot_full'  # ・
    cm[0xFF0E] = 'middot_full'  # ．

    # 2. 真实书法左括号
    pl_png = os.path.join(script_dir, "extracted_instances", "符号", "左括号_北客站北广场.png")
    cs_pl_500, _, lsb_pl500 = vectorize_symbol(pl_png, adv_width=500)
    cs_pl_1000, _, lsb_pl1000 = vectorize_symbol(pl_png, adv_width=1000)

    cstrings['parenleft'] = cs_pl_500
    gorder.append('parenleft')
    m['parenleft'] = (500, lsb_pl500)
    cm[0x0028] = 'parenleft'  # (

    cstrings['parenleft_full'] = cs_pl_1000
    gorder.append('parenleft_full')
    m['parenleft_full'] = (1000, lsb_pl1000)
    cm[0xFF08] = 'parenleft_full'  # （

    # 3. 真实书法右括号
    pr_png = os.path.join(script_dir, "extracted_instances", "符号", "右括号_北客站北广场.png")
    cs_pr_500, _, lsb_pr500 = vectorize_symbol(pr_png, adv_width=500)
    cs_pr_1000, _, lsb_pr1000 = vectorize_symbol(pr_png, adv_width=1000)

    cstrings['parenright'] = cs_pr_500
    gorder.append('parenright')
    m['parenright'] = (500, lsb_pr500)
    cm[0x0029] = 'parenright'  # )

    cstrings['parenright_full'] = cs_pr_1000
    gorder.append('parenright_full')
    m['parenright_full'] = (1000, lsb_pr1000)
    cm[0xFF09] = 'parenright_full'  # ）

    return cstrings, m, gorder, cm

predefined_styles = {
    "1号线": ("Line01", "01号线", "Line 01", 100, "Line01", "Line01"),
    "2号线": ("Line02", "02号线", "Line 02", 150, "Line02", "Line02"),
    "3号线": ("Line03", "03号线", "Line 03", 200, "Line03", "Line03"),
    "4号线": ("Line04", "04号线", "Line 04", 250, "Line04", "Line04"),
    "5号线": ("Line05", "05号线", "Line 05", 300, "Line05", "Line05"),
    "6号线": ("Line06", "06号线", "Line 06", 350, "Line06", "Line06"),
    "8号线": ("Line08", "08号线", "Line 08", 450, "Line08", "Line08"),
    "9号线": ("Line09", "09号线", "Line 09", 500, "Line09", "Line09"),
    "10号线": ("Line10", "10号线", "Line 10", 550, "Line10", "Line10"),
    "14号线": ("Line14", "14号线", "Line 14", 600, "Line14", "Line14"),
    "15号线": ("Line15", "15号线", "Line 15", 650, "Line15", "Line15"),
    "16号线": ("Line16", "16号线", "Line 16", 700, "Line16", "Line16"),
    "西户线": ("LineXiHu", "西户线", "Line XiHu", 800, "LineXiHu", "LineXiHu"),
}

all_lines_present = sorted(list(set(r["line"] for r in records if r.get("line"))), key=get_line_order)

styles = [
    ("Regular", None, "标准", "Regular", 400, "Regular", "Regular")
]

for line_name in all_lines_present:
    if line_name in predefined_styles:
        s_key, s_zh, s_en, s_w, s_ps, s_fs = predefined_styles[line_name]
        styles.append((s_key, line_name, s_zh, s_en, s_w, s_ps, s_fs))
    else:
        import re
        m = re.search(r'(\d+)', line_name)
        if m:
            num = int(m.group(1))
            s_key = f"Line{num:02d}"
            s_zh = f"{num:02d}号线"
            s_en = f"Line {num:02d}"
            s_ps = f"Line{num:02d}"
            s_fs = f"Line{num:02d}"
            s_w = min(850, max(120, num * 50))
        else:
            clean_tag = "".join([ch for ch in line_name if ch.isalnum()])
            s_key = f"Line_{clean_tag}"
            s_zh = line_name
            s_en = f"Line {clean_tag}"
            s_ps = f"Line{clean_tag}"
            s_fs = f"Line{clean_tag}"
            s_w = 850
        styles.append((s_key, line_name, s_zh, s_en, s_w, s_ps, s_fs))

os.makedirs("fonts", exist_ok=True)
os.makedirs("fonts/family_weights", exist_ok=True)

# Remove any old History files
for old_f in ["fonts/family_weights/XianMetro_YanKai_History.ttf", "fonts/family_weights/XianMetro_YanKai_History.otf"]:
    if os.path.exists(old_f):
        try: os.remove(old_f)
        except: pass

ttf_objects = []

print(f"\nBuilding unified font family with {len(styles)} styles/weights...")

for idx, (style_key, line_filter, zh_sub, en_sub, weight, ps_suffix, file_suffix) in enumerate(styles):
    if style_key == "Regular":
        style_mapping = dict(active_base_mapping)
    else:
        line_recs = [r for r in records if r["line"] == line_filter]
        line_char_cands = {}
        for r in line_recs:
            line_char_cands.setdefault(r["char"], []).append(r)
        line_chars = {}
        for c, cands in line_char_cands.items():
            preferred_inst = None
            if c in active_base_mapping:
                base_st = active_base_mapping[c].get("station")
                st_matches = [inst for inst in cands if inst.get("station") == base_st]
                if st_matches:
                    preferred_inst = st_matches[0]
            if preferred_inst:
                line_chars[c] = preferred_inst
                continue
            cands_sorted = sorted(cands, key=lambda x: (abs(x.get("height", 234.0) - 234.0), x["station"]))
            line_chars[c] = cands_sorted[0]

        line_ov = user_overrides.get("line_overrides", {}).get(line_filter, {})
        for c, item in line_ov.items():
            target_p = extract_path(item)
            if target_p:
                matched = [inst for inst in line_recs if inst["char"] == c and inst["out_path"].replace('/', '\\') == target_p.replace('/', '\\')]
                if matched:
                    line_chars[c] = matched[0]

        style_mapping = line_chars

    cstrings, metrics, gorder, cmap = make_common_glyphs()
    for ch, rec in sorted(style_mapping.items(), key=lambda x: ord(x[0])):
        lookup_key = rec["out_path"]
        if lookup_key not in glyph_cache:
            lookup_key = lookup_key.replace('/', '\\')
        if lookup_key not in glyph_cache:
            lookup_key = lookup_key.replace('\\', '/')
        if lookup_key not in glyph_cache:
            # 选定切片若未预先缓存，即时提取，严禁静默偷换为其他候选
            try:
                import sync_and_rebuild_cache
                _, res = sync_and_rebuild_cache.process_one_instance((0, rec['out_path'], 234.0, ch))
                if res is not None:
                    glyph_cache[lookup_key] = res
                    glyph_cache[lookup_key.replace('/', '\\')] = res
                    glyph_cache[lookup_key.replace('\\', '/')] = res
                    print(f"  [即时提取选定切片]: {rec['out_path']}")
            except Exception as ex:
                print(f"  [警告] 即时提取选定切片失败: {ex}")
            if lookup_key not in glyph_cache:
                print(f"  [警告] 字符 '{ch}' 选定字形不存在: {rec['out_path']}，跳过该字，未静默替换。")
                continue
        gname = f"uni{ord(ch):04X}"
        gorder.append(gname)
        cmap[ord(ch)] = gname
        cached_val = glyph_cache[lookup_key]
        c_str, adv, lsb = cached_val[0], cached_val[1], cached_val[2]
        cstrings[gname] = c_str
        metrics[gname] = (adv, lsb)

    ps_name = f"XianMetro-StationNames-{ps_suffix}"
    pref_family_zh = "西安地铁站名"
    pref_family_en = "XianMetro StationNames"
    pref_subfamily_zh = zh_sub
    pref_subfamily_en = en_sub

    if style_key == "Regular":
        family_zh = pref_family_zh
        family_en = pref_family_en
        subfamily_zh = "标准"
        subfamily_en = "Regular"
        full_name_zh = family_zh
        full_name_en = family_en
    else:
        family_zh = f"西安地铁站名 {zh_sub}"
        family_en = f"XianMetro StationNames {en_sub}"
        subfamily_zh = "标准"
        subfamily_en = "Regular"
        full_name_zh = f"西安地铁站名 {zh_sub}"
        full_name_en = f"XianMetro StationNames {en_sub}"

    name_strings = {
        'familyName': dict(en=family_en, zh=family_zh),
        'styleName': dict(en=subfamily_en, zh=subfamily_zh),
        'uniqueFontIdentifier': f'2.000;XMR;{ps_name}',
        'fullName': dict(en=full_name_en, zh=full_name_zh),
        'version': 'Version 2.000;2026',
        'psName': ps_name,
        'description': dict(en=f"Xi'an Metro Station Names Calligraphy Font - {en_sub}.", zh=f"西安地铁站名书法字体 - {zh_sub}"),
        'typographicFamily': dict(en=pref_family_en, zh=pref_family_zh),
        'typographicSubfamily': dict(en=pref_subfamily_en, zh=pref_subfamily_zh),
        'wwsFamilyName': dict(en=pref_family_en, zh=pref_family_zh),
        'wwsSubfamilyName': dict(en=pref_subfamily_en, zh=pref_subfamily_zh),
    }

    panose = Panose()
    panose.bFamilyType = 2
    panose.bSerifStyle = 1
    panose_w_map = {100: 2, 150: 3, 200: 3, 250: 4, 300: 4, 350: 5, 400: 5, 450: 6, 500: 6, 550: 7, 600: 7, 650: 8, 700: 8, 800: 9}
    panose.bWeight = panose_w_map.get(weight, min(9, max(2, int(weight / 100) + 1)))
    panose.bProportion = 9
    panose.bContrast, panose.bStrokeVariation, panose.bArmStyle = 6, 1, 1
    panose.bLetterForm, panose.bMidline, panose.bXHeight = 1, 1, 1

    fs_sel = 0b01000000

    # 1. OTF
    fb_otf = FontBuilder(unitsPerEm=1000, isTTF=False)
    fb_otf.setupGlyphOrder(gorder)
    fb_otf.setupCharacterMap(cmap)
    cff_info = {'FontName': ps_name, 'FullName': full_name_en, 'FamilyName': pref_family_en, 'Weight': en_sub}
    top_dict = {'version': '2.000', 'Notice': f'Xi\'an Metro Yan Calligraphy Font {ps_suffix}.', 'Copyright': 'Xi\'an Rail Transit Group'}
    fb_otf.setupCFF(ps_name, top_dict, cstrings, {})
    fb_otf.setupHorizontalMetrics(metrics)
    fb_otf.setupHorizontalHeader(ascent=880, descent=-120)
    fb_otf.font['hhea'].lineGap = 0
    fb_otf.setupNameTable(name_strings)
    fb_otf.setupOS2(
        sTypoAscender=880, sTypoDescender=-120, sTypoLineGap=0,
        usWinAscent=880, usWinDescent=120,
        sxHeight=500, sCapHeight=800,
        usWeightClass=weight,
        fsSelection=fs_sel, fsType=0,
        panose=panose, achVendID='XMET',
        ulCodePageRange1=0x00040001, ulCodePageRange2=0x00000000
    )
    fb_otf.setupPost()
    fb_otf.font['head'].fontRevision = 2.0
    dsig_otf = table_D_S_I_G_()
    dsig_otf.ulVersion, dsig_otf.usFlag, dsig_otf.usNumSigs, dsig_otf.signatureRecords = 1, 0, 0, []
    fb_otf.font['DSIG'] = dsig_otf

    otf_out = os.path.join("fonts", "family_weights", f"XianMetro_StationNames_{file_suffix}.otf")
    fb_otf.save(otf_out)

    # 2. TTF
    fb_ttf = FontBuilder(unitsPerEm=1000, isTTF=True)
    fb_ttf.setupGlyphOrder(gorder)
    fb_ttf.setupCharacterMap(cmap)
    tt_glyphs = {}
    ttf_metrics = {}
    for g in gorder:
        tt_pen = TTGlyphPen(None)
        cu_pen = Cu2QuPen(tt_pen, max_err=1.0, reverse_direction=True)
        cstrings[g].draw(cu_pen)
        glyph = tt_pen.glyph()
        glyph.recalcBounds(None)
        tt_glyphs[g] = glyph
        if hasattr(glyph, "xMin") and glyph.numberOfContours > 0:
            ttf_metrics[g] = (metrics[g][0], glyph.xMin)
        else:
            ttf_metrics[g] = (metrics[g][0], 0)

    fb_ttf.setupGlyf(tt_glyphs)
    fb_ttf.setupHorizontalMetrics(ttf_metrics)
    fb_ttf.setupHorizontalHeader(ascent=880, descent=-120)
    fb_ttf.font['hhea'].lineGap = 0
    fb_ttf.setupNameTable(name_strings)
    fb_ttf.setupOS2(
        sTypoAscender=880, sTypoDescender=-120, sTypoLineGap=0,
        usWinAscent=880, usWinDescent=120,
        sxHeight=500, sCapHeight=800,
        usWeightClass=weight,
        fsSelection=fs_sel, fsType=0,
        panose=panose, achVendID='XMET',
        ulCodePageRange1=0x00040001, ulCodePageRange2=0x00000000
    )
    gasp = table__g_a_s_p()
    gasp.version = 1
    gasp.gaspRange = {65535: 0x000F}
    fb_ttf.font['gasp'] = gasp
    fb_ttf.setupPost()
    fb_ttf.font['head'].fontRevision = 2.0
    dsig_ttf = table_D_S_I_G_()
    dsig_ttf.ulVersion, dsig_ttf.usFlag, dsig_ttf.usNumSigs, dsig_ttf.signatureRecords = 1, 0, 0, []
    fb_ttf.font['DSIG'] = dsig_ttf

    ttf_out = os.path.join("fonts", "family_weights", f"XianMetro_StationNames_{file_suffix}.ttf")
    fb_ttf.save(ttf_out)

    ttf_objects.append(fb_ttf.font)
    print(f"  [{idx+1}/{len(styles)}] Created: {family_zh} · {zh_sub} ({en_sub}, W={weight}, Chars={len(style_mapping)})")

ttf_objects[0].save(os.path.join("fonts", "XianMetro_StationNames_Regular.ttf"))
ttf_objects[0].save(os.path.join("fonts", "XianMetro_StationNames.ttf"))

ttc_path = os.path.join("fonts", "XianMetro_StationNames.ttc")
ttc = TTCollection()
ttc.fonts = ttf_objects
ttc.save(ttc_path)

print(f"\nSuccessfully generated unified font collection:")
print(f"  TTC: {ttc_path} ({os.path.getsize(ttc_path):,} bytes)")
print(f"  Family weights in: fonts/family_weights/ (14 styles OTF + TTF)")
print(f"  Primary font: fonts/XianMetro_StationNames.ttf")






