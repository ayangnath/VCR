# SVG parser that extracts elements and classifies them as data marks, legend
# swatches, or non-data (axes, gridlines, labels, etc).

import re
from lxml import etree
from collections import defaultdict, Counter
from color_science import parse_color, rgb_to_hex

SVG_NS = "http://www.w3.org/2000/svg"
NSMAP = {"svg": SVG_NS}

DATA_SHAPE_TAGS = {"rect", "circle", "ellipse", "path", "polygon", "polyline", "line"}

# Patterns for identifying non-data elements by class/id.
# "axes" is intentionally excluded because Matplotlib uses axes_N as a container
# for all plot elements. "axis" (singular) is fine though.
NON_DATA_PATTERNS = re.compile(
    r'((?:^|[.\s_-])axis(?:[.\s_-]|$)|grid|gridline|tick|label|title|annotation|background|border|'
    r'legend-title|x-axis|y-axis|domain|baseline|reference|tooltip|'
    r'clip-path|clippath|defs)',
    re.IGNORECASE
)

# legend-related class/id patterns
LEGEND_PATTERNS = re.compile(
    r'(legend|swatch|key|color-key|legend-item|legend-mark|legend-symbol)',
    re.IGNORECASE
)

LEGEND_SWATCH_MAX_DIM = 30  # px, small rects below this are likely swatches

# Tags where rule (i) (uniform fill/stroke/dims → non-data) is safe to apply.
# Paths/polygons are excluded because uniform color across complex shapes is
# common in real data (e.g., map regions all one color).
GRID_DETECT_TAGS = {"rect", "circle", "ellipse", "line"}

# Tags where rule (ii) (singleton element → non-data bounding box) applies.
SINGLETON_NONDATA_TAGS = {"rect"}


# A single SVG element with its color and role (data, legend, non-data).
class SVGElement:
    def __init__(self, etree_elem, fill_color, stroke_color, role, group_id=None):
        self.elem = etree_elem
        self.fill = fill_color
        self.stroke = stroke_color
        self.role = role
        self.group_id = group_id
        self.original_fill = fill_color
        self.original_stroke = stroke_color

    # The data-encoding color. Fill takes priority over stroke.
    @property
    def effective_color(self):
        return self.fill if self.fill else self.stroke

    def __repr__(self):
        tag = etree.QName(self.elem.tag).localname if '}' in self.elem.tag else self.elem.tag
        color_str = rgb_to_hex(self.fill) if self.fill else "no-fill"
        return f"<SVGElement {tag} role={self.role} color={color_str}>"


# The result of parsing an SVG: classified elements, palette, and labels.
class ParsedSVG:
    def __init__(self, tree, elements, data_colors, palette_map, labels=None,
                 ordered_legend_colors=None):
        self.tree = tree
        self.elements = elements
        self.data_elements = [e for e in elements if e.role == "data"]
        self.legend_elements = [e for e in elements if e.role == "legend"]
        self.nondata_elements = [e for e in elements if e.role == "non-data"]
        self.data_colors = data_colors
        self.palette_map = palette_map
        self.labels = labels or []
        # Legend colors in spatial order (hex), for monotonicity checks
        self.ordered_legend_colors = ordered_legend_colors or []

    # Unique data colors as a list of (R,G,B) tuples.
    @property
    def palette(self):
        return list(self.data_colors)


# Strip namespace prefix from tag name.
def _get_local_tag(elem):
    tag = elem.tag
    if isinstance(tag, str) and '}' in tag:
        return tag.split('}')[1]
    return str(tag)


# Pull a single property out of an inline style string.
def _get_style_property(elem, prop):
    style = elem.get("style", "")
    for part in style.split(";"):
        part = part.strip()
        if ":" in part:
            k, v = part.split(":", 1)
            if k.strip().lower() == prop.lower():
                return v.strip()
    return None


# Get fill color, preferring inline style over the attribute.
def _get_fill(elem):
    style_fill = _get_style_property(elem, "fill")
    if style_fill:
        return style_fill
    return elem.get("fill", None)


# Get stroke color, preferring inline style over the attribute.
def _get_stroke(elem):
    style_stroke = _get_style_property(elem, "stroke")
    if style_stroke:
        return style_stroke
    return elem.get("stroke", None)


# Set fill color, updating either the style or attribute depending on which is used.
def _set_fill(elem, hex_color):
    style = elem.get("style", "")
    if "fill:" in style or "fill :" in style:
        # replace within style string
        new_style = re.sub(
            r'fill\s*:\s*[^;]+',
            f'fill:{hex_color}',
            style
        )
        elem.set("style", new_style)
    else:
        elem.set("fill", hex_color)


# Set stroke color, same style-vs-attribute logic as _set_fill.
def _set_stroke(elem, hex_color):
    style = elem.get("style", "")
    if "stroke:" in style or "stroke :" in style:
        new_style = re.sub(
            r'stroke\s*:\s*[^;]+',
            f'stroke:{hex_color}',
            style
        )
        elem.set("style", new_style)
    else:
        elem.set("stroke", hex_color)


# Parse an attribute as a float, returning default on failure.
def _get_numeric_attr(elem, attr, default=None):
    val = elem.get(attr)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


# Get the spatial dimensions of an element for grid detection comparisons.
def _get_element_dims(elem):
    tag = _get_local_tag(elem)
    if tag == "rect":
        w = _get_numeric_attr(elem, "width")
        h = _get_numeric_attr(elem, "height")
        return (w, h)
    elif tag == "circle":
        r = _get_numeric_attr(elem, "r")
        return (r,)
    elif tag == "ellipse":
        rx = _get_numeric_attr(elem, "rx")
        ry = _get_numeric_attr(elem, "ry")
        return (rx, ry)
    elif tag == "line":
        x1 = _get_numeric_attr(elem, "x1", 0)
        y1 = _get_numeric_attr(elem, "y1", 0)
        x2 = _get_numeric_attr(elem, "x2", 0)
        y2 = _get_numeric_attr(elem, "y2", 0)
        length = round(((x2 - x1)**2 + (y2 - y1)**2)**0.5, 1)
        return (length,)
    return None


# Get the (x, y) position of an element for spatial ordering.
def _get_element_position(elem):
    tag = _get_local_tag(elem)
    if tag == "rect":
        return (_get_numeric_attr(elem, "x", 0), _get_numeric_attr(elem, "y", 0))
    elif tag in ("circle", "ellipse"):
        return (_get_numeric_attr(elem, "cx", 0), _get_numeric_attr(elem, "cy", 0))
    elif tag == "line":
        return (_get_numeric_attr(elem, "x1", 0), _get_numeric_attr(elem, "y1", 0))
    return None


# Check if two dimension tuples match within tolerance.
def _dims_match(d1, d2, tol=0.5):
    if d1 is None or d2 is None:
        return d1 is None and d2 is None
    if len(d1) != len(d2):
        return False
    return all(abs(a - b) < tol for a, b in zip(d1, d2)
               if a is not None and b is not None)


# Check whether an RGB color is achromatic (gray) within tolerance.
def _is_achromatic(rgb, max_diff=10):
    if rgb is None:
        return False
    r, g, b = rgb
    return max(abs(r - g), abs(g - b), abs(r - b)) < max_diff


# Group data elements by tag and reclassify structural/decorative elements.
# Rule (i): All elements of a tag share the same fill, stroke, and dimensions
#            → likely grid lines or uniform background tiles → non-data.
# Rule (ii): Only one element of a tag exists (for rects only)
#             → likely a bounding box → non-data.
# Rule (iii): No-fill data elements with uniform achromatic stroke are grid
#              lines. Groups by tag, so scattered data (varied strokes) is safe.
def _reclassify_grid_and_annotations(elements):
    tag_groups = defaultdict(list)
    for elem in elements:
        if elem.role == "data":
            tag = _get_local_tag(elem.elem)
            tag_groups[tag].append(elem)

    for tag, group in tag_groups.items():
        # Rule (ii): singleton rect → bounding box
        if len(group) == 1 and tag in SINGLETON_NONDATA_TAGS:
            group[0].role = "non-data"
            continue

        # Rule (i): uniform fill + stroke + dims → grid / annotation
        if tag in GRID_DETECT_TAGS and len(group) >= 2:
            ref = group[0]
            ref_dims = _get_element_dims(ref.elem)
            all_same = True

            for elem in group[1:]:
                if elem.fill != ref.fill or elem.stroke != ref.stroke:
                    all_same = False
                    break
                if not _dims_match(_get_element_dims(elem.elem), ref_dims):
                    all_same = False
                    break

            if all_same:
                for elem in group:
                    elem.role = "non-data"
                continue


def _reclassify_nofill_achromatic(elements):
    # Group ALL data elements by tag so we can compare filled vs no-fill.
    tag_data = defaultdict(list)
    for elem in elements:
        if elem.role == "data":
            tag = _get_local_tag(elem.elem)
            tag_data[tag].append(elem)

    for tag, group in tag_data.items():
        filled = [e for e in group if e.fill is not None]
        nofill_achromatic = [e for e in group
                             if e.fill is None and _is_achromatic(e.stroke)]

        if not nofill_achromatic:
            continue

        if filled:
            # Mixed group: tag has both filled data elements and no-fill
            # achromatic elements. The no-fill achromatic ones are grid/axis
            # lines (data elements of this tag type have fill).
            for e in nofill_achromatic:
                e.role = "non-data"
        else:
            # All elements of this tag are no-fill. Only reclassify if they
            # ALL share the same achromatic stroke (uniform grid/axis lines).
            # If strokes vary, they're likely data (e.g. scatterplot circles
            # with a subtle color ramp).
            if len(nofill_achromatic) == 1:
                nofill_achromatic[0].role = "non-data"
            else:
                ref_stroke = nofill_achromatic[0].stroke
                if all(e.stroke == ref_stroke for e in nofill_achromatic[1:]):
                    for e in nofill_achromatic:
                        e.role = "non-data"


# Extract width from a rect-like <path> d attribute (M...h<w>V...h<-w>z).
# Returns None for non-rectangular or complex paths.
def _get_path_rect_width(elem):
    d = elem.get("d", "")
    m = re.match(r'M[\d.\s-]+h([\d.]+)V', d)
    if m:
        return round(float(m.group(1)), 1)
    return None


# Detect annotation/background boxes: filled elements with unique dimensions
# and a perfectly achromatic fill (R==G==B) among their tag peers. These are
# background panels (e.g. matplotlib colorbar area) that should not be recolored.
def _reclassify_annotation_boxes(elements):
    tag_filled = defaultdict(list)
    for elem in elements:
        if elem.role == "data" and elem.fill is not None:
            tag = _get_local_tag(elem.elem)
            tag_filled[tag].append(elem)

    for tag, group in tag_filled.items():
        if len(group) < 3:
            continue

        # Collect effective dimensions for each element
        dims_map = defaultdict(list)
        for e in group:
            dims = _get_element_dims(e.elem)
            if dims is None and tag == "path":
                w = _get_path_rect_width(e.elem)
                if w is not None:
                    dims = (w,)
            if dims is not None:
                key = tuple(round(x, 1) if x is not None else 0 for x in dims)
                dims_map[key].append(e)

        for _dim_key, elems in dims_map.items():
            if len(elems) == 1:
                e = elems[0]
                # Strictly achromatic (R==G==B): only true grays, not
                # desaturated data colors like near-gray diverging midpoints.
                if _is_achromatic(e.fill, max_diff=1):
                    e.role = "non-data"


# Classify an element as data, legend, or non-data based on tag, class/id, and parent context.
def _classify_element(elem, parent_hints):
    tag = _get_local_tag(elem)
    elem_class = elem.get("class", "")
    elem_id = elem.get("id", "")
    combined_attrs = f"{elem_class} {elem_id}"

    # text is always non-data -- we only recolor swatches, not labels
    if tag in ("text", "tspan", "textPath"):
        return "non-data"

    # check ancestor class/id hints
    for hint in parent_hints:
        if LEGEND_PATTERNS.search(hint):
            return "legend"
        if NON_DATA_PATTERNS.search(hint):
            return "non-data"

    # direct class/id checks
    if LEGEND_PATTERNS.search(combined_attrs):
        return "legend"
    if NON_DATA_PATTERNS.search(combined_attrs):
        return "non-data"

    # gray lines are almost always gridlines or axes
    if tag == "line":
        stroke = parse_color(_get_stroke(elem))
        if stroke:
            r, g, b = stroke
            if max(abs(r - g), abs(g - b), abs(r - b)) < 30:
                return "non-data"

    # small rects near text are probably legend swatches
    if tag == "rect":
        w = _get_numeric_attr(elem, "width", 999)
        h = _get_numeric_attr(elem, "height", 999)
        if w <= LEGEND_SWATCH_MAX_DIM and h <= LEGEND_SWATCH_MAX_DIM and w > 0 and h > 0:
            # check if parent has text children nearby
            parent = elem.getparent()
            if parent is not None:
                has_text = any(_get_local_tag(sib) in ("text", "tspan") for sib in parent)
                if has_text:
                    return "legend"

    # if it's a shape and nothing else matched, it's data
    if tag in DATA_SHAPE_TAGS:
        return "data"

    return "non-data"


# Walk up the tree and collect class/id strings from ancestor elements.
def _collect_parent_hints(elem):
    hints = []
    current = elem.getparent()
    depth = 0
    while current is not None and depth < 10:
        cls = current.get("class", "")
        eid = current.get("id", "")
        if cls:
            hints.append(cls)
        if eid:
            hints.append(eid)
        current = current.getparent()
        depth += 1
    return hints


# Pull all text content from SVG text elements, deduplicated.
def _extract_labels(root):
    labels = []
    for elem in root.iter():
        tag = _get_local_tag(elem)
        if tag in ("text", "tspan", "textPath"):
            text = elem.text
            if text and text.strip():
                labels.append(text.strip())
            if elem.tail and elem.tail.strip():
                labels.append(elem.tail.strip())

    # deduplicate, keep order
    seen = set()
    unique_labels = []
    for label in labels:
        if label not in seen:
            seen.add(label)
            unique_labels.append(label)

    return unique_labels


# Get legend colors in spatial order (left-to-right or top-to-bottom).
def _get_ordered_legend_colors(legend_elements):
    positioned = []
    for elem in legend_elements:
        pos = _get_element_position(elem.elem)
        if pos and elem.effective_color:
            positioned.append((pos, elem.effective_color))

    if not positioned:
        return []

    # Determine layout direction from coordinate spread
    xs = [p[0] for p, _ in positioned]
    ys = [p[1] for p, _ in positioned]
    x_range = max(xs) - min(xs) if len(xs) > 1 else 0
    y_range = max(ys) - min(ys) if len(ys) > 1 else 0

    if x_range >= y_range:
        positioned.sort(key=lambda item: item[0][0])  # sort by x
    else:
        positioned.sort(key=lambda item: item[0][1])  # sort by y

    return [rgb_to_hex(color) for _, color in positioned]


# Parse an SVG into classified elements, build the palette map, and extract text labels.
def parse_svg(filepath):
    parser = etree.XMLParser(remove_blank_text=True, recover=True)
    tree = etree.parse(filepath, parser)
    root = tree.getroot()

    elements = []

    # walk all elements and classify them
    for elem in root.iter():
        tag = _get_local_tag(elem)

        # skip non-visual elements
        if tag in ("defs", "clipPath", "mask", "metadata", "style",
                    "linearGradient", "radialGradient", "stop",
                    "filter", "feGaussianBlur", "feOffset", "feMerge",
                    "feMergeNode", "pattern", "symbol", "use", "marker",
                    "title", "desc"):
            continue

        # only care about visible shapes and text
        if tag not in DATA_SHAPE_TAGS and tag not in ("g", "text", "tspan", "svg"):
            continue

        # skip group wrappers
        if tag in ("g", "svg"):
            continue

        fill_str = _get_fill(elem)
        stroke_str = _get_stroke(elem)
        fill_color = parse_color(fill_str)
        stroke_color = parse_color(stroke_str)

        # nothing to do if there's no color
        if fill_color is None and stroke_color is None:
            continue

        parent_hints = _collect_parent_hints(elem)
        role = _classify_element(elem, parent_hints)

        svg_elem = SVGElement(elem, fill_color, stroke_color, role)
        elements.append(svg_elem)

    # Reclassify grid lines and bounding boxes by structural patterns
    _reclassify_grid_and_annotations(elements)

    # collect unique data colors, filtering out near-white/near-black
    seen = set()
    data_colors = []
    for elem in elements:
        if elem.role == "data" and elem.effective_color:
            r, g, b = elem.effective_color
            # near-white is almost certainly background
            if min(r, g, b) > 248 and max(abs(r-g), abs(g-b), abs(r-b)) < 10:
                elem.role = "non-data"
                continue
            # near-black is usually axes or outlines
            if max(r, g, b) < 15:
                elem.role = "non-data"
                continue
            hex_key = rgb_to_hex(elem.effective_color)
            if hex_key not in seen:
                seen.add(hex_key)
                data_colors.append(elem.effective_color)

    # rebuild color map after filtering
    color_to_elements = defaultdict(list)
    for elem in elements:
        if elem.role in ("data", "legend"):
            ec = elem.effective_color
            if ec:
                hex_key = rgb_to_hex(ec)
                color_to_elements[hex_key].append(elem)

    # grab text labels for data signal analysis
    labels = _extract_labels(root)

    # get legend colors in spatial order for monotonicity checks
    legend_elems = [e for e in elements if e.role == "legend"]
    ordered_legend = _get_ordered_legend_colors(legend_elems)

    return ParsedSVG(tree, elements, data_colors, color_to_elements,
                     labels=labels, ordered_legend_colors=ordered_legend)


# Interpolate new colors for legend entries that aren't in color_mapping.
# Handles continuous legend gradients where intermediate stops don't match exactly.
def _extend_mapping_for_legend_gradients(parsed_svg, color_mapping):
    from color_science import srgb_to_lab, lab_to_srgb
    import numpy as np

    # find legend colors that have no direct mapping
    unmapped = {}
    for elem in parsed_svg.legend_elements:
        ec = elem.effective_color
        if ec:
            hex_ec = rgb_to_hex(ec)
            if hex_ec not in color_mapping:
                unmapped[hex_ec] = ec

    if not unmapped:
        return color_mapping

    # build a sorted reference by lightness from the existing mapping
    ref = []
    for old_hex, new_hex in color_mapping.items():
        old_rgb = parse_color(old_hex)
        if old_rgb:
            L = srgb_to_lab(old_rgb)[0]
            ref.append((L, old_hex, new_hex))
    ref.sort(key=lambda x: x[0])

    if not ref:
        return color_mapping

    extended = dict(color_mapping)
    ref_Ls = [r[0] for r in ref]

    for hex_c, rgb_c in unmapped.items():
        L = srgb_to_lab(rgb_c)[0]

        # find the two nearest mapped colors by lightness and interpolate
        idx = 0
        while idx < len(ref_Ls) and ref_Ls[idx] < L:
            idx += 1

        if idx == 0:
            extended[hex_c] = ref[0][2]
        elif idx >= len(ref):
            extended[hex_c] = ref[-1][2]
        else:
            lo_L, _, lo_new = ref[idx - 1]
            hi_L, _, hi_new = ref[idx]
            t = (L - lo_L) / (hi_L - lo_L + 1e-10)
            t = max(0.0, min(1.0, t))
            lo_lab = srgb_to_lab(parse_color(lo_new))
            hi_lab = srgb_to_lab(parse_color(hi_new))
            interp = np.array(lo_lab) * (1 - t) + np.array(hi_lab) * t
            extended[hex_c] = rgb_to_hex(lab_to_srgb(interp))

    return extended


# Remap gradient stop colors for legends that use a gradient colorbar.
# Uses offset-based interpolation: each stop's offset (position in the
# gradient) maps to the same relative position in the new palette. This
# avoids issues when gradient stop L* values don't match the data palette
# range or aren't monotonic.
def _recolor_svg_gradients(parsed_svg, color_mapping):
    from color_science import srgb_to_lab, lab_to_srgb
    import numpy as np

    # Collect new colors and sort by L* (dark to light)
    new_labs = []
    for new_hex in color_mapping.values():
        new_rgb = parse_color(new_hex)
        if new_rgb:
            new_labs.append(np.array(srgb_to_lab(new_rgb)))

    if not new_labs:
        return

    new_labs.sort(key=lambda lab: lab[0])
    new_labs_arr = np.array(new_labs)
    n_ref = len(new_labs_arr)

    root = parsed_svg.tree.getroot()

    for grad_elem in root.iter():
        tag = _get_local_tag(grad_elem)
        if tag not in ("linearGradient", "radialGradient"):
            continue

        # Collect stops with offsets and L*
        stops = []
        for child in grad_elem:
            if _get_local_tag(child) != "stop":
                continue

            offset_str = child.get("offset", "0").strip()
            if offset_str.endswith('%'):
                try:
                    offset = float(offset_str[:-1]) / 100.0
                except ValueError:
                    offset = 0.0
            else:
                try:
                    offset = float(offset_str)
                except ValueError:
                    offset = 0.0

            stop_color_str = _get_style_property(child, "stop-color")
            if not stop_color_str:
                stop_color_str = child.get("stop-color")
            if not stop_color_str:
                continue
            stop_rgb = parse_color(stop_color_str)
            if not stop_rgb:
                continue

            L = srgb_to_lab(stop_rgb)[0]
            stops.append((offset, L, child))

        if not stops:
            continue

        stops.sort(key=lambda x: x[0])

        # Determine direction: does L* increase or decrease along offset?
        first_L = stops[0][1]
        last_L = stops[-1][1]
        ascending = last_L >= first_L

        offset_min = stops[0][0]
        offset_max = stops[-1][0]
        offset_range = offset_max - offset_min

        for offset, _stop_L, stop_elem in stops:
            if offset_range < 0.001:
                t = 0.5
            else:
                t = (offset - offset_min) / offset_range

            # Map normalized offset to position in new palette (sorted dark→light)
            if ascending:
                pos = t * (n_ref - 1)
            else:
                pos = (1.0 - t) * (n_ref - 1)

            idx_lo = max(0, int(pos))
            idx_hi = min(idx_lo + 1, n_ref - 1)
            frac = pos - idx_lo
            frac = max(0.0, min(1.0, frac))

            new_lab = new_labs_arr[idx_lo] * (1 - frac) + new_labs_arr[idx_hi] * frac
            new_hex = rgb_to_hex(lab_to_srgb(new_lab))

            # write back
            style = stop_elem.get("style", "")
            if "stop-color" in style:
                new_style = re.sub(
                    r'stop-color\s*:\s*[^;]+',
                    f'stop-color:{new_hex}',
                    style
                )
                stop_elem.set("style", new_style)
            else:
                stop_elem.set("stop-color", new_hex)


# Apply a color mapping to data and legend elements. Non-data elements stay
# untouched. Also handles gradient stops and raster legend images.
def apply_recoloring(parsed_svg, color_mapping):
    # Late-reclassify grid/axis elements right before recoloring.
    # This runs AFTER the palette is already built, so classification,
    # invariant tests, and repair all use the original palette (unchanged).
    # The only effect is that these elements get role="non-data" and are
    # skipped by the role check below, preserving their original colors.
    _reclassify_nofill_achromatic(parsed_svg.elements)
    _reclassify_annotation_boxes(parsed_svg.elements)

    full_mapping = _extend_mapping_for_legend_gradients(parsed_svg, color_mapping)

    for hex_old, elems in parsed_svg.palette_map.items():
        if hex_old in full_mapping:
            new_hex = full_mapping[hex_old]
            for svg_elem in elems:
                # only recolor data and legend elements
                if svg_elem.role in ("data", "legend"):
                    if svg_elem.fill:
                        _set_fill(svg_elem.elem, new_hex)
                        svg_elem.fill = parse_color(new_hex)
                    if svg_elem.stroke:
                        old_stroke_hex = rgb_to_hex(svg_elem.stroke)
                        if old_stroke_hex == hex_old:
                            _set_stroke(svg_elem.elem, new_hex)
                            svg_elem.stroke = parse_color(new_hex)

    _recolor_svg_gradients(parsed_svg, color_mapping)
    _recolor_raster_legends(parsed_svg, color_mapping)


# Remap pixel colors in base64-encoded legend images using L*-interpolation.
def _recolor_raster_legends(parsed_svg, color_mapping):
    import base64
    import io
    from color_science import srgb_to_lab, lab_to_srgb
    import numpy as np

    try:
        from PIL import Image
    except ImportError:
        return

    ref = []
    for old_hex, new_hex in color_mapping.items():
        old_rgb = parse_color(old_hex)
        new_rgb = parse_color(new_hex)
        if old_rgb and new_rgb:
            L = srgb_to_lab(old_rgb)[0]
            new_lab = np.array(srgb_to_lab(new_rgb))
            ref.append((L, new_lab))
    ref.sort(key=lambda x: x[0])

    if not ref:
        return

    ref_Ls = np.array([r[0] for r in ref])
    ref_labs = np.array([r[1] for r in ref])

    XLINK = "http://www.w3.org/1999/xlink"
    root = parsed_svg.tree.getroot()

    for elem in root.iter():
        tag = _get_local_tag(elem)
        if tag != "image":
            continue

        # get base64 data from href or xlink:href
        href = elem.get("href") or elem.get(f"{{{XLINK}}}href")
        if not href or not href.startswith("data:image/"):
            continue

        # decode the image data
        try:
            header, b64_data = href.split(",", 1)
            img_bytes = base64.b64decode(b64_data)
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        except Exception:
            continue

        pixels = np.array(img)
        h, w, _ = pixels.shape

        # remap each pixel
        for y in range(h):
            for x in range(w):
                r, g, b = int(pixels[y, x, 0]), int(pixels[y, x, 1]), int(pixels[y, x, 2])
                L = srgb_to_lab((r, g, b))[0]

                # interpolate into new palette
                idx = np.searchsorted(ref_Ls, L)
                if idx == 0:
                    new_lab = ref_labs[0]
                elif idx >= len(ref_Ls):
                    new_lab = ref_labs[-1]
                else:
                    lo_L = ref_Ls[idx - 1]
                    hi_L = ref_Ls[idx]
                    t = (L - lo_L) / (hi_L - lo_L + 1e-10)
                    t = max(0.0, min(1.0, t))
                    new_lab = ref_labs[idx - 1] * (1 - t) + ref_labs[idx] * t

                nr, ng, nb = lab_to_srgb(new_lab)
                pixels[y, x] = [nr, ng, nb]

        # re-encode and update the href
        new_img = Image.fromarray(pixels.astype(np.uint8), "RGB")
        buf = io.BytesIO()
        fmt = "PNG" if "png" in header.lower() else "JPEG"
        new_img.save(buf, format=fmt)
        new_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        new_uri = f"{header},{new_b64}"

        if elem.get("href"):
            elem.set("href", new_uri)
        if elem.get(f"{{{XLINK}}}href"):
            elem.set(f"{{{XLINK}}}href", new_uri)


# Write the SVG tree to disk.
def write_svg(parsed_svg, output_path):
    parsed_svg.tree.write(output_path, xml_declaration=True,
                          encoding="utf-8", pretty_print=True)
