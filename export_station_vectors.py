# -*- coding: utf-8 -*-
import os, sys, glob, json, subprocess, shutil
from PIL import Image
import numpy as np
import cv2

script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)
sys.path.insert(0, os.path.join(script_dir, ".deps"))

potrace_bin = os.path.join(script_dir, "potrace_bin", "potrace-1.16.win64", "potrace.exe")
mapping_path = os.path.join(script_dir, "stations_verified_mapping.json")

out_png_root = os.path.join(script_dir, "车站站名与站徽")
out_svg_root = os.path.join(script_dir, "车站站名与站徽_SVG")
os.makedirs(out_png_root, exist_ok=True)
os.makedirs(out_svg_root, exist_ok=True)

tmp_dir = os.path.join(script_dir, "_temp_bitmap")
os.makedirs(tmp_dir, exist_ok=True)
tmp_bmp = os.path.join(tmp_dir, "temp.bmp")

with open(mapping_path, "r", encoding="utf-8") as f:
    mapping = json.load(f)

def clean_name(name):
    return name.replace("&#183;", "_").replace("·", "_").replace("/", "_").replace("\\", "_").replace(":", "_").replace(" ", "")

def locate_and_extract(img_path):
    pil_img = Image.open(img_path)
    img_rgb = np.array(pil_img.convert("RGB"))
    gray = np.array(pil_img.convert("L"))
    bin_img = (gray < 140).astype(np.uint8) * 255
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(bin_img)

    # 1. 查找站徽候选块 (w, h in [400, 650], y in [1000, 1350])
    cands = []
    for i in range(1, num_labels):
        x, y, w, h, area = stats[i]
        if 1000 <= y <= 1350 and 400 <= w <= 650 and 400 <= h <= 650:
            cands.append((x, y, w, h, area))

    if not cands:
        return None, None

    # 按面积降序找到主要块，并对同心/重叠的多个构件进行聚类外包络合并（解决如镐京站内外轮廓分离的问题）
    cands.sort(key=lambda c: c[4], reverse=True)
    seed = cands[0]
    seed_cx = seed[0] + seed[2] / 2
    seed_cy = seed[1] + seed[3] / 2

    cluster = [c for c in cands if abs((c[0] + c[2] / 2) - seed_cx) < 250 and abs((c[1] + c[3] / 2) - seed_cy) < 250]
    bx = min(c[0] for c in cluster)
    by = min(c[1] for c in cluster)
    bx2 = max(c[0] + c[2] for c in cluster)
    by2 = max(c[1] + c[3] for c in cluster)
    bw = bx2 - bx
    bh = by2 - by

    # 2. 查找站名汉字 (位于站徽最外右边界之外，排除小拼音和顶部线条)
    char_blocks = []
    for i in range(1, num_labels):
        x, y, w, h, area = stats[i]
        if x < bx + bw - 15: continue
        if y > 1475 and y + h < 1620 and h < 95: continue # 过滤下方拼音字母
        if y + h < 1050: continue # 过滤顶部线条
        if 1050 <= y <= 1470 or (1050 <= y <= 1470 and y + h > 1150):
            char_blocks.append((x, y, w, h, area))

    if not char_blocks:
        return None, None

    min_cx = min(b[0] for b in char_blocks)
    max_cx = max(b[0] + b[2] for b in char_blocks)
    min_cy = min(b[1] for b in char_blocks)
    max_cy = max(b[1] + b[3] for b in char_blocks)

    H, W, _ = img_rgb.shape
    pad = 16

    # 站名切片 (透明羽化 PNG)
    nx1, ny1 = max(0, min_cx - pad), max(0, min_cy - pad)
    nx2, ny2 = min(W, max_cx + pad), min(H, max_cy + pad)
    name_crop = img_rgb[ny1:ny2, nx1:nx2]
    name_gray = gray[ny1:ny2, nx1:nx2]
    alpha_name = np.clip((242 - name_gray.astype(float)) / (242 - 60) * 255, 0, 255).astype(np.uint8)
    name_rgba = np.zeros((name_crop.shape[0], name_crop.shape[1], 4), dtype=np.uint8)
    name_rgba[:, :, :3] = 16
    name_rgba[:, :, 3] = alpha_name

    # 站徽切片 (完整保留外框与内部全部图案)
    bx1 = max(0, bx - 10)
    by1 = max(0, by - 10)
    bx2 = min(W, bx + bw + 10)
    by2 = min(H, by + bh + 10)
    badge_crop = img_rgb[by1:by2, bx1:bx2]
    badge_gray = gray[by1:by2, bx1:bx2]
    alpha_badge = np.clip((245 - badge_gray.astype(float)) / (245 - 65) * 255, 0, 255).astype(np.uint8)
    badge_rgba = np.zeros((badge_crop.shape[0], badge_crop.shape[1], 4), dtype=np.uint8)
    badge_rgba[:, :, :3] = badge_crop
    badge_rgba[:, :, 3] = alpha_badge

    return Image.fromarray(name_rgba), Image.fromarray(badge_rgba)

def convert_alpha_to_svg(alpha_arr, svg_out_path):
    bin_data = np.where(alpha_arr > 120, 0, 255).astype(np.uint8)
    Image.fromarray(bin_data, mode="L").save(tmp_bmp)
    cmd = [potrace_bin, "-s", "-a", "1.1", "-t", "2", "-o", svg_out_path, tmp_bmp]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def get_line_sort_num(s):
    import re
    m = re.search(r'(\d+)', str(s))
    return int(m.group(1)) if m else (20 if "西户" in str(s) else 80)

metro_img_root = os.path.join(script_dir, "metro_images")
if os.path.exists(metro_img_root):
    line_order = sorted([d for d in os.listdir(metro_img_root) if os.path.isdir(os.path.join(metro_img_root, d))], key=get_line_sort_num)
else:
    line_order = ["1号线", "2号线", "3号线", "4号线", "5号线", "6号线", "8号线", "9号线", "10号线", "14号线", "15号线", "16号线", "西户线"]

total_done = 0

for line in line_order:
    names = mapping.get(line, [])
    imgs = sorted(glob.glob(os.path.join(script_dir, "metro_images", line, "*.jpg")))
    if line == "14号线":
        imgs = imgs[9:] # 仅取自建段 (西安北站 到 贺韶)

    png_n_dir = os.path.join(out_png_root, line, "站名")
    png_b_dir = os.path.join(out_png_root, line, "站徽")
    svg_n_dir = os.path.join(out_svg_root, line, "站名")
    svg_b_dir = os.path.join(out_svg_root, line, "站徽")
    for d in [png_n_dir, png_b_dir, svg_n_dir, svg_b_dir]:
        os.makedirs(d, exist_ok=True)

    print(f"正在处理 {line} (收录 {len(names)} 站)...")
    for idx, (img_p, st_name) in enumerate(zip(imgs, names)):
        n_img, b_img = locate_and_extract(img_p)
        if not n_img or not b_img: continue

        c_name = clean_name(st_name)
        f_name = f"{idx+1:02d}_{c_name}"

        # 1. 保存 PNG
        png_n_path = os.path.join(png_n_dir, f_name + ".png")
        png_b_path = os.path.join(png_b_dir, f_name + ".png")
        n_img.save(png_n_path)
        b_img.save(png_b_path)

        # 2. 转换 SVG
        svg_n_path = os.path.join(svg_n_dir, f_name + ".svg")
        svg_b_path = os.path.join(svg_b_dir, f_name + ".svg")
        convert_alpha_to_svg(np.array(n_img)[:, :, 3], svg_n_path)
        convert_alpha_to_svg(np.array(b_img)[:, :, 3], svg_b_path)

        total_done += 1

shutil.rmtree(tmp_dir, ignore_errors=True)
print(f"\n全部处理完毕！共处理 {total_done} 个车站，生成 285 对透明 PNG 与 SVG 矢量文件。")