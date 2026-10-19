"""템플릿: 교시별 양식 정의 (그리드 + 인적사항 영역), JSON 저장/로드."""
import json
import cv2
import numpy as np
from . import detect as D


def _f(v, r):
    """절대좌표 -> 페이지 기준 분율."""
    x0, y0, x1, y1 = r
    return (v[0] - x0) / max(1, x1 - x0), (v[1] - y0) / max(1, y1 - y0)


def _a(v, r):
    """분율 -> 절대좌표."""
    x0, y0, x1, y1 = r
    return (x0 + v[0] * (x1 - x0), y0 + v[1] * (y1 - y0))


class Zone:
    """하나의 판독 영역 (답안 그리드 / 수험번호 / 성명).

    xs: 열 x좌표 리스트 (페이지 전체 기준 분율, 200dpi 기준)
    ys: 행 y좌표 리스트 (페이지 전체 기준 분율)
    """
    def __init__(self, kind, name, xs, ys, **kw):
        self.kind = kind          # 'answer' | 'examno' | 'name'
        self.name = name
        self.xs = [float(v) for v in xs]
        self.ys = [float(v) for v in ys]
        self.opt = kw                 # q_start, choices, n_slots 등

    @property
    def rows(self):
        return len(self.ys)

    @property
    def cols(self):
        return len(self.xs)

    def centers(self, page_w, page_h):
        return [[(fx * page_w, fy * page_h) for fx in self.xs]
                for fy in self.ys]

    def to_dict(self):
        return dict(kind=self.kind, name=self.name, xs=self.xs, ys=self.ys,
                    opt=self.opt)

    @classmethod
    def from_dict(cls, d):
        return cls(d["kind"], d["name"], d["xs"], d["ys"], **d.get("opt", {}))


class Template:
    def __init__(self, period="", zones=None, page_size=(2337, 1653),
                 bubble_d=26, jamo_map="kice_std", anchor=None):
        self.period = period
        self.zones = zones or []
        self.page_size = tuple(page_size)  # (w, h) at calibration DPI
        self.bubble_d = bubble_d           # px at calibration DPI
        self.jamo_map = jamo_map
        self.anchor = anchor  # dict(x, y, w, h) in page fractions, patch saved separately

    def to_dict(self):
        return dict(period=self.period, page_size=list(self.page_size),
                    bubble_d=self.bubble_d, jamo_map=self.jamo_map,
                    anchor=self.anchor,
                    zones=[z.to_dict() for z in self.zones])

    def save(self, path):
        import os
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        t = cls(d.get("period", ""),
                page_size=tuple(d.get("page_size", (2337, 1653))),
                bubble_d=d.get("bubble_d", 26),
                jamo_map=d.get("jamo_map", "kice_std"),
                anchor=d.get("anchor"))
        t.zones = [Zone.from_dict(z) for z in d.get("zones", [])]
        # anchor patch 로드
        import os
        ap = os.path.splitext(path)[0] + "_anchor.png"
        t._anchor_img = cv2.imread(ap, cv2.IMREAD_GRAYSCALE) if os.path.exists(ap) else None
        return t

    def save_anchor(self, path, img):
        """앵커 패치 저장. img는 calibration 페이지 (그레이)."""
        import os
        if not self.anchor:
            return
        pw, ph = self.page_size
        a = self.anchor
        x, y = int(a["x"] * pw), int(a["y"] * ph)
        w, h = int(a["w"] * pw), int(a["h"] * ph)
        patch = img[y:y + h, x:x + w]
        ap = os.path.splitext(path)[0] + "_anchor.png"
        cv2.imwrite(ap, patch)
        self._anchor_img = patch

    def find_offset(self, img_gray):
        """앵커 매칭으로 (dx, dy) 오프셋 반환. 실패 시 (0, 0)."""
        if not getattr(self, "_anchor_img", None) is not None:
            return (0, 0)
        if self._anchor_img is None:
            return (0, 0)
        # 예상 위치 근처에서 탐색 (전체 페이지 탐색은 오매칭 위험)
        pw, ph = self.page_size
        a = self.anchor
        cx, cy = a["x"] * pw, a["y"] * ph
        ih, iw = img_gray.shape[:2]
        sx = iw / pw
        # 탐색 범위: ±80px
        x0 = max(0, int(cx * sx - 80)); x1 = min(iw, int(cx * sx + 80 + self._anchor_img.shape[1]))
        y0 = max(0, int(cy * sx - 80)); y1 = min(ih, int(cy * sx + 80 + self._anchor_img.shape[0]))
        search = img_gray[y0:y1, x0:x1]
        if search.shape[0] < self._anchor_img.shape[0] or search.shape[1] < self._anchor_img.shape[1]:
            return (0, 0)
        res = cv2.matchTemplate(search, self._anchor_img, cv2.TM_CCOEFF_NORMED)
        _, mx, _, ml = cv2.minMaxLoc(res)
        if mx < 0.4:
            return (0, 0)
        dx = (ml[0] + x0) - cx * sx
        dy = (ml[1] + y0) - cy * sx
        return (dx / sx, dy / sx)  # template 좌표계로


def snap_zone(img, zone, page_size, margin=0.02, search_px=18):
    """템플릿 영역을 실제 페이지에 스냅: 템플릿 위치 근처에서 국소 최대값 탐색.

    zone 좌표는 페이지 전체 기준 분율. page_size=(w,h).
    각 템플릿 격자점의 ±search_px 내에서 투영 프로파일의 피크를 찾아 보정.
    returns: (xs_abs, ys_abs) 절대좌표 리스트 또는 None (실패 시).
    """
    from . import detect as D
    pw, ph = page_size
    ih, iw = img.shape[:2]
    sx, sy = iw / pw, ih / ph
    # rough rect (분율 -> 절대, margin 확장)
    fx0 = min(zone.xs) - margin; fx1 = max(zone.xs) + margin
    fy0 = min(zone.ys) - margin; fy1 = max(zone.ys) + margin
    rx0 = int(fx0 * pw * sx); rx1 = int(fx1 * pw * sx)
    ry0 = int(fy0 * ph * sy); ry1 = int(fy1 * ph * sy)
    rx0, ry0 = max(0, rx0), max(0, ry0)
    rx1, ry1 = min(iw, rx1), min(ih, ry1)
    crop = img[ry0:ry1, rx0:rx1]
    if crop.size == 0:
        return None
    sig = D.darkness(crop) + D.pinkness(crop) * 3.0
    cprof = sig.mean(axis=0)
    rprof = sig.mean(axis=1)

    def refine(positions, prof, offset):
        out = []
        for p in positions:
            rel = p - offset
            lo = max(0, int(rel - search_px))
            hi = min(len(prof), int(rel + search_px + 1))
            if hi <= lo:
                return None
            seg = prof[lo:hi]
            # 가우시안 스무딩 후 최대값
            seg_s = cv2.GaussianBlur(seg.reshape(-1, 1), (1, 7), 0).ravel()
            best = lo + int(np.argmax(seg_s))
            out.append(best + offset)
        return out

    tpl_xs = [x * pw * sx for x in zone.xs]
    tpl_ys = [y * ph * sy for y in zone.ys]
    xs = refine(tpl_xs, cprof, rx0)
    ys = refine(tpl_ys, rprof, ry0)
    if xs is None or ys is None:
        return None
    return xs, ys


def propose_zones(img, page_size, bubble_d, region=None):
    """자동 영역 제안. 휴리스틱 기반, 교사가 UI에서 확정."""
    pw, ph = page_size
    x0, y0, x1, y1 = region or (0, 0, pw, ph)
    pts = D.detect_rings(img, bubble_d, thresh=0.22,
                         region=(x0, y0, x1, y1))
    marks = D.detect_marks(img, region=(x0, y0, x1, y1),
                           bubble_d=bubble_d)
    allp = pts + [(x, y) for x, y, _ in marks]
    kept = []
    for x, y in allp:
        if all((x - kx) ** 2 + (y - ky) ** 2 > (bubble_d * 0.5) ** 2
               for kx, ky in kept):
            kept.append((x, y))
    if len(kept) < 10:
        return [], kept
    xs = sorted(p[0] for p in kept)
    diffs = [b - a for a, b in zip(xs, xs[1:]) if b - a > bubble_d * 0.4]
    if not diffs:
        return [], kept
    hist, edges = np.histogram(diffs, bins=50)
    dx = float(edges[np.argmax(hist)])
    col_centers = D.cluster_1d(xs, dx * 0.4)

    zones = []
    groups, cur = [], [col_centers[0]]
    for c in col_centers[1:]:
        if c - cur[-1] < dx * 2.2:
            cur.append(c)
        else:
            groups.append(cur); cur = [c]
    groups.append(cur)

    for gi, gcols in enumerate(groups):
        if len(gcols) < 3:
            continue
        in_g = [p for p in kept if min(abs(p[0] - c) for c in gcols) < dx * 0.4]
        ys = sorted(p[1] for p in in_g)
        ydiffs = [b - a for a, b in zip(ys, ys[1:]) if b - a > bubble_d * 0.4]
        if not ydiffs:
            continue
        hist, edges = np.histogram(ydiffs, bins=50)
        dy = float(edges[np.argmax(hist)])
        rows = D.cluster_1d(ys, dy * 0.4)
        if len(rows) < 3:
            continue
        kind = "answer"
        name = f"답안{gi + 1}"
        opt = {}
        if len(gcols) >= 8 and 8 <= len(rows) <= 12:
            kind, name = "examno", "수험번호"
        elif len(gcols) % 3 == 0 and len(rows) >= 20:
            kind, name = "name", "성명"
            opt = {"n_slots": len(gcols) // 3}
        zones.append(Zone(kind, name,
                          [c / pw for c in gcols],
                          [r / ph for r in rows], **opt))
    return zones, kept


def draw_overlay(img, page_size, zones, points=None, bubble_d=28):
    """캘리브레이션 확인용 오버레이 이미지."""
    vis = img.copy()
    pw, ph = page_size
    colors = {"answer": (0, 180, 0), "examno": (255, 0, 255), "name": (0, 140, 255)}
    for z in zones:
        col = colors.get(z.kind, (0, 255, 255))
        for r, row in enumerate(z.centers(pw, ph)):
            for c, (x, y) in enumerate(row):
                cv2.circle(vis, (int(x), int(y)), max(3, int(bubble_d * 0.4)),
                             col, 1)
        # 영역 박스
        cs = z.centers(pw, ph)
        bx0 = min(p[0] for row in cs for p in row) - bubble_d
        by0 = min(p[1] for row in cs for p in row) - bubble_d
        bx1 = max(p[0] for row in cs for p in row) + bubble_d
        by1 = max(p[1] for row in cs for p in row) + bubble_d
        cv2.rectangle(vis, (int(bx0), int(by0)), (int(bx1), int(by1)), col, 2)
        cv2.putText(vis, z.name, (int(bx0), int(by0) - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, col, 2)
    if points:
        for x, y in points:
            cv2.circle(vis, (int(x), int(y)), 3, (0, 0, 255), -1)
    return vis


def find_block_rects(img):
    """엣지 기반 블록 테두리 검출. [(x, y, w, h)] 리스트 (픽셀)."""
    import cv2
    import numpy as np
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    rects = []
    for c in contours:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4:
            x, y, ww, hh = cv2.boundingRect(approx)
            area = ww * hh
            if w * h * 0.01 < area < w * h * 0.4 and ww > 60 and hh > 60:
                rects.append((int(x), int(y), int(ww), int(hh)))
    # 중복 제거 (큰 것 우선)
    rects = sorted(rects, key=lambda r: r[2] * r[3], reverse=True)
    kept = []
    for r in rects:
        x, y, ww, hh = r
        dup = False
        for kx, ky, kww, khh in kept:
            ix0, iy0 = max(x, kx), max(y, ky)
            ix1, iy1 = min(x + ww, kx + kww), min(y + hh, ky + khh)
            if ix1 > ix0 and iy1 > iy0:
                ia = (ix1 - ix0) * (iy1 - iy0)
                if ia / (ww * hh) > 0.5 or ia / (kww * khh) > 0.5:
                    dup = True
                    break
        if not dup:
            kept.append(r)
    return sorted(kept, key=lambda r: (r[0], r[1]))
