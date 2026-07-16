"""Parse GROBID TEI-XML into logical sections + figure metadata."""
from lxml import etree

TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}


def _text(el) -> str:
    if el is None:
        return ""
    return " ".join(el.itertext()).strip()


def parse_tei(tei_xml: str) -> dict:
    """Return {'title', 'sections': [...]}.

    Each section is:
        {'heading': str, 'text': str, 'figures': [figure_dict, ...]}
    Figures are attached to the section (TEI <div>) that contains them.
    """
    root = etree.fromstring(tei_xml.encode("utf-8"))

    # ---- Title ----
    title_el = root.find(".//tei:titleStmt/tei:title", TEI_NS)
    title = _text(title_el) or "Untitled"

    sections: list[dict] = []

    # track figure elements already attached
    claimed_figures: set = set()

    # ---- Abstract ----
    abstract_el = root.find(".//tei:profileDesc/tei:abstract", TEI_NS)
    if abstract_el is not None:
        abstract_text = _text(abstract_el)
        abstract_figs = _parse_figures(abstract_el, claimed_figures)
        if abstract_text or abstract_figs:
            sections.append({
                "heading": "Abstract",
                "text": abstract_text,
                "figures": abstract_figs,
            })

    # ---- Body divisions (introduction, methods, results, etc.) ----
    body = root.find(".//tei:text/tei:body", TEI_NS)
    if body is not None:
        for div in body.findall("tei:div", TEI_NS):
            head_el = div.find("tei:head", TEI_NS)
            heading = _text(head_el) or "Section"
            paragraphs = [_text(p) for p in div.findall("tei:p", TEI_NS)]
            div_text = "\n".join([p for p in paragraphs if p])

            # Figures contained anywhere within this div's subtree.
            div_figs = _parse_figures(div, claimed_figures)

            if div_text or div_figs:
                sections.append({
                    "heading": heading,
                    "text": div_text,
                    "figures": div_figs,
                })

        # ---- Orphan figures: figures in <body> not inside any parsed div ----
        orphan_figs = _parse_figures(body, claimed_figures)
        if orphan_figs:
            # Attach to a dedicated section so their images aren't lost.
            sections.append({
                "heading": "Figures",
                "text": "",
                "figures": orphan_figs,
            })

    # ---- References ----
    refs = root.findall(".//tei:back//tei:listBibl/tei:biblStruct", TEI_NS)
    if refs:
        ref_texts = [_text(r) for r in refs]
        ref_block = "\n".join([r for r in ref_texts if r])
        if ref_block:
            sections.append({
                "heading": "References",
                "text": ref_block,
                "figures": [],
            })

    return {"title": title, "sections": sections}


def _parse_coords(coords_str: str) -> list[dict]:
    """GROBID coords format: 'page,x,y,w,h;page,x,y,w,h'"""
    boxes = []
    if not coords_str:
        return boxes
    for chunk in coords_str.split(";"):
        parts = chunk.split(",")
        if len(parts) == 5:
            page, x, y, w, h = parts
            boxes.append({
                "page": int(float(page)) - 1,  # 0-indexed for PyMuPDF
                "x": float(x),
                "y": float(y),
                "w": float(w),
                "h": float(h),
            })
    return boxes


def _parse_figures(scope_el, claimed_figures: set) -> list[dict]:
    """Parse all <figure> elements within scope_el's subtree.

    Figures already attached to a previous section (tracked in
    `claimed_figures`) are skipped so no figure is processed twice.
    """
    figures = []
    for fig in scope_el.findall(".//tei:figure", TEI_NS):
        # Skip figures already claimed by an enclosing/earlier section.
        if fig in claimed_figures:
            continue
        claimed_figures.add(fig)

        fig_type = fig.get("type", "figure")
        label_el = fig.find("tei:head", TEI_NS)
        label = _text(label_el)
        desc_el = fig.find("tei:figDesc", TEI_NS)
        desc = _text(desc_el)

        coords_attr = fig.get("coords")
        boxes = _parse_coords(coords_attr)

        fig_id = fig.get("{http://www.w3.org/XML/1998/namespace}id", "")

        figures.append({
            "id": fig_id,
            # 'figure' or 'table'
            "type": fig_type,
            "label": label,
            "description": desc,
            "boxes": boxes,
            "text": f"{label}\n{desc}".strip(),
        })
    return figures