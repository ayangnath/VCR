# Color space conversions, CIEDE2000 distance, and CVD simulation using
# Machado et al. (2009) matrices.

import numpy as np
import re
import colorsys

# Common CSS named colors for SVG parsing
CSS_NAMED_COLORS = {
    "black": (0, 0, 0), "white": (255, 255, 255), "red": (255, 0, 0),
    "green": (0, 128, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0),
    "cyan": (0, 255, 255), "magenta": (255, 0, 255), "orange": (255, 165, 0),
    "purple": (128, 0, 128), "pink": (255, 192, 203), "brown": (165, 42, 42),
    "gray": (128, 128, 128), "grey": (128, 128, 128), "silver": (192, 192, 192),
    "navy": (0, 0, 128), "teal": (0, 128, 128), "maroon": (128, 0, 0),
    "olive": (128, 128, 0), "lime": (0, 255, 0), "aqua": (0, 255, 255),
    "fuchsia": (255, 0, 255), "coral": (255, 127, 80), "salmon": (250, 128, 114),
    "gold": (255, 215, 0), "khaki": (240, 230, 140), "plum": (221, 160, 221),
    "sienna": (160, 82, 45), "tomato": (255, 99, 71), "violet": (238, 130, 238),
    "wheat": (245, 222, 179), "ivory": (255, 255, 240), "beige": (245, 245, 220),
    "linen": (250, 240, 230), "snow": (255, 250, 250), "honeydew": (240, 255, 240),
    "mintcream": (245, 255, 250), "azure": (240, 255, 255), "lavender": (230, 230, 250),
    "mistyrose": (255, 228, 225), "ghostwhite": (248, 248, 255),
    "whitesmoke": (245, 245, 245), "gainsboro": (220, 220, 220),
    "lightgray": (211, 211, 211), "lightgrey": (211, 211, 211),
    "darkgray": (169, 169, 169), "darkgrey": (169, 169, 169),
    "dimgray": (105, 105, 105), "dimgrey": (105, 105, 105),
    "lightslategray": (119, 136, 153), "slategray": (112, 128, 144),
    "darkslategray": (47, 79, 79), "cornflowerblue": (100, 149, 237),
    "dodgerblue": (30, 144, 255), "steelblue": (70, 130, 180),
    "royalblue": (65, 105, 225), "mediumblue": (0, 0, 205),
    "darkblue": (0, 0, 139), "midnightblue": (25, 25, 112),
    "lightskyblue": (135, 206, 250), "skyblue": (135, 206, 235),
    "lightblue": (173, 216, 230), "powderblue": (176, 224, 230),
    "cadetblue": (95, 158, 160), "darkturquoise": (0, 206, 209),
    "mediumturquoise": (72, 209, 204), "turquoise": (64, 224, 208),
    "lightcyan": (224, 255, 255), "darkgreen": (0, 100, 0),
    "forestgreen": (34, 139, 34), "seagreen": (46, 139, 87),
    "mediumseagreen": (60, 179, 113), "springgreen": (0, 255, 127),
    "limegreen": (50, 205, 50), "lightgreen": (144, 238, 144),
    "palegreen": (152, 251, 152), "darkseagreen": (143, 188, 143),
    "greenyellow": (173, 255, 47), "chartreuse": (127, 255, 0),
    "lawngreen": (124, 252, 0), "yellowgreen": (154, 205, 50),
    "olivedrab": (107, 142, 35), "darkolivegreen": (85, 107, 47),
    "darkkhaki": (189, 183, 107), "lightyellow": (255, 255, 224),
    "lemonchiffon": (255, 250, 205), "lightgoldenrodyellow": (250, 250, 210),
    "papayawhip": (255, 239, 213), "moccasin": (255, 228, 181),
    "peachpuff": (255, 218, 185), "palegoldenrod": (238, 232, 170),
    "goldenrod": (218, 165, 32), "darkgoldenrod": (184, 134, 11),
    "sandybrown": (244, 164, 96), "darkorange": (255, 140, 0),
    "orangered": (255, 69, 0), "indianred": (205, 92, 92),
    "crimson": (220, 20, 60), "firebrick": (178, 34, 34),
    "darkred": (139, 0, 0), "lightcoral": (240, 128, 128),
    "rosybrown": (188, 143, 143), "lightsalmon": (255, 160, 122),
    "darksalmon": (233, 150, 122), "hotpink": (255, 105, 180),
    "deeppink": (255, 20, 147), "mediumvioletred": (199, 21, 133),
    "palevioletred": (219, 112, 147), "orchid": (218, 112, 214),
    "mediumorchid": (186, 85, 211), "darkorchid": (153, 50, 204),
    "darkviolet": (148, 0, 211), "blueviolet": (138, 43, 226),
    "mediumpurple": (147, 111, 219), "mediumslateblue": (123, 104, 238),
    "slateblue": (106, 90, 205), "darkslateblue": (72, 61, 139),
    "rebeccapurple": (102, 51, 153), "indigo": (75, 0, 130),
    "thistle": (216, 191, 216), "burlywood": (222, 184, 135),
    "tan": (210, 180, 140), "chocolate": (210, 105, 30),
    "peru": (205, 133, 63), "saddlebrown": (139, 69, 19),
    "aliceblue": (240, 248, 255), "antiquewhite": (250, 235, 215),
    "aquamarine": (127, 255, 212), "bisque": (255, 228, 196),
    "blanchedalmond": (255, 235, 205), "cornsilk": (255, 248, 220),
    "floralwhite": (255, 250, 240), "lavenderblush": (255, 240, 245),
    "navajowhite": (255, 222, 173), "oldlace": (253, 245, 230),
    "seashell": (255, 245, 238),
    "none": None, "transparent": None,
}

# Machado et al. (2009) simulation matrices for full dichromacy (severity 1.0)
MACHADO_MATRICES = {
    "protan": np.array([
        [0.152286, 1.052583, -0.204868],
        [0.114503, 0.786281,  0.099216],
        [-0.003882, -0.048116, 1.051998],
    ]),
    "deutan": np.array([
        [0.367322, 0.860646, -0.227968],
        [0.280085, 0.672501,  0.047413],
        [-0.011820, 0.042940, 0.968881],
    ]),
    "tritan": np.array([
        [1.255528, -0.076749, -0.178779],
        [-0.078411, 0.930809,  0.147602],
        [0.004733, 0.691367,  0.303900],
    ]),
}

D65_WHITE = np.array([0.95047, 1.00000, 1.08883])

# Parse a CSS color string into an (R, G, B) tuple, or None if unparseable
def parse_color(color_str):
    if color_str is None:
        return None
    color_str = color_str.strip().lower()
    if color_str in ("none", "transparent", ""):
        return None

    # hex formats
    m = re.match(r'^#([0-9a-f]{3,8})$', color_str)
    if m:
        h = m.group(1)
        if len(h) == 3:
            r, g, b = int(h[0]*2, 16), int(h[1]*2, 16), int(h[2]*2, 16)
        elif len(h) in (6, 8):
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        elif len(h) == 4:
            r, g, b = int(h[0]*2, 16), int(h[1]*2, 16), int(h[2]*2, 16)
        else:
            return None
        return (r, g, b)

    # rgb() and rgb() with percentages
    m = re.match(r'^rgb\(\s*(\d+%?)\s*,\s*(\d+%?)\s*,\s*(\d+%?)\s*\)$', color_str)
    if m:
        vals = []
        for v in m.groups():
            if v.endswith('%'):
                vals.append(int(float(v[:-1]) * 255 / 100))
            else:
                vals.append(int(v))
        return tuple(np.clip(vals, 0, 255))

    # rgba()
    m = re.match(r'^rgba\(\s*(\d+%?)\s*,\s*(\d+%?)\s*,\s*(\d+%?)\s*,\s*[\d.]+\s*\)$', color_str)
    if m:
        vals = []
        for v in m.groups():
            if v.endswith('%'):
                vals.append(int(float(v[:-1]) * 255 / 100))
            else:
                vals.append(int(v))
        return tuple(np.clip(vals, 0, 255))

    # hsl()
    m = re.match(r'^hsl\(\s*([\d.]+)\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%\s*\)$', color_str)
    if m:
        h_val = float(m.group(1)) / 360.0
        s_val = float(m.group(2)) / 100.0
        l_val = float(m.group(3)) / 100.0
        r, g, b = colorsys.hls_to_rgb(h_val, l_val, s_val)
        return (int(r * 255), int(g * 255), int(b * 255))

    # named color lookup
    if color_str in CSS_NAMED_COLORS:
        return CSS_NAMED_COLORS[color_str]

    return None

def rgb_to_hex(rgb):
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"

# sRGB [0, 255] to linear RGB [0, 1]
def srgb_to_linear(c):
    c = np.asarray(c, dtype=np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)

# Linear RGB [0, 1] to sRGB [0, 255]
def linear_to_srgb(c):
    c = np.clip(np.asarray(c, dtype=np.float64), 0, 1)
    srgb = np.where(c <= 0.0031308, 12.92 * c, 1.055 * (c ** (1.0 / 2.4)) - 0.055)
    return np.clip(np.round(srgb * 255), 0, 255).astype(int)


# sRGB to XYZ (D65) conversion matrix
_SRGB_TO_XYZ = np.array([
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041],
])

_XYZ_TO_SRGB = np.linalg.inv(_SRGB_TO_XYZ)

# Linear RGB [0,1] to XYZ
def linear_rgb_to_xyz(rgb_lin):
    return _SRGB_TO_XYZ @ np.asarray(rgb_lin, dtype=np.float64)


# XYZ to CIELAB (D65 illuminant)
def xyz_to_lab(xyz):
    xyz = np.asarray(xyz, dtype=np.float64)
    ratio = xyz / D65_WHITE
    delta = 6 / 29
    f = np.where(ratio > delta**3,
                 np.cbrt(ratio),
                 ratio / (3 * delta**2) + 4 / 29)
    L = 116 * f[1] - 16
    a = 500 * (f[0] - f[1])
    b = 200 * (f[1] - f[2])
    return np.array([L, a, b])

# CIELAB to XYZ (D65 illuminant)
def lab_to_xyz(lab):
    L, a, b = lab
    fy = (L + 16) / 116
    fx = a / 500 + fy
    fz = fy - b / 200
    delta = 6 / 29
    x = D65_WHITE[0] * (fx**3 if fx > delta else 3 * delta**2 * (fx - 4/29))
    y = D65_WHITE[1] * (fy**3 if fy > delta else 3 * delta**2 * (fy - 4/29))
    z = D65_WHITE[2] * (fz**3 if fz > delta else 3 * delta**2 * (fz - 4/29))
    return np.array([x, y, z])

# sRGB (0-255) to CIELAB
def srgb_to_lab(rgb):
    return xyz_to_lab(linear_rgb_to_xyz(srgb_to_linear(rgb)))

# CIELAB to sRGB (0-255)
def lab_to_srgb(lab):
    xyz = lab_to_xyz(lab)
    lin = _XYZ_TO_SRGB @ xyz
    return tuple(linear_to_srgb(lin))

# CIEDE2000 color difference between two CIELAB colors.
# Based on Sharma, Wu, Dalal (2005)
def ciede2000(lab1, lab2):
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2

    # calculate C' and h'
    C1 = np.sqrt(a1**2 + b1**2)
    C2 = np.sqrt(a2**2 + b2**2)
    C_avg = (C1 + C2) / 2.0
    C_avg7 = C_avg**7
    G = 0.5 * (1 - np.sqrt(C_avg7 / (C_avg7 + 25**7)))

    a1p = a1 * (1 + G)
    a2p = a2 * (1 + G)

    C1p = np.sqrt(a1p**2 + b1**2)
    C2p = np.sqrt(a2p**2 + b2**2)

    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360

    # delta values
    dLp = L2 - L1
    dCp = C2p - C1p

    if C1p * C2p == 0:
        dhp = 0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360

    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp / 2))

    # final CIEDE2000 computation
    Lp_avg = (L1 + L2) / 2
    Cp_avg = (C1p + C2p) / 2

    if C1p * C2p == 0:
        hp_avg = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hp_avg = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hp_avg = (h1p + h2p + 360) / 2
    else:
        hp_avg = (h1p + h2p - 360) / 2

    T = (1
         - 0.17 * np.cos(np.radians(hp_avg - 30))
         + 0.24 * np.cos(np.radians(2 * hp_avg))
         + 0.32 * np.cos(np.radians(3 * hp_avg + 6))
         - 0.20 * np.cos(np.radians(4 * hp_avg - 63)))

    SL = 1 + 0.015 * (Lp_avg - 50)**2 / np.sqrt(20 + (Lp_avg - 50)**2)
    SC = 1 + 0.045 * Cp_avg
    SH = 1 + 0.015 * Cp_avg * T

    Cp_avg7 = Cp_avg**7
    RT = (-np.sin(2 * np.radians(60 * np.exp(-((hp_avg - 275) / 25)**2)))
          * 2 * np.sqrt(Cp_avg7 / (Cp_avg7 + 25**7)))

    dE = np.sqrt(
        (dLp / SL)**2
        + (dCp / SC)**2
        + (dHp / SH)**2
        + RT * (dCp / SC) * (dHp / SH)
    )
    return dE

# Simulate how an sRGB color appears under a given CVD type.
# Returns simulated sRGB (0-255)
def simulate_cvd(rgb, cvd_type="deutan"):
    mat = MACHADO_MATRICES[cvd_type]
    lin = srgb_to_linear(rgb)
    sim_lin = mat @ lin
    return tuple(linear_to_srgb(sim_lin))

# Simulate CVD and return result in CIELAB
def simulate_cvd_lab(rgb, cvd_type="deutan"):
    sim_rgb = simulate_cvd(rgb, cvd_type)
    return srgb_to_lab(sim_rgb)

# Simulate CVD on a list of colors and return an NxN matrix of pairwise
# CIEDE2000 distances
def pairwise_de_under_cvd(colors, cvd_type="deutan"):
    labs = [simulate_cvd_lab(c, cvd_type) for c in colors]
    n = len(labs)
    mat = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            d = ciede2000(labs[i], labs[j])
            mat[i, j] = d
            mat[j, i] = d
    return mat

# Get L* values of colors after CVD simulation
def get_lightness_under_cvd(colors, cvd_type="deutan"):
    return [simulate_cvd_lab(c, cvd_type)[0] for c in colors]
