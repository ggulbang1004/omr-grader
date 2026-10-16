"""버블(마킹 원) 검출: 옅은 스캔 대응, 링 매칭 + 어두운 마크 검출."""
import cv2
import numpy as np


def darkness(img):
    """255 - gray : 어두울수록 큰 값."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return (255 - gray).astype(np.float32)


def pinkness(img):
    """R - G : 분홍색 원 테두리 강조."""
    b, g, r = cv2.split(img.astype(np.int16))
    return np.clip(r - g, 0, None).astype(np.float32)


def ring_kernel(diameter, thickness=0.18):
    """diameter 지름의 링 커널 (zero-mean)."""
    d = int(diameter)
    s = int(d * 2.2) | 1
    yy, xx = np.mgrid[0:s, 0:s]
    rr = np.sqrt((xx - s / 2) ** 2 + (yy - s / 2) ** 2)
    k = np.zeros((s, s), np.float32)
    k[np.abs(rr - d / 2) < d * thickness / 2] = 1.0
    return k - k.mean()


def _nms_peaks(resp, thresh, min_dist):
    dil = cv2.dilate(resp, np.ones((min_dist, min_dist), np.uint8))
    peaks = (resp == dil) & (resp > thresh)
    ys, xs = np.where(peaks)
    pts = list(zip(xs, ys))
    scored = sorted(zip(pts, resp[ys, xs]), key=lambda t: -t[1])
    kept = []
    r2 = (min_dist * 0.7) ** 2
    for (x, y), _ in scored:
        if all((x - kx) ** 2 + (y - ky) ** 2 > r2 for kx, ky in kept):
            kept.append((float(x), float(y)))
    return kept


def detect_rings(img, diameter, thresh=0.25, region=None):
    """링 매칭으로 버블 중심 검출 (빈 버블 + 마킹 버블 모두)."""
    x0, y0, x1, y1 = region or (0, 0, img.shape[1], img.shape[0])
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    crop = img[y0:y1, x0:x1]
    sig = darkness(crop) + pinkness(crop) * 2.0
    k = ring_kernel(diameter)
    resp = cv2.matchTemplate(sig, k, cv2.TM_CCOEFF_NORMED)
    s = k.shape[0] // 2
    pts = _nms_peaks(resp, thresh, int(diameter * 0.8))
    return [(x + x0 + s, y + y0 + s) for x, y in pts]


def detect_marks(img, region=None, dark_thresh=150, min_area_ratio=0.15,
                 bubble_d=28):
    """칠해진 마크(어두운 블롭) 검출. (x, y, area) 리스트."""
    x0, y0, x1, y1 = region or (0, 0, img.shape[1], img.shape[0])
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    crop = img[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    _, bw = cv2.threshold(gray, dark_thresh, 255, cv2.THRESH_BINARY_INV)
    n, _, stats, cent = cv2.connectedComponentsWithStats(bw, 8)
    out = []
    full = np.pi * (bubble_d / 2) ** 2
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        if a < full * min_area_ratio or a > full * 2.5:
            continue
        w = stats[i, cv2.CC_STAT_WIDTH]; h = stats[i, cv2.CC_STAT_HEIGHT]
        if w == 0 or h == 0:
            continue
        ar = w / float(h)
        if 0.55 < ar < 1.8 and w < bubble_d * 1.8:
            out.append((float(cent[i][0] + x0), float(cent[i][1] + y0), float(a)))
    return out


def cluster_1d(vals, tol):
    """1D 값을 tol 간격으로 클러스터링 -> 대표값 리스트(정렬)."""
    vals = sorted(vals)
    groups = []
    for v in vals:
        if groups and v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [float(np.median(g)) for g in groups if len(g) >= 2]


def infer_grid(points, n_cols=None, n_rows=None, tol_ratio=0.35):
    """버블 중심들에서 격자 추정.

    returns dict(cols, rows, dx, dy, x0, y0) 또는 None.
    cols/rows: 각 열/행의 대표 좌표.
    """
    if len(points) < 8:
        return None
    xs = [p[0] for p in points]; ys = [p[1] for p in points]
    # 간격 추정: 인접 차이의 최빈값
    def spacing(v):
        v = sorted(v)
        diffs = [b - a for a, b in zip(v, v[1:]) if b - a > 3]
        if not diffs:
            return None
        hist, edges = np.histogram(diffs, bins=40)
        return float(edges[np.argmax(hist)])
    dx = spacing(xs); dy = spacing(ys)
    if not dx or not dy:
        return None
    cols = cluster_1d(xs, dx * tol_ratio)
    rows = cluster_1d(ys, dy * tol_ratio)
    if n_cols and abs(len(cols) - n_cols) > 1:
        return None
    if n_rows and abs(len(rows) - n_rows) > 2:
        return None
    return dict(cols=cols, rows=rows, dx=dx, dy=dy,
                x0=cols[0], y0=rows[0])


def fill_ratio(img, cx, cy, d):
    """버블 중심(cx,cy), 지름 d 원 내부의 어두운 비율."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    r = d * 0.42
    x0, x1 = int(cx - r), int(cx + r)
    y0, y1 = int(cy - r), int(cy + r)
    h, w = gray.shape
    x0, x1 = max(0, x0), min(w, x1); y0, y1 = max(0, y0), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    patch = gray[y0:y1, x0:x1].astype(np.float32)
    yy, xx = np.mgrid[0:patch.shape[0], 0:patch.shape[1]]
    mask = (xx - (cx - x0)) ** 2 + (yy - (cy - y0)) ** 2 < r ** 2
    if mask.sum() == 0:
        return 0.0
    # 어두운 정도: (235 - gray)/235 클리핑 -> 마크/빈 분리
    dark = np.clip((235 - patch[mask]) / 235, 0, 1)
    return float(dark.mean())
