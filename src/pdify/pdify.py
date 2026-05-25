import os
import json
import argparse
import sys
import glob
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
import pdfplumber

def create_directories(base_dir="extracted_content"):
    """Creates the output directory structure."""
    text_dir = os.path.join(base_dir, "text")
    image_dir = os.path.join(base_dir, "images")
    os.makedirs(text_dir, exist_ok=True)
    os.makedirs(image_dir, exist_ok=True)
    return text_dir, image_dir

def group_words_into_lines(words, y_tolerance=3.0):
    """Groups words into distinct lines based on vertical overlap/closeness."""
    if not words:
        return []
    
    sorted_words = sorted(words, key=lambda w: w['top'])
    lines = []
    
    for w in sorted_words:
        matched_line = None
        # Check the last few lines to handle multi-column vertical misalignments
        for line in reversed(lines[-5:]):
            if abs(w['top'] - line['top_avg']) < y_tolerance:
                matched_line = line
                break
        
        if matched_line:
            matched_line['words'].append(w)
            matched_line['top_avg'] = sum(x['top'] for x in matched_line['words']) / len(matched_line['words'])
        else:
            lines.append({
                'top_avg': w['top'],
                'words': [w]
            })
            
    # Sort words within each line from left to right
    for line in lines:
        line['words'] = sorted(line['words'], key=lambda w: w['x0'])
    
    # Sort lines by their top coordinates
    return sorted(lines, key=lambda l: l['top_avg'])

def split_line_into_segments(line, gap_threshold=15.0):
    """Splits a line of words into distinct segments if a horizontal gap is larger than threshold."""
    words = line['words']
    if not words:
        return []
    
    segments = []
    current_seg = [words[0]]
    
    for w in words[1:]:
        prev_w = current_seg[-1]
        gap = w['x0'] - prev_w['x1']
        if gap > gap_threshold:
            segments.append(current_seg)
            current_seg = [w]
        else:
            current_seg.append(w)
    segments.append(current_seg)
    
    formatted_segments = []
    for seg in segments:
        x0 = min(w['x0'] for w in seg)
        x1 = max(w['x1'] for w in seg)
        text = " ".join(w['text'] for w in seg)
        top = min(w['top'] for w in seg)
        bottom = max(w['bottom'] for w in seg)
        formatted_segments.append({
            'x0': x0,
            'x1': x1,
            'top': top,
            'bottom': bottom,
            'text': text,
            'words': seg
        })
    return formatted_segments

def clean_text(text):
    """Replaces standard unicode ligatures and cleans up common encoding noise."""
    if not text:
        return ""
    replacements = {
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬀ": "ff",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
        "ﬆ": "st",
        "(cid:0)": "",  # Strip CID-0 placeholders
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text

def reconstruct_paragraphs_from_lines(lines, dehyphenate=True):
    """
    Intelligently joins lines within a text column to form cohesive paragraphs,
    using line lengths, ending punctuation, and list/bullet markers.
    """
    if not lines:
        return ""
        
    paragraphs = []
    current_para = []
    
    non_empty_lines = [l.strip() for l in lines if l.strip()]
    if not non_empty_lines:
        return ""
    
    avg_len = sum(len(l) for l in non_empty_lines) / len(non_empty_lines)
    
    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue
            
        if not current_para:
            current_para.append(line_clean)
        else:
            prev_line = current_para[-1]
            
            is_hyphenated = False
            if dehyphenate and prev_line.endswith("-") and len(prev_line) > 1 and prev_line[-2].isalpha():
                is_hyphenated = True
                
            is_new_para = False
            
            # Check for list/section markers:
            if line_clean.startswith(("- ", "• ", "* ", "1. ", "2. ", "3. ", "4. ", "5. ", "6. ", "7. ", "8. ", "9. ")):
                is_new_para = True
            elif prev_line[-1] in ".?!" and len(prev_line) < (avg_len * 0.85):
                # Ends in punctuation and is short
                is_new_para = True
                
            if is_new_para and not is_hyphenated:
                paragraphs.append(" ".join(current_para))
                current_para = [line_clean]
            else:
                if is_hyphenated:
                    # Strip the hyphen and merge the first word directly
                    parts = line_clean.split(maxsplit=1)
                    if parts:
                        first_word = parts[0]
                        rest = parts[1] if len(parts) > 1 else ""
                        current_para[-1] = prev_line[:-1] + first_word
                        if rest:
                            current_para.append(rest)
                    else:
                        current_para[-1] = prev_line[:-1]
                else:
                    current_para.append(line_clean)
                    
    if current_para:
        paragraphs.append(" ".join(current_para))
        
    return "\n\n".join(paragraphs)

def extract_text_and_words_with_layout(
    page, 
    x_tolerance=1.5, 
    y_tolerance=3.0, 
    gap_threshold=15.0,
    remove_headers_footers=True,
    top_margin=50.0,
    bottom_margin=50.0,
    keep_page1_header=True,
    exclude_figure_text=True,
    figure_boxes=None,
    dehyphenate=True,
    reconstruct_paragraphs=True
):
    """
    Extracts text and word list in correct visual reading order,
    de-segmenting columns while retaining titles and section headers spans.
    """
    words = page.extract_words(x_tolerance=x_tolerance, y_tolerance=y_tolerance)
    if not words:
        return "", []
        
    page_num = page.page_number
    page_height = float(page.height)
    
    filtered_words = []
    for w in words:
        w_text = clean_text(w["text"])
        if not w_text.strip():
            continue
            
        w = dict(w)
        w["text"] = w_text
        
        # 1. Header and footer removal
        if remove_headers_footers:
            is_header = False
            # For page 1, optionally keep the top margin (title/authors)
            if page_num > 1 or not keep_page1_header:
                if w["top"] < top_margin:
                    is_header = True
            
            is_footer = w["bottom"] > (page_height - bottom_margin)
            
            if is_header or is_footer:
                continue
                
        # 2. Exclude figure text
        if exclude_figure_text and figure_boxes:
            in_figure = False
            w_cx = (w["x0"] + w["x1"]) / 2.0
            w_cy = (w["top"] + w["bottom"]) / 2.0
            for box in figure_boxes:
                if box[0] <= w_cx <= box[2] and box[1] <= w_cy <= box[3]:
                    in_figure = True
                    break
            if in_figure:
                continue
                
        filtered_words.append(w)
        
    if not filtered_words:
        return "", []
        
    lines = group_words_into_lines(filtered_words, y_tolerance=y_tolerance)
    
    blocks_text = []
    blocks_words = []
    active_cols = []
    
    def flush_active_cols():
        if not active_cols:
            return
        # Output accumulated columns from left to right
        for col in sorted(active_cols, key=lambda c: c['x0']):
            if col['lines_text']:
                if reconstruct_paragraphs:
                    para_text = reconstruct_paragraphs_from_lines(col['lines_text'], dehyphenate=dehyphenate)
                    if para_text:
                        blocks_text.append(para_text)
                else:
                    blocks_text.append("\n".join(col['lines_text']))
                blocks_words.extend(col['lines_words'])
        active_cols.clear()
        
    for line in lines:
        segments = split_line_into_segments(line, gap_threshold=gap_threshold)
        if not segments:
            continue
            
        m = len(active_cols)
        k = len(segments)
        is_transition = False
        
        if m > 0:
            if m == 1 and k > 1:
                # Transitioning from single column to multiple columns
                is_transition = True
            elif m > 1 and k == 1:
                # Check if the single segment spans across/overlaps multiple active columns
                seg = segments[0]
                overlapping_count = 0
                for col in active_cols:
                    overlap = max(0, min(seg['x1'], col['x1']) - max(seg['x0'], col['x0']))
                    if overlap > 5:
                        overlapping_count += 1
                if overlapping_count > 1:
                    is_transition = True
            elif m > 1 and k > 1:
                # Check if any segment overlaps with multiple active columns
                for seg in segments:
                    overlapping_count = 0
                    for col in active_cols:
                        overlap = max(0, min(seg['x1'], col['x1']) - max(seg['x0'], col['x0']))
                        if overlap > 5:
                            overlapping_count += 1
                    if overlapping_count > 1:
                        is_transition = True
                        break
        
        if is_transition:
            flush_active_cols()
            
        if not active_cols:
            # Create new column anchors
            for seg in segments:
                active_cols.append({
                    'x0': seg['x0'],
                    'x1': seg['x1'],
                    'lines_text': [seg['text']],
                    'lines_words': seg['words']
                })
        else:
            # Match each segment to the best active column by horizontal overlap
            matched_indices = set()
            for seg in segments:
                best_col_idx = None
                max_overlap = -1
                for idx, col in enumerate(active_cols):
                    overlap = max(0, min(seg['x1'], col['x1']) - max(seg['x0'], col['x0']))
                    if overlap > 0 and overlap > max_overlap:
                        max_overlap = overlap
                        best_col_idx = idx
                
                if best_col_idx is not None and best_col_idx not in matched_indices:
                    col = active_cols[best_col_idx]
                    col['x0'] = min(col['x0'], seg['x0'])
                    col['x1'] = max(col['x1'], seg['x1'])
                    col['lines_text'].append(seg['text'])
                    col['lines_words'].extend(seg['words'])
                    matched_indices.add(best_col_idx)
                else:
                    # If no match or already matched, treat as a new column
                    active_cols.append({
                        'x0': seg['x0'],
                        'x1': seg['x1'],
                        'lines_text': [seg['text']],
                        'lines_words': seg['words']
                    })
                    
    flush_active_cols()
    return "\n\n".join(blocks_text), blocks_words

def get_cohesive_figure_boxes(page):
    """
    Attempts to find entire figure bounding boxes by grouping nearby image elements 
    or using vector/figure graphic boundaries, preventing tiny fragmented crops.
    Filters out background wrappers or full-page decorative shapes.
    """
    raw_boxes = []
    
    # 1. Collect from images
    if page.images:
        for img in page.images:
            img_w = float(img["width"])
            img_h = float(img["height"])
            if img_w < page.width * 0.95 or img_h < page.height * 0.95:
                raw_boxes.append([float(img["x0"]), float(img["top"]), float(img["x1"]), float(img["bottom"])])
                
    # 2. Collect from figure and rect containers
    containers = page.objects.get("figure", []) + page.objects.get("rect", [])
    for c in containers:
        width = float(c["width"])
        height = float(c["height"])
        if width > 50 and height > 50:
            if width < page.width * 0.95 or height < page.height * 0.95:
                raw_boxes.append([float(c["x0"]), float(c["top"]), float(c["x1"]), float(c["bottom"])])
                
    if not raw_boxes:
        return []
        
    # 3. Merge overlapping or close boxes
    tolerance = 15.0
    merged = True
    while merged:
        merged = False
        n = len(raw_boxes)
        for i in range(n):
            for j in range(i + 1, n):
                b1 = raw_boxes[i]
                b2 = raw_boxes[j]
                
                # Check for 2D overlap / closeness with tolerance
                is_close = not (b1[2] < b2[0] - tolerance or b1[0] > b2[2] + tolerance or
                                 b1[3] < b2[1] - tolerance or b1[1] > b2[3] + tolerance)
                
                if is_close:
                    raw_boxes[i] = [
                        min(b1[0], b2[0]),
                        min(b1[1], b2[1]),
                        max(b1[2], b2[2]),
                        max(b1[3], b2[3])
                    ]
                    raw_boxes.pop(j)
                    merged = True
                    break
            if merged:
                break
                
    # 4. Filter out any remaining nested boxes
    final_boxes = []
    for b in raw_boxes:
        is_inside = False
        for other in raw_boxes:
            if b is other:
                continue
            # Check if 'other' contains 'b' with a 2-point margin
            if (other[0] - 2.0 <= b[0] and other[1] - 2.0 <= b[1] and
                b[2] <= other[2] + 2.0 and b[3] <= other[3] + 2.0):
                area_other = (other[2] - other[0]) * (other[3] - other[1])
                area_b = (b[2] - b[0]) * (b[3] - b[1])
                if area_other > area_b:
                    is_inside = True
                    break
        if not is_inside:
            box_tuple = tuple(b)
            if box_tuple not in final_boxes:
                final_boxes.append(box_tuple)
            
    return final_boxes

def process_single_page(args_dict):
    """
    Processes a single page of a PDF file.
    Takes a dictionary containing all parameters to avoid pickling issues.
    """
    pdf_path = args_dict["pdf_path"]
    page_num = args_dict["page_num"]
    output_base = args_dict["output_base"]
    x_tolerance = args_dict["x_tolerance"]
    y_tolerance = args_dict["y_tolerance"]
    gap_threshold = args_dict["gap_threshold"]
    resolution = args_dict["resolution"]
    remove_headers_footers = args_dict["remove_headers_footers"]
    top_margin = args_dict["top_margin"]
    bottom_margin = args_dict["bottom_margin"]
    keep_page1_header = args_dict["keep_page1_header"]
    exclude_figure_text = args_dict["exclude_figure_text"]
    dehyphenate = args_dict["dehyphenate"]
    reconstruct_paragraphs = args_dict["reconstruct_paragraphs"]
    
    # Sub-directory based on the PDF name
    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
    pdf_output_dir = os.path.join(output_base, pdf_name)
    text_dir = os.path.join(pdf_output_dir, "text")
    image_dir = os.path.join(pdf_output_dir, "images")
    
    os.makedirs(text_dir, exist_ok=True)
    os.makedirs(image_dir, exist_ok=True)
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[page_num - 1]
            
            # 1. Get figure boxes first
            figure_boxes = get_cohesive_figure_boxes(page)
            
            # 2. Extract text layout
            text_content, words = extract_text_and_words_with_layout(
                page,
                x_tolerance=x_tolerance,
                y_tolerance=y_tolerance,
                gap_threshold=gap_threshold,
                remove_headers_footers=remove_headers_footers,
                top_margin=top_margin,
                bottom_margin=bottom_margin,
                keep_page1_header=keep_page1_header,
                exclude_figure_text=exclude_figure_text,
                figure_boxes=figure_boxes,
                dehyphenate=dehyphenate,
                reconstruct_paragraphs=reconstruct_paragraphs
            )
            
            if words:
                text_filename = f"page_{page_num}_text.txt"
                text_path = os.path.join(text_dir, text_filename)
                
                with open(text_path, "w", encoding="utf-8") as f:
                    f.write(text_content if text_content else "")
                
                text_meta = {
                    "source_file": os.path.basename(pdf_path),
                    "page_number": page_num,
                    "page_width": float(page.width),
                    "page_height": float(page.height),
                    "text_file_path": text_path,
                    "word_coordinates": [
                        {
                            "text": w["text"],
                            "x0": round(float(w["x0"]), 2),
                            "top": round(float(w["top"]), 2),
                            "x1": round(float(w["x1"]), 2),
                            "bottom": round(float(w["bottom"]), 2)
                        }
                        for w in words
                    ]
                }
                with open(os.path.join(text_dir, f"page_{page_num}_text_metadata.json"), "w") as f:
                    json.dump(text_meta, f, indent=4)
                    
            # 3. Extract whole figures efficiently by rendering page once
            if figure_boxes:
                try:
                    page_img = page.to_image(resolution=resolution).original
                    scale_x = page_img.width / float(page.width)
                    scale_y = page_img.height / float(page.height)
                except Exception as e:
                    return pdf_path, page_num, False, f"Could not render page for figure extraction: {e}"
                
                for fig_idx, raw_bbox in enumerate(figure_boxes):
                    x0 = max(0, raw_bbox[0])
                    top = max(0, raw_bbox[1])
                    x1 = min(float(page.width), raw_bbox[2])
                    bottom = min(float(page.height), raw_bbox[3])
                    
                    if x1 <= x0 or bottom <= top:
                        continue
                        
                    bbox = (x0, top, x1, bottom)
                    
                    try:
                        crop_box = (
                            int(x0 * scale_x),
                            int(top * scale_y),
                            int(x1 * scale_x),
                            int(bottom * scale_y)
                        )
                        pil_img = page_img.crop(crop_box)
                        
                        img_filename = f"page_{page_num}_figure_{fig_idx + 1}.png"
                        img_path = os.path.join(image_dir, img_filename)
                        
                        pil_img.save(img_path)
                        
                        img_meta = {
                            "source_file": os.path.basename(pdf_path),
                            "page_number": page_num,
                            "figure_index": fig_idx + 1,
                            "image_file_path": img_path,
                            "bounding_box": {
                                "x0": round(x0, 2),
                                "top": round(top, 2),
                                "x1": round(x1, 2),
                                "bottom": round(bottom, 2),
                                "width": round(x1 - x0, 2),
                                "height": round(bottom - top, 2)
                            }
                        }
                        
                        with open(os.path.join(image_dir, f"page_{page_num}_figure_{fig_idx + 1}_metadata.json"), "w") as f:
                            json.dump(img_meta, f, indent=4)
                    except Exception as e:
                        print(f"   [Warning] Could not crop figure {fig_idx + 1} on page {page_num} of {pdf_name}: {e}")
                        
        return pdf_path, page_num, True, None
    except Exception as e:
        return pdf_path, page_num, False, str(e)

def resolve_input_paths(input_args):
    """Resolves directories, globs, and direct file paths to a list of PDF file paths."""
    resolved_paths = []
    for pattern in input_args:
        if os.path.isdir(pattern):
            dir_pdfs = glob.glob(os.path.join(pattern, "*.pdf"))
            resolved_paths.extend(dir_pdfs)
        else:
            globbed = glob.glob(pattern)
            if not globbed:
                if os.path.exists(pattern):
                    resolved_paths.append(pattern)
            else:
                resolved_paths.extend(globbed)
                
    unique_paths = []
    seen = set()
    for p in resolved_paths:
        abs_p = os.path.abspath(p)
        if abs_p not in seen and os.path.isfile(abs_p) and p.lower().endswith(".pdf"):
            seen.add(abs_p)
            unique_paths.append(p)
            
    return unique_paths

def main(argv=None):
    parser = argparse.ArgumentParser(description="Extract text layout and cohesive figures from PDF files.")
    parser.add_argument("--input", "-i", nargs="+", default=["./ALPAR.pdf"], help="Path to input PDF file(s), directory, or glob pattern (default: ./ALPAR.pdf).")
    parser.add_argument("--output", "-o", default="extracted_content", help="Base directory for extracted assets (default: extracted_content).")
    parser.add_argument("--x-tolerance", "-x", type=float, default=1.5, help="Horizontal character spacing tolerance (default: 1.5).")
    parser.add_argument("--y-tolerance", "-y", type=float, default=3.0, help="Vertical line grouping tolerance (default: 3.0).")
    parser.add_argument("--gap-threshold", "-g", type=float, default=15.0, help="Horizontal gap between columns (default: 15.0).")
    parser.add_argument("--resolution", "-r", type=int, default=200, help="Resolution in DPI for cropping images (default: 200).")
    
    # Performance Options
    parser.add_argument("--workers", "-w", type=int, default=None, help="Number of parallel processes (default: min(4, CPU count)).")
    
    # Paper Layout Options
    parser.add_argument("--remove-headers-footers", action="store_true", default=True, help="Remove running headers/footers (default: True).")
    parser.add_argument("--no-remove-headers-footers", dest="remove_headers_footers", action="store_false", help="Keep running headers/footers.")
    parser.add_argument("--top-margin", type=float, default=50.0, help="Top margin height in points for header removal (default: 50.0).")
    parser.add_argument("--bottom-margin", type=float, default=50.0, help="Bottom margin height in points for footer removal (default: 50.0).")
    parser.add_argument("--keep-page1-header", action="store_true", default=True, help="Do not remove top margin text on page 1 (default: True).")
    parser.add_argument("--no-keep-page1-header", dest="keep_page1_header", action="store_false", help="Remove top margin text on page 1.")
    parser.add_argument("--exclude-figure-text", action="store_true", default=True, help="Exclude text inside cropped figures from main text (default: True).")
    parser.add_argument("--no-exclude-figure-text", dest="exclude_figure_text", action="store_false", help="Keep text inside figures in main text.")
    parser.add_argument("--dehyphenate", action="store_true", default=True, help="Join line-break hyphenated words (default: True).")
    parser.add_argument("--no-dehyphenate", dest="dehyphenate", action="store_false", help="Do not join line-break hyphenated words.")
    parser.add_argument("--reconstruct-paragraphs", action="store_true", default=True, help="Merge lines back into paragraphs (default: True).")
    parser.add_argument("--no-reconstruct-paragraphs", dest="reconstruct_paragraphs", action="store_false", help="Do not merge lines into paragraphs.")
    
    args = parser.parse_args(argv)
    
    pdf_paths = resolve_input_paths(args.input)
    if not pdf_paths:
        print(f"Error: No PDF files found matching input patterns: {args.input}", file=sys.stderr)
        return 1
        
    print(f"Found {len(pdf_paths)} PDF file(s) to process:")
    for p in pdf_paths:
        print(f"  - {p}")
        
    tasks = []
    for pdf_path in pdf_paths:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                num_pages = len(pdf.pages)
            for page_num in range(1, num_pages + 1):
                tasks.append({
                    "pdf_path": pdf_path,
                    "page_num": page_num,
                    "output_base": args.output,
                    "x_tolerance": args.x_tolerance,
                    "y_tolerance": args.y_tolerance,
                    "gap_threshold": args.gap_threshold,
                    "resolution": args.resolution,
                    "remove_headers_footers": args.remove_headers_footers,
                    "top_margin": args.top_margin,
                    "bottom_margin": args.bottom_margin,
                    "keep_page1_header": args.keep_page1_header,
                    "exclude_figure_text": args.exclude_figure_text,
                    "dehyphenate": args.dehyphenate,
                    "reconstruct_paragraphs": args.reconstruct_paragraphs
                })
        except Exception as e:
            print(f"Error opening {pdf_path}: {e}", file=sys.stderr)
            
    if not tasks:
        print("No pages to process.", file=sys.stderr)
        return 1
        
    print(f"Total pages to process: {len(tasks)}")
    
    workers = args.workers
    if workers is None:
        workers = min(4, os.cpu_count() or 1)
        
    print(f"Running with {workers} parallel worker(s)...")
    
    start_time = time.time()
    success_count = 0
    failure_count = 0
    
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(process_single_page, t) for t in tasks]
        for future in as_completed(futures):
            try:
                pdf_path, page_num, success, err = future.result()
                pdf_name = os.path.basename(pdf_path)
                if success:
                    success_count += 1
                    print(f"  [Success] Processed page {page_num} of {pdf_name}")
                else:
                    failure_count += 1
                    print(f"  [Failure] Page {page_num} of {pdf_name}: {err}", file=sys.stderr)
            except Exception as e:
                failure_count += 1
                print(f"  [Error] Worker failed: {e}", file=sys.stderr)
                
    elapsed = time.time() - start_time
    print(f"\nDone! Cohesive figures and text successfully exported to '{args.output}/'")
    print(f"Completed in {elapsed:.2f} seconds.")
    print(f"Successfully processed {success_count} / {len(tasks)} pages.")
    if failure_count > 0:
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
