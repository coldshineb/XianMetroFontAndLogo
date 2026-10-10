# -*- coding: utf-8 -*-
import os, sys, glob, json, re, shutil
from PIL import Image
import numpy as np

script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)
sys.path.insert(0, os.path.join(script_dir, ".deps"))
import cv2

def sanitize_station_name(name):
    clean = name.replace("&#183;", "").replace("·", "").replace("（", "").replace("）", "").replace("(", "").replace(")", "")
    clean = re.sub(r'[\s_/\\]+', '', clean)
    return clean

def extract_station_name_from_filename(filename, line_name=""):
    stem = os.path.splitext(filename)[0]
    if line_name and stem.startswith(line_name):
        stem = stem[len(line_name):].lstrip("_- ")
    stem = sanitize_station_name(stem)
    hans = re.findall(r'[\u4e00-\u9fa5]+', stem)
    if hans:
        return "".join(hans)
    return ""

def load_names_for_folder(folder_path, line_name, img_files):
    names_map = {}
    
    txt_candidates = [
        os.path.join(folder_path, "站名.txt"),
        os.path.join(folder_path, "stations.txt"),
        os.path.join(folder_path, f"{line_name}.txt")
    ]
    txt_names = []
    for cand in txt_candidates:
        if os.path.exists(cand):
            try:
                with open(cand, "r", encoding="utf-8") as f:
                    txt_names = [sanitize_station_name(line.strip()) for line in f if line.strip()]
                if txt_names:
                    print(f"从文本文件 {os.path.basename(cand)} 载入 {len(txt_names)} 个站名。")
                    break
            except Exception as e:
                pass

    mapping_json_names = []
    map_p = os.path.join(script_dir, "stations_verified_mapping.json")
    if os.path.exists(map_p):
        try:
            with open(map_p, "r", encoding="utf-8") as f:
                mapping = json.load(f)
            raw_list = mapping.get(line_name, [])
            mapping_json_names = [sanitize_station_name(n) for n in raw_list]
        except:
            pass

    for idx, fpath in enumerate(img_files):
        fname = os.path.basename(fpath)
        st_from_file = extract_station_name_from_filename(fname, line_name)
        if st_from_file:
            names_map[fpath] = st_from_file
        elif idx < len(txt_names):
            names_map[fpath] = txt_names[idx]
        elif idx < len(mapping_json_names):
            names_map[fpath] = mapping_json_names[idx]
        else:
            names_map[fpath] = ""

    return names_map

def slice_single_characters(img_path, station_name, line_name, out_root):
    chars = [c for c in station_name if '\u4e00' <= c <= '\u9fa5']
    n_chars = len(chars)
    if n_chars == 0:
        return []

    pil_img = Image.open(img_path)
    img_rgb = np.array(pil_img.convert("RGB"))
    gray = np.array(pil_img.convert("L"))
    bin_img = (gray < 140).astype(np.uint8) * 255
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(bin_img)

    cands = []
    for i in range(1, num_labels):
        x, y, w, h, area = stats[i]
        if 1000 <= y <= 1350 and 400 <= w <= 650 and 400 <= h <= 650:
            cands.append((x, y, w, h, area))

    if not cands:
        return []

    cands.sort(key=lambda c: c[4], reverse=True)
    seed = cands[0]
    seed_cx = seed[0] + seed[2] / 2
    seed_cy = seed[1] + seed[3] / 2

    cluster = [c for c in cands if abs((c[0] + c[2] / 2) - seed_cx) < 250 and abs((c[1] + c[3] / 2) - seed_cy) < 250]
    bx = min(c[0] for c in cluster)
    bw = max(c[0] + c[2] for c in cluster) - bx

    # 收集站名主体区域连通域（包含小点小笔画，不粗暴丢弃面积小部件）
    raw_blocks = []
    for i in range(1, num_labels):
        x, y, w, h, area = stats[i]
        if x < bx + bw - 15: continue
        if y > 1475 and y + h < 1620 and h < 95: continue
        if y + h < 1050: continue
        if 1020 <= y <= 1500:
            if area >= 30:  # 保留小点与细笔画
                raw_blocks.append([x, y, x + w, y + h, area])

    if not raw_blocks:
        return []

    # 第一阶段：将小点/小部件（面积<400）根据横向投影与距离并入最可能的主体，防止漏点漏撇
    major_blocks = [b for b in raw_blocks if b[4] >= 400]
    minor_blocks = [b for b in raw_blocks if b[4] < 400]

    if not major_blocks:
        major_blocks = raw_blocks
        minor_blocks = []

    for mb in minor_blocks:
        mx_mid = (mb[0] + mb[2]) / 2.0
        best_target = None
        best_dist = 999999
        for target in major_blocks:
            # 优先检查是否位于主部件的横向覆盖范围内
            if target[0] - 15 <= mx_mid <= target[2] + 15:
                dist = 0
            else:
                dist = min(abs(mb[2] - target[0]), abs(target[2] - mb[0]))
            if dist < best_dist:
                best_dist = dist
                best_target = target
        if best_target is not None and best_dist < 80:
            best_target[0] = min(best_target[0], mb[0])
            best_target[1] = min(best_target[1], mb[1])
            best_target[2] = max(best_target[2], mb[2])
            best_target[3] = max(best_target[3], mb[3])
            best_target[4] += mb[4]
        else:
            major_blocks.append(mb)

    blocks = sorted(major_blocks, key=lambda b: b[0])

    # 估算单个汉字的典型宽度基准
    total_span = blocks[-1][2] - blocks[0][0]
    exp_char_w = total_span / max(1, n_chars)

    # 第二阶段：结合相邻间隙与字宽惩罚进行合并，防止跨字粘连
    while len(blocks) > n_chars:
        best_score = 999999
        merge_idx = -1
        for i in range(len(blocks) - 1):
            b1, b2 = blocks[i], blocks[i + 1]
            gap = max(0, b2[0] - b1[2])
            merged_w = max(b1[2], b2[2]) - min(b1[0], b2[0])
            # 若合并后宽度严重超出单个汉字预期宽度，施加重惩罚，防止邻字部件错合
            width_penalty = max(0, merged_w - exp_char_w * 1.15) * 2.5
            score = gap + width_penalty
            if score < best_score:
                best_score = score
                merge_idx = i

        if merge_idx == -1:
            break
        b1, b2 = blocks[merge_idx], blocks[merge_idx + 1]
        new_b = [min(b1[0], b2[0]), min(b1[1], b2[1]), max(b1[2], b2[2]), max(b1[3], b2[3]), b1[4] + b2[4]]
        blocks = blocks[:merge_idx] + [new_b] + blocks[merge_idx+2:]

    if len(blocks) != n_chars:
        return []

    H, W, _ = img_rgb.shape
    new_records = []
    pad = 12

    for c_idx, (ch, b) in enumerate(zip(chars, blocks)):
        x1, y1, x2, y2 = b[0], b[1], b[2], b[3]
        cx1 = max(0, x1 - pad)
        cy1 = max(0, y1 - pad)
        cx2 = min(W, x2 + pad)
        cy2 = min(H, y2 + pad)

        char_crop = img_rgb[cy1:cy2, cx1:cx2]
        char_gray = gray[cy1:cy2, cx1:cx2]
        alpha = np.clip((242 - char_gray.astype(float)) / (242 - 60) * 255, 0, 255).astype(np.uint8)

        rgba = np.zeros((char_crop.shape[0], char_crop.shape[1], 4), dtype=np.uint8)
        rgba[:, :, :3] = 16
        rgba[:, :, 3] = alpha

        char_dir = os.path.join(out_root, ch)
        os.makedirs(char_dir, exist_ok=True)
        fname = f"{line_name}_{station_name}_{c_idx}.png"
        out_p = os.path.join(char_dir, fname)

        Image.fromarray(alpha, mode="L").save(out_p)

        norm_rel_p = os.path.join("extracted_instances", ch, fname).replace('/', '\\')
        w_box = x2 - x1
        h_box = y2 - y1

        rec = {
            "char": ch,
            "line": line_name,
            "station": station_name,
            "char_index": c_idx,
            "img_path": os.path.relpath(img_path, script_dir),
            "out_path": norm_rel_p,
            "width": int(w_box),
            "height": int(h_box)
        }
        new_records.append(rec)

    return new_records

def main():
    print("==================================================")
    print("      母牌站名自动识别与单字高精度切分程序        ")
    print("==================================================")

    img_root = os.path.join(script_dir, "metro_images")
    if not os.path.exists(img_root):
        print("未检测到 metro_images 目录。")
        return

    out_root = os.path.join(script_dir, "extracted_instances")
    os.makedirs(out_root, exist_ok=True)

    meta_path = os.path.join(script_dir, "character_instances_meta.json")
    all_meta = []
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            all_meta = json.load(f)

    existing_set = {r["out_path"].replace('/', '\\') for r in all_meta}
    existing_set.update({r["out_path"].replace('\\', '/') for r in all_meta})

    line_dirs = [d for d in os.listdir(img_root) if os.path.isdir(os.path.join(img_root, d))]
    
    total_new_sliced = 0
    for line_name in sorted(line_dirs):
        folder_p = os.path.join(img_root, line_name)
        img_files = sorted(glob.glob(os.path.join(folder_p, "*.jpg")) + glob.glob(os.path.join(folder_p, "*.png")))
        if not img_files: continue

        names_map = load_names_for_folder(folder_p, line_name, img_files)

        for img_p in img_files:
            st_name = names_map.get(img_p, "")
            if not st_name:
                continue

            chars = [c for c in st_name if '\u4e00' <= c <= '\u9fa5']
            already_done = True
            for c_idx, ch in enumerate(chars):
                expected_p = os.path.join("extracted_instances", ch, f"{line_name}_{st_name}_{c_idx}.png").replace('/', '\\')
                alt_p = os.path.join("extracted_instances", ch, f"{line_name}_西工程大西科大_{c_idx}.png").replace('/', '\\')
                if expected_p not in existing_set and alt_p not in existing_set:
                    already_done = False
                    break

            if already_done:
                continue

            recs = slice_single_characters(img_p, st_name, line_name, out_root)
            if recs:
                print(f"  成功切分车站 [{st_name}]: {len(recs)} 个单字已存入 extracted_instances")
                for r in recs:
                    all_meta.append(r)
                    existing_set.add(r["out_path"].replace('/', '\\'))
                total_new_sliced += len(recs)

    if total_new_sliced > 0:
        print(f"\n全部切片完成！共新增切出 {total_new_sliced} 个单字。更新元数据...")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(all_meta, f, ensure_ascii=False, indent=2)
    else:
        print("\n所有母牌图样均已在库中，无待切片新图样。")

if __name__ == "__main__":
    main()
