# -*- coding: utf-8 -*-
import os, sys, glob, json, pickle, subprocess, time, tempfile, shutil
import multiprocessing as mp
from PIL import Image
import numpy as np
import xml.etree.ElementTree as ET

script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(script_dir, ".deps"))

from svg.path import parse_path, Move, Line, CubicBezier
from fontTools.pens.t2CharStringPen import T2CharStringPen

temp_dir_base = tempfile.gettempdir()
potrace_src = os.path.join(script_dir, "potrace_bin", "potrace-1.16.win64", "potrace.exe")
potrace_exe = os.path.join(temp_dir_base, "metro_potrace.exe")
if not os.path.exists(potrace_exe) or os.path.getsize(potrace_exe) != os.path.getsize(potrace_src):
    shutil.copy2(potrace_src, potrace_exe)

TARGET_VISUAL_H = 765.0

def eval_cubic_bezier(p0, p1, p2, p3, t):
    mt = 1.0 - t
    return (
        mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0],
        mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
    )

def process_one_instance(task_info):
    idx, rel_path, station_ref_h, char_text = task_info
    full_path = os.path.join(script_dir, rel_path)
    if not os.path.exists(full_path):
        return rel_path, None

    try:
        mtime = os.path.getmtime(full_path)
        fsize = os.path.getsize(full_path)
    except:
        mtime, fsize = 0.0, 0

    img = Image.open(full_path)
    arr = np.array(img)
    if len(arr.shape) == 3:
        if arr.shape[2] == 4:
            alpha = arr[:, :, 3]
            bin_arr = np.where(alpha > 120, 0, 255).astype(np.uint8)
        else:
            gray = np.mean(arr[:, :, :3], axis=2)
            bin_arr = np.where(gray > 120, 0, 255).astype(np.uint8)
    else:
        bin_arr = np.where(arr > 120, 0, 255).astype(np.uint8)

    tmp_dir = os.path.join(temp_dir_base, "_metro_potrace", f"p_{os.getpid()}")
    os.makedirs(tmp_dir, exist_ok=True)
    bmp_path = os.path.join(tmp_dir, f"t_{idx}.bmp")
    svg_path = os.path.join(tmp_dir, f"t_{idx}.svg")

    Image.fromarray(bin_arr).save(bmp_path)
    cmd = [potrace_exe, "-s", "-a", "1.1", "-t", "2", "-o", svg_path, bmp_path]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    tree = ET.parse(svg_path)
    root = tree.getroot()
    paths = root.findall('.//{http://www.w3.org/2000/svg}path') or root.findall('.//path')

    contours = []
    real_ink_pts = []
    for p_elem in paths:
        d_str = p_elem.attrib.get('d', '')
        if not d_str: continue
        p = parse_path(d_str)
        contour = []
        for seg in p:
            if isinstance(seg, Move):
                pt = (seg.start.real * 0.1, seg.start.imag * 0.1)
                contour.append(('move', pt))
                real_ink_pts.append(pt)
            elif isinstance(seg, Line):
                pt = (seg.end.real * 0.1, seg.end.imag * 0.1)
                contour.append(('line', pt))
                real_ink_pts.append(pt)
            elif isinstance(seg, CubicBezier):
                p0 = (seg.start.real * 0.1, seg.start.imag * 0.1)
                c1 = (seg.control1.real * 0.1, seg.control1.imag * 0.1)
                c2 = (seg.control2.real * 0.1, seg.control2.imag * 0.1)
                p3 = (seg.end.real * 0.1, seg.end.imag * 0.1)
                contour.append(('cubic', c1, c2, p3))
                # 采样曲线真实点集以获取精确墨迹边界，消除控制点外凸误差
                real_ink_pts.append(p0)
                real_ink_pts.append(p3)
                for step in (0.2, 0.4, 0.6, 0.8):
                    real_ink_pts.append(eval_cubic_bezier(p0, c1, c2, p3, step))
        contours.append(contour)

    try:
        os.remove(bmp_path)
        os.remove(svg_path)
    except: pass

    if not real_ink_pts:
        return rel_path, None

    xs = [pt[0] for pt in real_ink_pts]
    ys = [pt[1] for pt in real_ink_pts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    raw_w = max_x - min_x
    raw_h = max_y - min_y

    scale = TARGET_VISUAL_H / float(station_ref_h)

    # 针对小体量或扁平字形进行视觉平准补偿
    scaled_h_pre = raw_h * scale
    scaled_w_pre = raw_w * scale
    if scaled_h_pre < 580.0 and scaled_w_pre < 740.0:
        boost = min(1.20, 580.0 / scaled_h_pre)
        scale *= boost

    if raw_w * scale > 960.0:
        scale = 960.0 / raw_w

    scaled_w = raw_w * scale
    scaled_h = raw_h * scale

    adv_width = 1000
    side_bearing = (adv_width - scaled_w) / 2.0
    offset_x = side_bearing - min_x * scale
    offset_y = 370.0 - ((min_y + max_y) / 2.0) * scale

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

    lsb = round(side_bearing)
    c_str = pen.getCharString()
    # 缓存记录中存入 mtime 与 file_size，以便未来图片更新时自动感知并失效旧缓存
    return rel_path, (c_str, adv_width, lsb, mtime, fsize)

def scan_and_sync_instances():
    meta_path = "character_instances_meta.json"
    records = []
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            records = json.load(f)

    existing_paths = {r["out_path"].replace('/', '\\') for r in records}
    existing_paths.update({r["out_path"].replace('\\', '/') for r in records})

    added_count = 0
    inst_root = "extracted_instances"
    if os.path.exists(inst_root):
        for char_dir in os.listdir(inst_root):
            full_char_dir = os.path.join(inst_root, char_dir)
            if not os.path.isdir(full_char_dir) or char_dir == "符号":
                continue
            for fname in os.listdir(full_char_dir):
                if not fname.lower().endswith(".png"):
                    continue
                rel_p = os.path.join(inst_root, char_dir, fname)
                norm_p = rel_p.replace('/', '\\')
                if norm_p in existing_paths or rel_p.replace('\\', '/') in existing_paths:
                    continue

                stem = os.path.splitext(fname)[0]
                parts = stem.split("_")
                if len(parts) >= 3:
                    line_name = parts[0]
                    st_name = parts[1]
                    try:
                        c_idx = int(parts[2])
                    except:
                        c_idx = 0
                else:
                    line_name = "新线"
                    st_name = stem
                    c_idx = 0

                try:
                    with Image.open(rel_p) as im:
                        w, h = im.size
                except:
                    w, h = 234, 234

                new_rec = {
                    "char": char_dir,
                    "line": line_name,
                    "station": st_name,
                    "char_index": c_idx,
                    "img_path": "",
                    "out_path": norm_p,
                    "width": w,
                    "height": h
                }
                records.append(new_rec)
                existing_paths.add(norm_p)
                added_count += 1

    if added_count > 0:
        print(f"检测到 {added_count} 个新切片，自动同步至元数据...")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

        picker_path = "font_character_picker.html"
        if os.path.exists(picker_path):
            try:
                with open(picker_path, "r", encoding="utf-8") as f:
                    html_content = f.read()
                prefix = "const allRecords = "
                idx1 = html_content.find(prefix)
                if idx1 != -1:
                    idx2 = html_content.find(";\n", idx1)
                    if idx2 != -1:
                        new_json_str = json.dumps(records, ensure_ascii=False)
                        html_content = html_content[:idx1 + len(prefix)] + new_json_str + html_content[idx2:]
                        with open(picker_path, "w", encoding="utf-8") as f:
                            f.write(html_content)
                        print("已将最新元数据同步至网页选字面板。")
            except Exception as e:
                print(f"提示: 同步网页选字面板暂缓: {e}")
    else:
        print("未检测到新增切片，元数据维持最新。")

    return records

def get_station_ref_h_map(records):
    st_heights = {}
    for r in records:
        h = float(r.get("height", 234.0))
        if 100.0 <= h <= 280.0:
            st_key = (r.get("line", ""), r.get("station", ""))
            st_heights.setdefault(st_key, []).append(h)

    st_ref_h_map = {}
    for st_key, hs in st_heights.items():
        med = float(np.median(hs))
        st_ref_h_map[st_key] = min(240.0, max(228.0, med))
    return st_ref_h_map

def main():
    force_all = "--force" in sys.argv
    records = scan_and_sync_instances()
    st_ref_h_map = get_station_ref_h_map(records)

    cache_path = "glyph_cache.pkl"
    cache = {}
    if os.path.exists(cache_path) and not force_all:
        try:
            with open(cache_path, "rb") as f:
                raw = pickle.load(f)
            for k, v in raw.items():
                cache[k] = v
                cache[k.replace('/', '\\')] = v
                cache[k.replace('\\', '/')] = v
            print(f"已载入现有字模缓存: {len(raw)} 项")
        except Exception as e:
            print(f"缓存载入异常，将重新构建: {e}")
            cache = {}

    pending_tasks = []
    for idx, r in enumerate(records):
        p = r["out_path"]
        norm_w = p.replace('/', '\\')
        norm_u = p.replace('\\', '/')
        
        # 缓存有效性校验：检查是否存在、以及图片修改时间/大小是否变动
        needs_rebuild = force_all
        cached_entry = cache.get(norm_w) or cache.get(norm_u)
        
        if not needs_rebuild:
            if cached_entry is None:
                needs_rebuild = True
            elif len(cached_entry) >= 5:
                # 校验 mtime 与 file_size
                _, _, _, c_mtime, c_fsize = cached_entry
                full_p = os.path.join(script_dir, p)
                if os.path.exists(full_p):
                    cur_mtime = os.path.getmtime(full_p)
                    cur_fsize = os.path.getsize(full_p)
                    if cur_mtime != c_mtime or cur_fsize != c_fsize:
                        needs_rebuild = True
                else:
                    needs_rebuild = True

        if needs_rebuild:
            st_h = st_ref_h_map.get((r.get("line", ""), r.get("station", "")), 234.0)
            pending_tasks.append((idx, p, st_h, r.get("char", "")))

    if pending_tasks:
        print(f"需要提取矢量的切片数: {len(pending_tasks)}")
        cpu_cnt = min(8, mp.cpu_count())
        t0 = time.time()
        with mp.Pool(processes=cpu_cnt) as pool:
            for idx, (p, res) in enumerate(pool.imap_unordered(process_one_instance, pending_tasks, chunksize=16)):
                if res is not None:
                    cache[p] = res
                    cache[p.replace('/', '\\')] = res
                    cache[p.replace('\\', '/')] = res
                if (idx + 1) % 100 == 0 or (idx + 1) == len(pending_tasks):
                    print(f"提取进度: {idx + 1}/{len(pending_tasks)} (耗时: {time.time() - t0:.1f}s)")

        with open(cache_path, "wb") as f:
            pickle.dump(cache, f)
        print(f"字模缓存更新完成，总收录字形数: {len(cache)}")
    else:
        print("全部切片矢量均有效且在缓存中，无需重复计算。")

    shutil.rmtree(os.path.join(temp_dir_base, "_metro_potrace"), ignore_errors=True)

if __name__ == "__main__":
    mp.freeze_support()
    main()
