"""실제 스캔(page1) 판독 테스트 - 수동 템플릿."""
import sys
sys.path.insert(0, "/home/hatch/workspace/goals/omr/app")
import cv2
from omr import pdfio
from omr.template import Template, Zone
from omr import read as R

img = cv2.imread("/tmp/real200-1.png")
img2, angle, rect = pdfio.prepare_page(img)
x0, y0, x1, y1 = rect
cw, ch = x1 - x0, y1 - y0
print("rect:", rect)

def F(xs, ys):
    return ([(x - x0) / cw for x in xs], [(y - y0) / ch for y in ys])

# 답안 1-20
xs1 = [1058, 1082, 1106, 1131, 1151]
ys1 = [424, 466, 505, 546, 587, 626, 667, 708, 748, 789,
       828, 869, 910, 950, 991, 1032, 1071, 1113, 1153, 1193]
fx1, fy1 = F(xs1, ys1)
# 답안 21-25
xs2 = [1324, 1349, 1372, 1396, 1420]
ys2 = [424, 467, 505, 546, 587]
fx2, fy2 = F(xs2, ys2)
# 수험번호 8자리 x 10행
xs3 = [293, 317, 340, 365, 413, 438, 462, 486]
ys3 = [769, 810, 848, 890, 931, 969, 1012, 1050, 1091, 1130]
fx3, fy3 = F(xs3, ys3)

tpl = Template(period="4교시-사회탐구", zones=[
    Zone("answer", "답안1", fx1, fy1, q_start=1),
    Zone("answer", "답안2", fx2, fy2, q_start=21),
    Zone("examno", "수험번호", fx3, fy3),
])
tpl.save("/tmp/tpl_test.json")

res = R.read_page(img2, rect, tpl)
for zname, z in res["zones"].items():
    if z["kind"] == "answer":
        print(f"\n== {zname} ==")
        for q in sorted(k for k in z["answers"] if k != "_thresh"):
            a = z["answers"][q]
            mark = a["mark"]
            flag = ""
            if a["blank"]:
                flag = " [미기재]"
            elif a["multi"]:
                flag = " [복수표기]"
            print(f"  {q}번: {mark}{flag}")
    elif z["kind"] == "examno":
        print(f"\n수험번호: {z['exam_no']}")
        print("오류:", z["errors"])
