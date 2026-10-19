"""OMR 가채점 프로그램 - Streamlit UI."""
import os
import io
import json
import cv2
import numpy as np
import streamlit as st

import sys
sys.path.insert(0, os.path.dirname(__file__))
from omr import pdfio
from omr.template import Template, Zone, propose_zones, draw_overlay
from omr import pipeline as PL
from omr import roster as RO
from omr import key as KEY

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SESS_DIR = os.path.join(APP_DIR, "sessions")
os.makedirs(SESS_DIR, exist_ok=True)

st.set_page_config(page_title="OMR 가채점", layout="wide")


# ---------- helpers ----------
def sess_path(name):
    p = os.path.join(SESS_DIR, name)
    os.makedirs(p, exist_ok=True)
    return p


def list_sessions():
    return sorted([d for d in os.listdir(SESS_DIR)
                   if os.path.isdir(os.path.join(SESS_DIR, d))])


def export_session(name):
    """세션 폴더를 zip 바이트로 내보내기."""
    import zipfile
    import tempfile
    sp = os.path.join(SESS_DIR, name)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("_session_name.txt", name)
        for root, dirs, files in os.walk(sp):
            for f in files:
                fp = os.path.join(root, f)
                arc = os.path.relpath(fp, sp)
                z.write(fp, arc)
    buf.seek(0)
    return buf.getvalue()


def import_session(uploaded):
    """업로드된 zip을 세션으로 가져오기. 세션명을 반환."""
    import zipfile
    import re
    z = zipfile.ZipFile(io.BytesIO(uploaded.read()))
    names = z.namelist()
    # zip 안에 저장된 세션명 우선 사용
    if "_session_name.txt" in names:
        base = z.read("_session_name.txt").decode("utf-8").strip()
    else:
        base = names[0].split("/")[0] if names else "imported"
    base = re.sub(r'[\\/:*?"<>|]', "_", base).strip() or "imported"
    name = base
    i = 2
    while os.path.exists(os.path.join(SESS_DIR, name)):
        name = f"{base}_{i}"
        i += 1
    sp = os.path.join(SESS_DIR, name)
    os.makedirs(sp, exist_ok=True)
    for n in names:
        if n == "_session_name.txt":
            continue
        z.extract(sp, n)
    return name


def img_to_bytes(img):
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes()


def load_image(uploaded):
    data = np.frombuffer(uploaded.read(), np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


# ---------- sidebar ----------
st.sidebar.title("OMR 가채점")
steps = ["📖 사용 설명서", "1. 세션", "2. 양식 캘리브레이션", "3. 스캔 PDF 판독",
         "4. 명렬표", "5. 정답·배점", "6. 결과"]
step = st.sidebar.radio("단계", steps)

sessions = list_sessions()
if "session" not in st.session_state:
    st.session_state.session = sessions[0] if sessions else None

# ---------- manual ----------
if step.startswith("📖"):
    st.header("📖 사용 설명서")
    st.markdown("""
## 전체 흐름

스캔한 답안지 PDF를 올리면 마킹을 자동으로 읽고, 명렬표와 대조한 뒤,
정답·배점으로 채점해서 엑셀 리포트로 만들어줍니다.

**준비물 3가지**
- 스캔 PDF: 복합기로 스캔한 답안지 (교시별)
- 명렬표 엑셀: 수험번호·성명 컬럼이 있는 원본 명렬표
- 정답 엑셀: 문항·정답·배점 컬럼이 있는 정답표

## 1단계: 세션 만들기

모의고사마다 세션을 하나 만드세요. (예: 2026-09-고2-모평)
이후 모든 작업(템플릿·판독 결과·명렬표·정답)은 이 세션 안에 저장됩니다.

## 2단계: 양식 캘리브레이션 (교시별 1회)

답안지 양식은 모의고사마다 조금씩 바뀌므로, 교시마다 한 번씩 해주세요.
1. 교시명 입력 (예: 1교시, 2교시)
2. 해당 교시의 빈 답안지 또는 기준 답안지 1장을 업로드
3. 자동으로 답안 영역·수험번호 영역을 찾습니다
4. 영역이 맞는지 확인하고, 어긋나면 수정 후 템플릿 저장

## 3단계: 스캔 PDF 판독

1. **명렬표를 먼저 올려두세요** (3단계 화면 상단에서 바로 올릴 수 있음)
2. 교시별로 스캔 PDF를 올리세요 (한 번에 여러 교시 가능)
3. 판독 시작을 누르면 각 답안지의 마킹을 읽고, **곧바로 명렬표와 대조**합니다
4. 수험번호 오류·미기재·미제출이 바로 표시되니 확인 후 오류 답안지는 다시 스캔하세요

## 4단계: 명렬표 업로드

원본 명렬표 엑셀을 한 번만 올리세요. (4개 교시가 공유합니다)
- 필요한 컬럼: 수험번호, 성명 (학년/반/번호가 있으면 함께 인식)
- 컬럼명이 달라도 자동으로 찾아줍니다

대조 결과: 정상 / 번호-성명 불일치 / 명렬표에 없음 / 수험번호 오류(후보 번호 제안) / 중복 제출 / 확인 필요 / 미제출

## 5단계: 정답·배점 업로드

교시별로 정답 엑셀을 올리세요. (문항 / 정답 / 배점 컬럼)

## 6단계: 가채점 결과

- 교시별 실행: 한 교시씩 실행하고 엑셀 다운로드
- 1~4교시 전체 실행: 4개 교시를 한 번에 실행 (교시별 엑셀 + 종합표 생성)

엑셀 리포트: 개인별점수 / 문항분석 / 오류리스트 / 정답및배점 / 명렬표대조

## 2일에 나눠서 작업하기

**첫째 날 (스캔·판독·오류 수정)**
1. 1~2단계 진행 (세션 → 캘리브레이션)
2. 3단계에서 명렬표를 먼저 올린 뒤 스캔 PDF 판독 → 오류가 바로 표시됨
3. 오류리스트에서 인적사항 마킹 오류 확인·수정
4. 오류 난 답안지는 다시 스캔해서 판독
5. 1단계에서 **세션 저장** 버튼으로 파일 다운로드

**둘째 날 (채점)**
1. 1단계에서 저장된 세션 파일 불러오기
2. 5단계에서 정답·배점 엑셀 업로드
3. 6단계에서 가채점 실행 → 엑셀 다운로드

> 웹 버전은 서버가 쉬면 저장 내용이 지워질 수 있으니,
> 첫째 날 작업이 끝나면 반드시 세션 저장 파일을 받아두세요.

## 자주 묻는 질문

**Q. 답안이 잘못 읽혔어요.**
A. 2단계에서 템플릿 영역이 정확한지 확인하세요. 스캔이 기울어졌거나 흐리면 다시 스캔하세요.

**Q. 수험번호 마킹 오류가 떴어요.**
A. 오류리스트에서 확인 후 수기로 정정하세요. 가장 가까운 후보 번호를 제안해줍니다.

**Q. 다음 모의고사에도 쓸 수 있나요?**
A. 1단계에서 새 세션을 만들고, 양식이 바뀌었으면 2단계에서 교시별 템플릿만 다시 만들면 됩니다.
""")

# ---------- step 1: session ----------
if step.startswith("1."):
    st.header("1. 모의고사 세션")
    new_name = st.text_input("새 세션 이름 (예: 2026-09-고2-모평)")
    if st.button("세션 만들기") and new_name.strip():
        sess_path(new_name.strip())
        st.session_state.session = new_name.strip()
        st.success(f"세션 '{new_name.strip()}' 생성")
    sel = st.selectbox("세션 선택", sessions,
                       index=sessions.index(st.session_state.session)
                       if st.session_state.session in sessions else 0)
    st.session_state.session = sel
    if sel:
        sp = sess_path(sel)
        st.write("저장된 템플릿:", [f for f in os.listdir(sp)
                                    if f.startswith("template_")])
        st.divider()
        st.subheader("중간 저장 / 불러오기")
        st.caption("첫째 날 판독까지 끝낸 뒤 저장해두면, 둘째 날 이어서 작업할 수 있습니다.")
        c1, c2 = st.columns(2)
        with c1:
            data = export_session(sel)
            st.download_button("💾 세션 저장 (파일 다운로드)",
                               data, file_name=f"세션_{sel}.zip",
                               mime="application/zip")
        with c2:
            up = st.file_uploader("저장된 세션 파일 불러오기", type=["zip"],
                                  key="sess_import")
            if up:
                name = import_session(up)
                st.session_state.session = name
                st.success(f"세션 '{name}' 불러옴")
                st.rerun()

# ---------- step 2: calibration ----------
elif step.startswith("2."):
    st.header("2. 양식 캘리브레이션 (교시별 1회)")
    if not st.session_state.session:
        st.warning("먼저 세션을 만드세요.")
        st.stop()
    sp = sess_path(st.session_state.session)
    period = st.text_input("교시명", value="4교시")
    ref = st.file_uploader("기준 답안지 이미지/PDF 1페이지", type=["png", "jpg", "pdf"])
    if ref:
        if ref.name.lower().endswith(".pdf"):
            pages = pdfio.render_pages(ref, dpi=200)
            img = pages[0]
        else:
            img = load_image(ref)
        img2, angle, rect = pdfio.prepare_page(img)
        ph, pw = img2.shape[:2]
        page_size = (pw, ph)
        st.info(f"기울기 보정: {angle:.2f}도 / 페이지: {pw}x{ph}")
        bubble_d = 26
        if st.button("자동 영역 찾기"):
            zones, pts = propose_zones(img2, page_size, bubble_d,
                                       region=(0, 0, int(pw * 0.7), ph))
            st.session_state.zones = [z.to_dict() for z in zones]
            cv2.imwrite(os.path.join(sp, f"ref_{period}.png"), img2)
            st.success(f"{len(zones)}개 영역 후보 검출")
        if "zones" in st.session_state:
            zones = [Zone.from_dict(z) for z in st.session_state.zones]
            st.subheader("검출된 영역 (종류/이름 수정, 삭제 가능)")
            keep = []
            for i, z in enumerate(zones):
                c1, c2, c3, c4 = st.columns([2, 3, 2, 1])
                z.kind = c1.selectbox(f"종류{i}", ["answer", "examno", "name"],
                                      index=["answer", "examno", "name"].index(z.kind),
                                      key=f"kind{i}")
                z.name = c2.text_input(f"이름{i}", z.name, key=f"nm{i}")
                if z.kind == "answer":
                    z.opt["q_start"] = c3.number_input(f"시작문항{i}", 1, 200,
                                                       z.opt.get("q_start", 1), key=f"qs{i}")
                if not c4.checkbox("삭제", key=f"del{i}"):
                    keep.append(z)
            st.session_state.zones = [z.to_dict() for z in keep]
            ov = draw_overlay(img2, page_size, keep, bubble_d=bubble_d)
            st.image(cv2.cvtColor(cv2.resize(ov, (900, int(900 * ov.shape[0] / ov.shape[1]))),
                                  cv2.COLOR_BGR2RGB), caption="영역 오버레이 (초록=답안, 분홍=수험번호, 주황=성명)")
            # 앵커: 답안1 영역의 상단 헤더를 자동 지정
            if st.button("템플릿 저장"):
                tpl = Template(period=period, zones=keep, page_size=page_size,
                               bubble_d=bubble_d)
                # 앵커: 첫 번째 answer zone의 좌상단 패치
                ans_zones = [z for z in keep if z.kind == "answer"]
                if ans_zones:
                    z0 = ans_zones[0]
                    ax = min(z0.xs) - 0.02; ay = min(z0.ys) - 0.06
                    tpl.anchor = dict(x=max(0, ax), y=max(0, ay), w=0.07, h=0.036)
                tp = os.path.join(sp, f"template_{period}.json")
                tpl.save(tp)
                gray = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
                tpl.save_anchor(tp, gray)
                st.success(f"템플릿 저장됨: template_{period}.json")

# ---------- step 3: PDF read ----------
elif step.startswith("3."):
    st.header("3. 스캔 PDF 업로드 및 판독")
    if not st.session_state.session:
        st.warning("먼저 세션을 만드세요.")
        st.stop()
    sp = sess_path(st.session_state.session)
    tpls = [f for f in os.listdir(sp) if f.startswith("template_") and f.endswith(".json")]
    if not tpls:
        st.warning("2단계에서 템플릿을 먼저 만드세요.")
        st.stop()
    periods = [t[9:-5] for t in tpls]
    # 명렬표: 판독 직후 대조할 수 있도록 여기서도 업로드 가능
    roster_x = os.path.join(sp, "roster.xlsx")
    with st.expander("명렬표 (판독 후 오류 대조용 — 먼저 올려두세요)",
                     expanded=not os.path.exists(roster_x)):
        up_r = st.file_uploader("명렬표 엑셀", type=["xlsx"], key="roster_in_3")
        if up_r:
            with open(roster_x, "wb") as f:
                f.write(up_r.read())
            st.success("명렬표 저장됨")
            st.rerun()
        if os.path.exists(roster_x):
            try:
                st.caption(f"{len(RO.load_roster(roster_x))}명 로드됨")
            except Exception as e:
                st.error(str(e))
    st.info("교시별로 스캔 PDF를 올리세요. 한 번에 여러 교시 올려도 됩니다.")
    for period in periods:
        with st.expander(f"{period} PDF", expanded=False):
            pdfs = st.file_uploader(f"{period} 스캔 PDF", type=["pdf"],
                                    accept_multiple_files=True, key=f"pdf_{period}")
            if pdfs and st.button(f"{period} 판독 시작", key=f"read_{period}"):
                tpl = Template.load(os.path.join(sp, f"template_{period}.json"))
                tmp_paths = []
                for p in pdfs:
                    tp = os.path.join(sp, f"upload_{period}_{p.name}")
                    with open(tp, "wb") as f:
                        f.write(p.read())
                    tmp_paths.append(tp)
                prog = st.progress(0)
                total_pages = sum(len(pdfio.render_pages(tp, dpi=120)) for tp in tmp_paths)
                done, results = 0, []
                for tp in tmp_paths:
                    for img, rect, angle in PL.read_pdf_pages(tp, dpi=200):
                        s = PL.read_sheet(img, tpl)
                        s["src"] = os.path.basename(tp)
                        results.append(s)
                        done += 1
                        prog.progress(done / max(1, total_pages))
                import pickle
                with open(os.path.join(sp, f"read_{period}.pkl"), "wb") as f:
                    pickle.dump(results, f)
                st.success(f"{period}: {len(results)}장 판독 완료")
                # 판독 직후 명렬표 대조
                if os.path.exists(roster_x):
                    try:
                        roster = RO.load_roster(roster_x)
                        infos = [dict(exam_no=s["exam_no"], name="?") for s in results]
                        verdicts, unsubmitted = RO.check_sheets(infos, roster)
                        rows = []
                        for s, v in zip(results, verdicts):
                            errs = [m for _, m in v["errors"]]
                            errs += s.get("exam_no_errors", [])
                            rows.append({"수험번호": v["exam_no_attrib"] or s["exam_no"],
                                         "판정": v["verdict"],
                                         "오류": "; ".join(errs)})
                        st.subheader(f"{period} 명렬표 대조 결과")
                        st.dataframe(rows)
                        if unsubmitted:
                            st.warning("미제출: " + ", ".join(
                                f"{r['exam_no']}({r['name']})" for r in unsubmitted))
                    except Exception as e:
                        st.error(f"대조 중 오류: {e}")
                else:
                    st.warning("명렬표를 올리면 판독 직후 오류를 바로 확인할 수 있습니다.")

# ---------- step 4: roster ----------
elif step.startswith("4."):
    st.header("4. 명렬표 업로드")
    if not st.session_state.session:
        st.warning("먼저 세션을 만드세요.")
        st.stop()
    sp = sess_path(st.session_state.session)
    up = st.file_uploader("명렬표 엑셀", type=["xlsx"])
    if up:
        tp = os.path.join(sp, "roster.xlsx")
        with open(tp, "wb") as f:
            f.write(up.read())
        try:
            roster = RO.load_roster(tp)
            st.success(f"{len(roster)}명 로드됨")
            st.dataframe([{"수험번호": r["exam_no"], "성명": r["name"],
                           "학년": r["grade"], "반": r["class_no"],
                           "번호": r["num"]} for r in roster[:20]])
        except Exception as e:
            st.error(str(e))

# ---------- step 5: key ----------
elif step.startswith("5."):
    st.header("5. 정답·배점 업로드")
    if not st.session_state.session:
        st.warning("먼저 세션을 만드세요.")
        st.stop()
    sp = sess_path(st.session_state.session)
    tpls = [f for f in os.listdir(sp) if f.startswith("template_") and f.endswith(".json")]
    periods = [t[9:-5] for t in tpls] if tpls else []
    st.info("교시별 정답 엑셀을 올리세요. (문항/정답/배점 컬럼)")
    for period in periods:
        with st.expander(f"{period} 정답", expanded=False):
            up = st.file_uploader(f"{period} 정답·배점 엑셀", type=["xlsx"],
                                  key=f"key_{period}")
            if up:
                tp = os.path.join(sp, f"key_{period}.xlsx")
                with open(tp, "wb") as f:
                    f.write(up.read())
                try:
                    key, points = KEY.load_key(tp)
                    st.success(f"{period}: {len(key)}문항 로드됨")
                except Exception as e:
                    st.error(str(e))

# ---------- step 6: results ----------
elif step.startswith("6."):
    st.header("6. 가채점 결과")
    if not st.session_state.session:
        st.warning("먼저 세션을 만드세요.")
        st.stop()
    sp = sess_path(st.session_state.session)
    tpls = [f for f in os.listdir(sp) if f.startswith("template_") and f.endswith(".json")]
    if not tpls:
        st.warning("템플릿이 없습니다.")
        st.stop()
    periods = [t[9:-5] for t in tpls]
    roster_x = os.path.join(sp, "roster.xlsx")
    if not os.path.exists(roster_x):
        st.warning("4단계에서 명렬표를 업로드하세요.")
        st.stop()

    # 준비 상태 표시
    import pickle
    from omr import scoring as SC
    ready = []
    for period in periods:
        rp = os.path.join(sp, f"read_{period}.pkl")
        kp = os.path.join(sp, f"key_{period}.xlsx")
        ready.append({"교시": period,
                      "판독": "O" if os.path.exists(rp) else "X",
                      "정답": "O" if os.path.exists(kp) else "X"})
    st.dataframe(ready)

    def run_period(period):
        """한 교시 실행 -> (students, verdicts, unsubmitted, xlsx_path)."""
        with open(os.path.join(sp, f"read_{period}.pkl"), "rb") as f:
            sheets = pickle.load(f)
        key, points = KEY.load_key(os.path.join(sp, f"key_{period}.xlsx"))
        roster = RO.load_roster(roster_x)
        infos = [dict(exam_no=s["exam_no"], name="?") for s in sheets]
        verdicts, unsubmitted = RO.check_sheets(infos, roster)
        by_no = {r["exam_no"]: r for r in roster}
        students = []
        for s, v in zip(sheets, verdicts):
            attrib = v["exam_no_attrib"]
            name = by_no.get(attrib.rstrip("*") if attrib else "",
                             {}).get("name", "") if attrib else ""
            total, per_q = SC.score_answers(s["answers"], key, points)
            errors = [("인적사항", e) for e in s["exam_no_errors"]]
            errors += [("명렬표대조", m) for g, m in v["errors"]]
            for q, a in s["answers"].items():
                if q == "_thresh":
                    continue
                if a["blank"]:
                    errors.append(("답안", f"{q}번 미기재"))
                elif a["multi"]:
                    errors.append(("답안", f"{q}번 복수표기{a['mark']}"))
            students.append(dict(exam_no=attrib or s["exam_no"], name=name,
                                 total=total, per_q=per_q, errors=errors))
        wb = SC.build_workbook(students, key, points, period=period)
        RO.attach_to_workbook(wb, verdicts, unsubmitted, roster)
        out = os.path.join(sp, f"가채점_{period}_{st.session_state.session}.xlsx")
        wb.save(out)
        return students, verdicts, unsubmitted, out

    c1, c2 = st.columns(2)
    with c1:
        period = st.selectbox("교시별 실행", periods, key="res_period")
        if st.button(f"{period} 가채점 실행"):
            students, verdicts, unsubmitted, out = run_period(period)
            st.session_state.report = out
            st.session_state.students_ui = students
            st.success(f"{period} 완료")
    with c2:
        st.write("전체 교시 일괄 실행")
        if st.button("1~4교시 전체 실행"):
            all_results = {}
            prog = st.progress(0)
            for i, period in enumerate(periods):
                rp = os.path.join(sp, f"read_{period}.pkl")
                kp = os.path.join(sp, f"key_{period}.xlsx")
                if not (os.path.exists(rp) and os.path.exists(kp)):
                    continue
                all_results[period] = run_period(period)
                prog.progress((i + 1) / len(periods))
            st.session_state.batch_results = all_results
            # 종합표: 학생 x 교시 점수
            roster = RO.load_roster(roster_x)
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "종합"
            ws.append(["수험번호", "성명"] + periods)
            by_no = {r["exam_no"]: r["name"] for r in roster}
            for r in roster:
                row = [r["exam_no"], r["name"]]
                for period in periods:
                    sc = ""
                    if period in all_results:
                        for s in all_results[period][0]:
                            if s["exam_no"].rstrip("*") == r["exam_no"]:
                                sc = s["total"]
                                break
                    row.append(sc)
                ws.append(row)
            out = os.path.join(sp, f"가채점_종합_{st.session_state.session}.xlsx")
            wb.save(out)
            st.session_state.batch_summary = out
            st.success(f"{len(all_results)}개 교시 완료")

    if "students_ui" in st.session_state:
        st.subheader("개인별 점수")
        st.dataframe([{"수험번호": s["exam_no"], "성명": s["name"],
                       "점수": s["total"], "오류": len(s["errors"])}
                      for s in st.session_state.students_ui])
        with open(st.session_state.report, "rb") as f:
            st.download_button("엑셀 다운로드", f,
                               file_name=os.path.basename(st.session_state.report))
    if "batch_results" in st.session_state:
        st.subheader("교시별 완료")
        for period, (students, v, u, out) in st.session_state.batch_results.items():
            with open(out, "rb") as f:
                st.download_button(f"{period} 엑셀", f,
                                   file_name=os.path.basename(out),
                                   key=f"dl_{period}")
        with open(st.session_state.batch_summary, "rb") as f:
            st.download_button("종합표 엑셀", f,
                               file_name=os.path.basename(st.session_state.batch_summary))
