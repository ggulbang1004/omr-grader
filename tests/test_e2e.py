"""실제 1.pdf end-to-end 테스트."""
import sys, os
sys.path.insert(0, "/home/hatch/workspace/goals/omr/app")
import cv2
from omr import pdfio
from omr.template import Template, Zone
from omr import read as R
from omr import roster as RO, scoring as SC, key as KEY
from openpyxl import Workbook

# 1. 템플릿 생성 (page1 기준 실측 좌표 -> 페이지 분율)
img = cv2.imread("/tmp/real200-1.png")
img2, angle, rect = pdfio.prepare_page(img)
ph, pw = img2.shape[:2]
def F(xs, ys):
    return ([x / pw for x in xs], [y / ph for y in ys])

xs1 = [1058, 1082, 1106, 1131, 1151]
ys1 = [424, 466, 505, 546, 587, 626, 667, 708, 748, 789,
       828, 869, 910, 950, 991, 1032, 1071, 1113, 1153, 1193]
xs2 = [1324, 1349, 1372, 1396, 1420]
ys2 = [424, 467, 505, 546, 587]
xs3 = [293, 317, 340, 365, 413, 438, 462, 486]
ys3 = [769, 810, 848, 890, 931, 969, 1012, 1050, 1091, 1143]
fx1, fy1 = F(xs1, ys1); fx2, fy2 = F(xs2, ys2); fx3, fy3 = F(xs3, ys3)

tpl = Template(period="4교시", page_size=(pw, ph), bubble_d=26,
             anchor=dict(x=1020/pw, y=340/ph, w=160/pw, h=60/ph),
             zones=[
    Zone("answer", "답안1", fx1, fy1, q_start=1),
    Zone("answer", "답안2", fx2, fy2, q_start=21),
    Zone("examno", "수험번호", fx3, fy3),
])
os.makedirs("/tmp/e2e", exist_ok=True)
tpl.save("/tmp/e2e/template_4교시.json")
# 앵커 패치 저장
gray = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
tpl.save_anchor("/tmp/e2e/template_4교시.json", gray)
print("template+anchor saved")

# 2. 전 페이지 판독 (1,2,3,5,6 - 유사 레이아웃)
pages = pdfio.render_pages("/home/hatch/workspace/user/files/1.pdf", dpi=200)
print(f"{len(pages)} pages")
sheets = []
for i in [0, 1, 2, 4, 5]:
    pimg, ang, r = pdfio.prepare_page(pages[i])
    # read_sheet와 동일한 로직
    from omr import read as RD
    res = RD.read_page(pimg, tpl)
    exam_no, exam_errs, answers = "", [], {}
    for zname, z in res["zones"].items():
        if z["kind"] == "examno":
            exam_no, exam_errs = z["exam_no"], z["errors"]
        elif z["kind"] == "answer":
            for q, a in z["answers"].items():
                if q != "_thresh":
                    answers[q] = a
    sheets.append(dict(page=i+1, exam_no=exam_no, exam_errs=exam_errs, answers=answers))
    n_blank = sum(1 for a in answers.values() if a["blank"])
    n_multi = sum(1 for a in answers.values() if a["multi"])
    print(f"page {i+1}: exam_no={exam_no}, blanks={n_blank}, multi={n_multi}, errs={exam_errs}")

# 3. 가상 명렬표 + 정답
wb = Workbook(); ws = wb.active
ws.append(["수험번호", "성명", "학년", "반", "번호"])
# 실제 판독된 번호 중 정상적인 것을 명렬표에 등록 (테스트용)
for s in sheets:
    clean = s["exam_no"].replace("?", "").replace("-", "")
    if len(clean) >= 6:
        ws.append([s["exam_no"], f"학생{s['page']}", 2, 1, s["page"]])
wb.save("/tmp/e2e/roster.xlsx")

wb = Workbook(); ws = wb.active
ws.append(["문항", "정답", "배점"])
for q in range(1, 26):
    ws.append([q, 3, 2])  # 전부 3번, 2점 (테스트용)
wb.save("/tmp/e2e/key.xlsx")

# 4. 파이프라인 실행
roster = RO.load_roster("/tmp/e2e/roster.xlsx")
key, points = KEY.load_key("/tmp/e2e/key.xlsx")
infos = [dict(exam_no=s["exam_no"], name="?") for s in sheets]
verdicts, unsubmitted = RO.check_sheets(infos, roster)
by_no = {r["exam_no"]: r for r in roster}
students = []
for s, v in zip(sheets, verdicts):
    attrib = v["exam_no_attrib"]
    name = by_no.get(attrib.rstrip("*") if attrib else "", {}).get("name", "") if attrib else ""
    total, per_q = SC.score_answers(s["answers"], key, points)
    errors = [("인적사항", e) for e in s["exam_errs"]]
    errors += [("명렬표대조", m) for g, m in v["errors"]]
    for q, a in s["answers"].items():
        if a["blank"]:
            errors.append(("답안", f"{q}번 미기재"))
        elif a["multi"]:
            errors.append(("답안", f"{q}번 복수표기{a['mark']}"))
    students.append(dict(exam_no=attrib or s["exam_no"], name=name,
                         total=total, per_q=per_q, errors=errors))
    print(f"page {s['page']}: {attrib} {name} {total}점, 판정={v['status']}, 오류={len(errors)}")

wb = SC.build_workbook(students, key, points, period="4교시")
RO.attach_to_workbook(wb, verdicts, unsubmitted, roster)
wb.save("/tmp/e2e/가채점_실측테스트.xlsx")
print("saved /tmp/e2e/가채점_실측테스트.xlsx")
print("ALL E2E STEPS DONE")
