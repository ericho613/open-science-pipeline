"""Crop figures/tables from a PDF into PNG images using PyMuPDF."""
import os
import fitz  # PyMuPDF


def extract_figure_images(pdf_path: str, figures: list[dict], out_dir: str) -> list[dict]:
    """For each figure with boxes, crop the union region and save a PNG.
    Returns list of {'figure': fig, 'image_path': path}."""
    os.makedirs(out_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    results = []

    for idx, fig in enumerate(figures):
        boxes = fig.get("boxes") or []
        if not boxes:
            continue

        # Group boxes by page; take the page of the first box.
        page_no = boxes[0]["page"]
        if page_no < 0 or page_no >= doc.page_count:
            continue
        page = doc[page_no]

        # Union all boxes on that page into a bounding rectangle.
        page_boxes = [b for b in boxes if b["page"] == page_no]
        x0 = min(b["x"] for b in page_boxes)
        y0 = min(b["y"] for b in page_boxes)
        x1 = max(b["x"] + b["w"] for b in page_boxes)
        y1 = max(b["y"] + b["h"] for b in page_boxes)

        # Small padding
        pad = 5
        rect = fitz.Rect(x0 - pad, y0 - pad, x1 + pad, y1 + pad)
        rect = rect & page.rect  # clamp to page

        if rect.is_empty:
            continue

        # Render at 2x for clarity
        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat, clip=rect)
        img_path = os.path.join(out_dir, f"figure_{idx}_{fig.get('type','fig')}.png")
        pix.save(img_path)
        results.append({"figure": fig, "image_path": img_path})

    doc.close()
    return results