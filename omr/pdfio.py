"""PDF -> 페이지 이미지 변환, 기울기 보정, 용지 영역 검출."""
import cv2
import numpy as np


def render_pages(pdf_path, dpi=200):
    """PDF의 모든 페이지를 BGR 이미지 리스트로 렌더링.
    pdf_path: 파일 경로 또는 PDF bytes."""
    import fitz
    if isinstance(pdf_path, (bytes, bytearray)):
        doc = fitz.open(stream=bytes(pdf_path), filetype="pdf")
    elif hasattr(pdf_path, "read"):
        doc = fitz.open(stream=pdf_path.read(), filetype="pdf")
    else:
        doc = fitz.open(pdf_path)
    pages = []
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    for page in doc:
        pix = page.get_pixmap(matrix=mat)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        elif pix.n == 1:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        pages.append(img)
    doc.close()
    return pages


def estimate_skew(img, max_angle=5.0):
    """Hough 직선 기반 기울기 추정 (도 단위). 실패 시 0.0."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blur, 50, 150)
    lines = cv2.HoughLines(edges, 1, np.pi / 180, 400)
    if lines is None:
        return 0.0
    angles = []
    for rho, theta in lines[:, 0]:
        deg = theta * 180.0 / np.pi
        # 수평선(0/180도) 또는 수직선(90도) 근처만 사용
        for base in (0.0, 90.0, 180.0):
            d = abs(deg - base)
            if d < max_angle:
                angles.append(deg - base if base != 90 else deg - 90)
                break
    if not angles:
        return 0.0
    return float(np.median(angles))


def deskew(img, max_angle=5.0):
    """기울기 보정. 보정 각도(도)와 함께 반환."""
    angle = estimate_skew(img, max_angle)
    if abs(angle) < 0.1:
        return img, 0.0
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    out = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR,
                         borderValue=(255, 255, 255))
    return out, angle


def content_rect(img, pad=10, thresh=235):
    """용지 내용이 있는 영역의 bounding box (x0, y0, x1, y1).

    스캔 페이지가 가로로 넓고 실제 답안지가 왼쪽에만 있는 경우 등을 처리.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, thresh, 255, cv2.THRESH_BINARY_INV)
    # 작은 노이즈 제거
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, k)
    ys, xs = np.where(bw > 0)
    if len(xs) == 0:
        h, w = gray.shape
        return (0, 0, w, h)
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    h, w = gray.shape
    x0 = max(0, x0 - pad); y0 = max(0, y0 - pad)
    x1 = min(w, x1 + pad); y1 = min(h, y1 + pad)
    return (x0, y0, x1, y1)


def prepare_page(img, dpi=200):
    """한 페이지를 판독용으로 전처리: deskew + content rect 반환."""
    img2, angle = deskew(img)
    rect = content_rect(img2)
    return img2, angle, rect
