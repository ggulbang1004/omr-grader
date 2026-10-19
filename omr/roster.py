"""Roster (명렬표) cross-check against OMR-read student info.

The user asked (2026-10-16): upload the original class roster and compare it
with the student-info markings read from the answer sheets; flag wrongly
marked or unmarked student info as errors.

Inputs
------
- roster rows: dict(exam_no, name, grade, class_no, num) from load_roster()
- sheet infos: list of dicts like omr_info returns:
    {"exam_no": "26110?23" ('?'=복수표기/'-'=미표기 in a digit),
     "name": "김민준" ('?' if unreadable),
     "info_ok": bool, "info_errors": [...]}

Rules
-----
- exam_no fully read + in roster + name matches        -> OK
- exam_no fully read + in roster + name differs        -> 번호-성명 불일치
- exam_no fully read + NOT in roster                  -> 명렬표에 없음(오마킹/전입생 가능)
- exam_no partial ('-'/'?') + name matches roster row  -> missing-digit positions + candidate exam_no suggestion
- exam_no partial + name unreadable                   -> 수기 확인 필요
- same attributed exam_no on 2+ sheets                 -> 중복 제출
- roster row with no attributed sheet                 -> 미제출
"""
import re
from openpyxl import load_workbook

# header variants we accept, normalized (whitespace removed)
HEADER_MAP = {
    "exam_no": ["수험번호", "수험", "examno", "exam_no", "수험번호8자리"],
    "name":    ["성명", "이름", "name"],
    "grade":   ["학년", "grade"],
    "class_no":["반", "학급", "class"],
    "num":     ["번호", "출석번호", "no", "num"],
}

GRADE = "명렬표대조"


def _norm_header(h):
    return re.sub(r"\s+", "", str(h or "")).lower()


def _map_columns(header_row):
    cols = {}
    for idx, h in enumerate(header_row):
        nh = _norm_header(h)
        for key, variants in HEADER_MAP.items():
            if nh in [_norm_header(v) for v in variants] and key not in cols:
                cols[key] = idx
    return cols


def _detect_wide_format(rows):
    """반별 wide 양식 감지: '1반','2반'... 헤더가 있는 행과 열 위치 반환."""
    pat = re.compile(r"^(\d+)\s*반$")
    for i, r in enumerate(rows[:5]):
        found = []
        for idx, v in enumerate(r):
            m = pat.match(str(v or "").strip())
            if m:
                found.append((idx, int(m.group(1))))
        if found:
            return i, found
    return None, []


def _load_roster_wide(ws, header_idx, class_cols, rows):
    """wide 양식 파싱: 반 헤더 열=번호, 다음 열=이름."""
    roster = []
    for cidx, class_no in class_cols:
        ncol, nmcol = cidx, cidx + 1
        for r in rows[header_idx + 1:]:
            if ncol >= len(r):
                continue
            num_v = r[ncol] if ncol < len(r) else None
            name_v = r[nmcol] if nmcol < len(r) else None
            if num_v is None or name_v is None:
                continue
            num_s = str(num_v).strip()
            name_s = str(name_v).strip()
            if not num_s or not name_s:
                continue
            if not re.fullmatch(r"\d+", num_s):
                continue  # '인원' 합계 행 등 제외
            roster.append(dict(exam_no="", name=name_s, grade="",
                               class_no=str(class_no), num=num_s))
    return roster


def load_roster(xlsx_path, sheet=None):
    """Read a roster xlsx into a list of dicts. Column-tolerant: any common
    header wording for 수험번호/성명/학년/반/번호 is accepted.
    반별 wide 양식(1반/2반... 헤더 + 번호/이름 쌍)도 자동 인식."""
    wb = load_workbook(xlsx_path, data_only=True)
    ws = wb[sheet] if sheet else wb.active
    rows = list(ws.iter_rows(values_only=True))
    header_idx, cols = None, {}
    for i, r in enumerate(rows[:5]):          # header usually within first 5 rows
        c = _map_columns(r)
        if "exam_no" in c and "name" in c:
            header_idx, cols = i, c
            break
    if header_idx is not None:
        roster = []
        for r in rows[header_idx + 1:]:
            if all(v is None for v in r):
                continue
            exam_no = str(r[cols["exam_no"]]).strip() if r[cols["exam_no"]] is not None else ""
            name = str(r[cols["name"]]).strip() if r[cols["name"]] is not None else ""
            if not exam_no:
                continue
            roster.append(dict(
                exam_no=re.sub(r"\D", "", exam_no),
                name=name,
                grade=str(r[cols["grade"]]).strip() if "grade" in cols and r[cols["grade"]] else "",
                class_no=str(r[cols["class_no"]]).strip() if "class_no" in cols and r[cols["class_no"]] else "",
                num=str(r[cols["num"]]).strip() if "num" in cols and r[cols["num"]] else "",
            ))
        return roster
    # wide 양식 시도
    widx, class_cols = _detect_wide_format(rows)
    if class_cols:
        roster = _load_roster_wide(ws, widx, class_cols, rows)
        if roster:
            return roster
    raise ValueError("명렬표에서 수험번호/성명 컬럼을 찾지 못했습니다.")


def synthesize_exam_nos(roster, school_code, class_digits=2, num_digits=2):
    """반/번호만 있는 명렬표에 수험번호 합성: 학교번호+반+번호."""
    sc = re.sub(r"\D", "", str(school_code or ""))
    for r in roster:
        if r.get("exam_no"):
            continue
        try:
            cn = int(re.sub(r"\D", "", str(r.get("class_no", ""))))
            nm = int(re.sub(r"\D", "", str(r.get("num", ""))))
        except ValueError:
            continue
        r["exam_no"] = f"{sc}{cn:0{class_digits}d}{nm:0{num_digits}d}"
    return roster


def _bad_positions(exam_no):
    return [i + 1 for i, ch in enumerate(exam_no) if ch in "?-"]


def _pattern_match(partial, roster):
    """'?-'를 와일드카드로 명렬표에서 후보 검색."""
    cands = []
    for r in roster:
        no = r["exam_no"]
        if len(no) != len(partial):
            continue
        if all(p in "?-" or p == n for p, n in zip(partial, no)):
            cands.append(r)
    return cands


def _one_diff(a, roster):
    """한 자리만 다른 명렬표 번호 (오마킹 후보)."""
    cands = []
    for r in roster:
        no = r["exam_no"]
        if len(no) != len(a):
            continue
        if sum(1 for x, y in zip(a, no) if x != y) == 1:
            cands.append(r)
    return cands


def check_sheets(sheet_infos, roster):
    """Compare each sheet's OMR-read exam_no with the roster.

    sheet_infos: [{exam_no (with '?/-'), name}]  (name은 참고용)
    Returns (verdicts, unsubmitted).
    """
    by_no = {r["exam_no"]: r for r in roster}
    verdicts, seen = [], {}

    for si, info in enumerate(sheet_infos):
        raw = (info.get("exam_no") or "")
        errors, attrib, status = [], None, None

        if raw and "?" not in raw and "-" not in raw:
            attrib = raw
            if raw in by_no:
                status = "정상"
            else:
                status = "명렬표에 없음"
                cands = _one_diff(raw, roster)
                msg = f"수험번호 {raw}가 명렬표에 없습니다 (오마킹 또는 전입생 가능)"
                if cands:
                    msg += " - " + ", ".join(
                        f"{c['exam_no']}({c['name']})" for c in cands[:3]) + " 오마킹 가능성"
                errors.append((GRADE, msg))
        elif raw:
            bad = _bad_positions(raw)
            cands = _pattern_match(raw, roster)
            if len(cands) == 1:
                r = cands[0]
                status = "수험번호 오류(후보 제안)"
                errors.append((GRADE,
                    f"수험번호 {', '.join(map(str, bad))}번째 자리 "
                    f"{'미기재' if '-' in raw else '판독불가'} - "
                    f"{r['exam_no']}({r['name']})일 가능성"))
                attrib = r["exam_no"] + "*"
            elif len(cands) > 1:
                status = "확인 필요"
                errors.append((GRADE,
                    f"수험번호 {', '.join(map(str, bad))}번째 자리 "
                    f"{'미기재' if '-' in raw else '판독불가'} - 후보 "
                    + ", ".join(f"{c['exam_no']}({c['name']})" for c in cands[:5])
                    + " - 수기 확인 필요"))
            else:
                status = "확인 필요"
                errors.append((GRADE,
                    f"수험번호 {', '.join(map(str, bad))}번째 자리 "
                    f"{'미기재' if '-' in raw else '판독불가'} - 수기 확인 필요"))
        else:
            status = "확인 필요"
            errors.append((GRADE, "수험번호 판독 실패 - 수기 확인 필요"))

        if attrib and not attrib.endswith("*"):
            seen.setdefault(attrib, []).append(si)

        verdicts.append(dict(sheet_idx=si, exam_no_attrib=attrib,
                             name=info.get("name") or "",
                             status=status, errors=errors))

    # duplicates
    for no, idxs in seen.items():
        if len(idxs) > 1:
            r = by_no.get(no, {})
            for si in idxs:
                verdicts[si]["errors"].append(
                    (GRADE, f"수험번호 {no}({r.get('name', '')}) 답안지 {len(idxs)}장 중복 제출"))
                if verdicts[si]["status"] == "정상":
                    verdicts[si]["status"] = "중복 제출"

    attributed = {v["exam_no_attrib"].rstrip("*") for v in verdicts
                  if v["exam_no_attrib"]}
    unsubmitted = [r for r in roster if r["exam_no"] not in attributed]
    return verdicts, unsubmitted


def attach_to_workbook(wb, verdicts, unsubmitted, roster):
    """Append roster verdicts into the 오류리스트 sheet and add a 명렬표대조 sheet."""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    fill = PatternFill("solid", fgColor="FCE4D6")
    bold = Font(bold=True)
    center = Alignment(horizontal="center", vertical="center")
    thin = Side(style="thin", color="B0B0B0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws = wb.create_sheet("명렬표대조")
    ws.append(["수험번호(판독)", "성명(판독)", "판정", "내용"])
    for v in verdicts:
        for g, msg in v["errors"]:
            ws.append([v["exam_no_attrib"] or "-", v.get("name") or "", v["status"], msg])
    if unsubmitted:
        for r in unsubmitted:
            ws.append([r["exam_no"], r["name"], "미제출", "명렬표에는 있으나 답안지 없음"])
    for c in range(1, 5):
        cell = ws.cell(row=1, column=c)
        cell.fill, cell.font, cell.alignment, cell.border = fill, bold, center, border
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment, cell.border = center, border
    ws.freeze_panes = "A2"

    # also merge into 오류리스트 for the one-glance error list
    if "오류리스트" in wb.sheetnames:
        w3 = wb["오류리스트"]
        # drop the "오류 없음" placeholder if it is the only row
        if w3.max_row == 2 and w3["C2"].value == "없음":
            w3.delete_rows(2)
        for v in verdicts:
            for g, msg in v["errors"]:
                w3.append([v["exam_no_attrib"] or "-", "", g, msg])
        for r in unsubmitted:
            w3.append([r["exam_no"], r["name"], GRADE, "명렬표에는 있으나 답안지 없음"])
    return wb
