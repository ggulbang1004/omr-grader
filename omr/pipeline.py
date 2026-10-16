"""세션 파이프라인: PDF 일괄 판독 -> 명렬표 대조 -> 채점 -> 엑셀."""
import os
import cv2
import numpy as np
from . import pdfio, read as R, roster as RO, scoring as SC, key as K
from .template import Template


def read_pdf_pages(pdf_path, dpi=200, progress=None):
    """PDF -> 전처리된 페이지 리스트 [(img, rect, angle)]."""
    pages = pdfio.render_pages(pdf_path, dpi=dpi)
    out = []
    for i, img in enumerate(pages):
        img2, angle, rect = pdfio.prepare_page(img, dpi=dpi)
        out.append((img2, rect, angle))
        if progress:
            progress(i + 1, len(pages))
    return out


def read_sheet(img, template):
    """한 장 판독 -> dict(exam_no, exam_no_errors, answers, name_pattern)."""
    res = R.read_page(img, template)
    exam_no, exam_errs = "", []
    name_pattern = []
    answers = {}
    for zname, z in res["zones"].items():
        if z["kind"] == "examno":
            exam_no, exam_errs = z["exam_no"], z["errors"]
        elif z["kind"] == "name":
            name_pattern = z["pattern"]
        elif z["kind"] == "answer":
            for q, a in z["answers"].items():
                if q == "_thresh":
                    continue
                answers[q] = a
    return dict(exam_no=exam_no, exam_no_errors=exam_errs,
                answers=answers, name_pattern=name_pattern)


def run_session(session_dir, period, pdf_paths, roster_path, key_path,
                progress=None):
    """전체 실행. -> (students, verdicts, unsubmitted, wb, log)."""
    log = []
    tpl = Template.load(os.path.join(session_dir, f"template_{period}.json"))
    key, points = K.load_key(key_path)
    roster = RO.load_roster(roster_path)
    log.append(f"명렬표 {len(roster)}명, 정답 {len(key)}문항 로드")

    sheets = []  # per-page read
    all_pages = []
    for p in pdf_paths:
        all_pages.extend(read_pdf_pages(p, progress=progress))
    log.append(f"총 {len(all_pages)}페이지 판독 시작")
    for i, (img, rect, angle) in enumerate(all_pages):
        s = read_sheet(img, tpl)
        s["page_idx"] = i
        sheets.append(s)
        if progress:
            progress(i + 1, len(all_pages), phase="read")
    log.append(f"{len(sheets)}장 판독 완료")

    # 명렬표 대조
    infos = [dict(exam_no=s["exam_no"], name="?") for s in sheets]
    verdicts, unsubmitted = RO.check_sheets(infos, roster)
    by_no = {r["exam_no"]: r for r in roster}

    # 채점 + 학생 레코드
    students = []
    for s, v in zip(sheets, verdicts):
        attrib = v["exam_no_attrib"]
        name = by_no.get(attrib, {}).get("name", "") if attrib else ""
        total, per_q = SC.score_answers(s["answers"], key, points)
        errors = []
        for e in s["exam_no_errors"]:
            errors.append(("인적사항", e))
        for e in v["errors"]:
            errors.append(("명렬표대조", e[1] if len(e) > 1 else e[0]))
        for q, a in s["answers"].items():
            if a["blank"]:
                errors.append(("답안", f"{q}번 미기재"))
            elif a["multi"]:
                errors.append(("답안", f"{q}번 복수표기{a['mark']}"))
        students.append(dict(exam_no=attrib or s["exam_no"], name=name,
                             total=total, per_q=per_q, errors=errors,
                             page_idx=s["page_idx"]))
    wb = SC.build_workbook(students, key, points, period=period)
    RO.attach_to_workbook(wb, verdicts, unsubmitted, roster)
    log.append(f"채점 완료: {len(students)}명, 미제출 {len(unsubmitted)}명")
    return students, verdicts, unsubmitted, wb, log
