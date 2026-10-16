"""템플릿을 페이지에 적용해 마킹/인적사항 판독."""
import cv2
import numpy as np
from . import detect as D
from .template import snap_zone


def _adaptive_thresh(fills):
    """분포 기반 이진화 임계값. bimodal이면 Otsu, 아니면 0.35."""
    a = np.array(fills, dtype=np.float32)
    if len(a) < 4:
        return 0.35
    lo, hi = a.min(), a.max()
    if hi - lo < 0.25:
        return 0.35
    # Otsu on 64 bins
    hist, edges = np.histogram(a, bins=64, range=(lo, hi))
    hist = hist.astype(np.float64)
    total = hist.sum()
    best_t, best_v = 0.35, -1
    s1 = c1 = 0.0
    for i in range(64):
        s1 += hist[i] * i; c1 += hist[i]
        if c1 == 0 or c1 == total:
            continue
        c2 = total - c1
        m1 = s1 / c1; m2 = (hist * np.arange(64)).sum() / total
        m2 = ((hist * np.arange(64)).sum() - s1) / c2
        v = c1 * c2 * (m1 - m2) ** 2
        if v > best_v:
            best_v, best_t = v, lo + (i + 0.5) * (hi - lo) / 64
    return float(np.clip(best_t, 0.2, 0.6))


def read_zone(img, zone, page_size, bubble_d, snapped=None):
    """한 영역의 셀별 fill ratio 행렬 반환.

    snapped: snap_zone 결과 (xs_abs, ys_abs). None이면 템플릿 분율 사용.
    """
    if snapped:
        xs_abs, ys_abs = snapped
        centers = [[(x, y) for x in xs_abs] for y in ys_abs]
    else:
        pw, ph = page_size
        centers = zone.centers(pw, ph)
    fills = []
    for row in centers:
        frow = []
        for x, y in row:
            frow.append(D.fill_ratio(img, x, y, bubble_d))
        fills.append(frow)
    return fills


def read_answers(img, zone, page_size, bubble_d, q_start=1):
    """답안 그리드 판독. {문항: {'mark': [선택지], 'blank': bool, 'multi': bool}}"""
    fills = read_zone(img, zone, page_size, bubble_d, None)
    flat = [f for row in fills for f in row]
    th = _adaptive_thresh(flat)
    out = {}
    for ri, row in enumerate(fills):
        marked = [ci + 1 for ci, f in enumerate(row) if f >= th]
        faint = [ci + 1 for ci, f in enumerate(row) if th * 0.45 <= f < th]
        q = q_start + ri
        out[q] = dict(mark=marked, blank=len(marked) == 0,
                      multi=len(marked) > 1, faint=faint, fills=row)
    out["_thresh"] = th
    return out


def read_examno(img, zone, page_size, bubble_d):
    """수험번호: digit 컬럼별 0-9 판독. '?'(복수)/'-'(미기재) 포함 문자열."""
    fills = read_zone(img, zone, page_size, bubble_d, None)
    # fills: rows = 0-9 (y 순서), cols = digit 자리
    flat = [f for row in fills for f in row]
    th = _adaptive_thresh(flat)
    n_digits = len(fills[0])
    digits = []
    errors = []
    for c in range(n_digits):
        col = [fills[r][c] for r in range(len(fills))]
        marked = [r for r, f in enumerate(col) if f >= th]
        if len(marked) == 1:
            digits.append(str(marked[0]))
        elif len(marked) == 0:
            digits.append("-")
            errors.append(f"{c + 1}번째 자리 미기재")
        else:
            digits.append("?")
            errors.append(f"{c + 1}번째 자리 복수표기{marked}")
    return "".join(digits), errors, th


def read_name_pattern(img, zone, page_size, bubble_d):
    """성명: 마크된 셀의 (slot, 행, 열) 패턴. 디코딩 없이 일치성 비교용."""
    fills = read_zone(img, zone, page_size, bubble_d, None)
    flat = [f for row in fills for f in row]
    th = _adaptive_thresh(flat)
    n_slots = zone.opt.get("n_slots", 3)
    subcols = zone.cols // n_slots
    pattern = []
    for ri, row in enumerate(fills):
        for ci, f in enumerate(row):
            if f >= th:
                slot = ci // subcols
                sub = ci % subcols
                pattern.append((slot, sub, ri))
    return sorted(pattern), th


def read_page(img, template):
    """한 페이지 전체 판독."""
    ph, pw = img.shape[:2]
    page_size = (pw, ph)
    sx = pw / template.page_size[0]
    bubble_d = template.bubble_d * sx
    # 앵커 기반 전역 오프셋
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    dx, dy = template.find_offset(gray)
    result = {"zones": {}, "page_size": page_size, "bubble_d": bubble_d,
              "offset": (dx, dy)}
    for z in template.zones:
        # 오프셋 적용된 zone (분율 좌표에 오프셋 더함)
        z2 = _shifted_zone(z, dx / template.page_size[0], dy / template.page_size[1])
        if z.kind == "answer":
            q_start = z.opt.get("q_start", 1)
            result["zones"][z.name] = dict(
                kind="answer",
                answers=read_answers(img, z2, page_size, bubble_d, q_start))
        elif z.kind == "examno":
            no, errs, th = read_examno(img, z2, page_size, bubble_d)
            result["zones"][z.name] = dict(kind="examno", exam_no=no,
                                          errors=errs, thresh=th)
        elif z.kind == "name":
            pat, th = read_name_pattern(img, z2, page_size, bubble_d)
            result["zones"][z.name] = dict(kind="name", pattern=pat,
                                          thresh=th)
    return result


def _shifted_zone(zone, dfx, dfy):
    """분율 좌표에 오프셋을 더한 복사본."""
    from .template import Zone
    return Zone(zone.kind, zone.name,
                [x + dfx for x in zone.xs],
                [y + dfy for y in zone.ys],
                **zone.opt)
