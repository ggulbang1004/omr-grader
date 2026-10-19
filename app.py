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


def load_roster_full(sp):
    """세션 명렬표 로드 + 수험번호 구조 설정 적용 (반/번호 → 수험번호 합성)."""
    import json
    cfg = {"school_code": "21911", "class_digits": 2, "num_digits": 2}
    cp = os.path.join(sp, "exam_struct.json")
    if os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            cfg.update(json.load(f))
    roster = RO.load_roster(os.path.join(sp, "roster.xlsx"))
    RO.synthesize_exam_nos(roster, cfg["school_code"],
                           int(cfg["class_digits"]), int(cfg["num_digits"]))
    return roster, cfg


def save_exam_struct(sp, school_code, class_digits, num_digits):
    import json
    with open(os.path.join(sp, "exam_struct.json"), "w", encoding="utf-8") as f:
        json.dump({"school_code": school_code, "class_digits": int(class_digits),
                   "num_digits": int(num_digits)}, f, ensure_ascii=False)


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
steps = ["📖 사용 설명서", "1. 세션", "2. 양식 캘리브레이션", "3. 명렬표",
         "4. 스캔 PDF 판독", "5. 정답·배점", "6. 결과"]
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

## 3단계: 명렬표 업로드

원본 명렬표 엑셀을 한 번만 올리세요. (4개 교시가 공유합니다)
- 필요한 컬럼: 수험번호, 성명 (학년/반/번호가 있으면 함께 인식)
- 컬럼명이 달라도 자동으로 찾아줍니다

대조 결과: 정상 / 번호-성명 불일치 / 명렬표에 없음 / 수험번호 오류(후보 번호 제안) / 중복 제출 / 확인 필요 / 미제출

## 4단계: 스캔 PDF 판독

1. 교시별로 스캔 PDF를 올리세요 (한 번에 여러 교시 가능)
2. **스캔 방향** 확인: 가로 답안지가 90도 돌아가서 스캔됐다면 90°를 선택하세요.
   미리보기에서 똑바로 보이는지 확인할 수 있습니다. (자동이 기본값)
3. 판독 시작을 누르면 각 답안지의 마킹을 읽고, 곧바로 명렬표와 대조합니다
4. 수험번호 오류·미기재·미제출이 바로 표시되니 확인 후 오류 답안지는 다시 스캔하세요

## 5단계: 정답·배점 업로드

교시별로 정답 엑셀을 올리세요. (문항 / 정답 / 배점 컬럼)

## 6단계: 가채점 결과

- 교시별 실행: 한 교시씩 실행하고 엑셀 다운로드
- 1~4교시 전체 실행: 4개 교시를 한 번에 실행 (교시별 엑셀 + 종합표 생성)

엑셀 리포트: 개인별점수 / 문항분석 / 오류리스트 / 정답및배점 / 명렬표대조

## 2일에 나눠서 작업하기

**첫째 날 (스캔·판독·오류 수정)**
1. 1~3단계 진행 (세션 → 캘리브레이션 → 명렬표)
2. 4단계에서 스캔 PDF 판독 → 오류가 바로 표시됨
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

**Q. 스캔할 때 뒷면(빈 종이)도 같이 들어갔어요.**
A. 판독 후 "빈 페이지로 의심" 경고가 뜨면 확인하고 삭제 버튼을 누르세요.
수험번호도 답안도 하나도 마킹 안 된 장을 자동으로 찾아줍니다.

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

# ---------- step 3: roster ----------
elif step.startswith("3."):
    st.header("3. 명렬표 업로드")
    if not st.session_state.session:
        st.warning("먼저 세션을 만드세요.")
        st.stop()
    sp = sess_path(st.session_state.session)
    st.info("판독 전에 명렬표를 먼저 올려두세요. 판독 직후 오류를 바로 대조합니다.")
    # 수험번호 구조 설정 (학교번호+반+번호)
    import json
    cfg_path = os.path.join(sp, "exam_struct.json")
    cfg0 = {"school_code": "21911", "class_digits": 2, "num_digits": 2}
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            cfg0.update(json.load(f))
    with st.expander("수험번호 구조 설정", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            school_code = st.text_input("학교번호", value=cfg0["school_code"])
        with c2:
            class_digits = st.number_input("반 자리수", min_value=1, max_value=3,
                                           value=int(cfg0["class_digits"]))
        with c3:
            num_digits = st.number_input("번호 자리수", min_value=1, max_value=3,
                                         value=int(cfg0["num_digits"]))
        st.caption(f"예: {school_code} + 반({class_digits}자리) + 번호({num_digits}자리)"
                   f" → {len(school_code) + class_digits + num_digits}자리 수험번호")
        if st.button("구조 저장"):
            save_exam_struct(sp, school_code, class_digits, num_digits)
            st.success("저장됨")
    up = st.file_uploader("명렬표 엑셀", type=["xlsx"])
    if up:
        tp = os.path.join(sp, "roster.xlsx")
        with open(tp, "wb") as f:
            f.write(up.read())
        try:
            roster, cfg = load_roster_full(sp)
            st.success(f"{len(roster)}명 로드됨")
            st.dataframe([{"수험번호": r["exam_no"], "성명": r["name"],
                           "학년": r["grade"], "반": r["class_no"],
                           "번호": r["num"]} for r in roster[:20]])
        except Exception as e:
            st.error(str(e))

# ---------- step 4: PDF read ----------
elif step.startswith("4."):
    st.header("4. 스캔 PDF 업로드 및 판독")
    if not st.session_state.session:
        st.warning("먼저 세션을 만드세요.")
        st.stop()
    sp = sess_path(st.session_state.session)
    tpls = [f for f in os.listdir(sp) if f.startswith("template_") and f.endswith(".json")]
    if not tpls:
        st.warning("2단계에서 템플릿을 먼저 만드세요.")
        st.stop()
    periods = [t[9:-5] for t in tpls]
    roster_x = os.path.join(sp, "roster.xlsx")
    if not os.path.exists(roster_x):
        st.warning("3단계에서 명렬표를 먼저 올리면 판독 직후 오류를 바로 확인할 수 있습니다.")
    st.info("교시별로 스캔 PDF를 올리세요. 한 번에 여러 교시 올려도 됩니다.")

    def rotate_img(img, rot):
        """rot: 0, 90 (시계), -90 (반시계), 180."""
        if rot == 90:
            return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        if rot == -90:
            return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
        if rot == 180:
            return cv2.rotate(img, cv2.ROTATE_180)
        return img

    def get_rotation(sp, period, tpl):
        """저장된 회전값, 없으면 자동 판단 (가로/세로 비율 비교)."""
        import json
        rp = os.path.join(sp, f"rot_{period}.json")
        if os.path.exists(rp):
            with open(rp, encoding="utf-8") as f:
                return json.load(f).get("rot", 0)
        return "auto"

    def resolve_rotation(rot_setting, img, tpl):
        if rot_setting != "auto":
            return rot_setting
        tw, th = tpl.page_size
        h, w = img.shape[:2]
        # 템플릿과 가로/세로가 뒤바뀌었으면 90도 회전
        if (tw > th) != (w > h):
            return 90
        return 0

    def load_results(period):
        import pickle
        rp = os.path.join(sp, f"read_{period}.pkl")
        if os.path.exists(rp):
            with open(rp, "rb") as f:
                return pickle.load(f)
        return []

    def save_results(period, results):
        import pickle
        with open(os.path.join(sp, f"read_{period}.pkl"), "wb") as f:
            pickle.dump(results, f)

    def merge_results(old, new):
        """수정한 답안지만 다시 올렸을 때: 같은 수험번호는 교체, 나머지는 유지."""
        new_nos = {s["exam_no"] for s in new}
        kept = [s for s in old if s["exam_no"] not in new_nos]
        return kept + new

    def is_blank_page(s):
        """수험번호 전부 미기재 + 답안 전부 빈칸 → 빈 페이지(뒷면) 의심."""
        no = s.get("exam_no", "")
        no_blank = (not no) or all(c == "-" for c in no)
        ans = s.get("answers", {})
        qs = [q for q in ans if q != "_thresh"]
        all_blank = bool(qs) and all(ans[q].get("blank") for q in qs)
        return no_blank and all_blank

    def show_check(period, results):
        """판독 결과에 대한 명렬표 대조 표시."""
        if not os.path.exists(roster_x):
            st.warning("명렬표를 올리면 판독 직후 오류를 바로 확인할 수 있습니다.")
            return
        try:
            # 빈 페이지(뒷면 스캔) 분리
            blanks = [s for s in results if is_blank_page(s)]
            valid = [s for s in results if not is_blank_page(s)]
            if blanks:
                st.warning(f"빈 페이지로 의심되는 장이 {len(blanks)}장 있습니다 "
                           f"(뒷면 스캔?). 확인 후 삭제하세요.")
                if st.button(f"빈 페이지 {len(blanks)}장 모두 삭제",
                             key=f"delblank_{period}"):
                    save_results(period, valid)
                    st.success("삭제됨")
                    st.rerun()
            if not valid:
                return
            roster, _cfg = load_roster_full(sp)
            infos = [dict(exam_no=s["exam_no"], name="?") for s in valid]
            verdicts, unsubmitted = RO.check_sheets(infos, roster)
            rows = []
            for s, v in zip(valid, verdicts):
                errs = [m for _, m in v["errors"]]
                errs += s.get("exam_no_errors", [])
                rows.append({"수험번호": v["exam_no_attrib"] or s["exam_no"],
                             "판정": v["status"],
                             "오류": "; ".join(errs)})
            st.subheader(f"{period} 명렬표 대조 결과")
            st.dataframe(rows)
            if unsubmitted:
                st.warning("미제출: " + ", ".join(
                    f"{r['exam_no']}({r['name']})" for r in unsubmitted))
            # 잘못 읽힌 옛 항목 삭제 (수험번호 자체를 고쳐서 다시 스캔한 경우)
            err_nos = [r["수험번호"] for r in rows if r["판정"] != "정상"]
            if err_nos:
                st.caption("수험번호 마킹을 고쳐서 다시 스캔했다면, 잘못 읽힌 옛 항목을 지우세요.")
                del_nos = st.multiselect("삭제할 항목 선택", err_nos,
                                         key=f"del_{period}")
                if del_nos and st.button("선택 삭제", key=f"delbtn_{period}"):
                    del_set = {n.rstrip("*") for n in del_nos}
                    results = [s for s in results
                               if s["exam_no"].rstrip("*") not in del_set]
                    save_results(period, results)
                    st.success(f"{len(del_nos)}건 삭제됨")
                    st.rerun()
        except Exception as e:
            st.error(f"대조 중 오류: {e}")

    for period in periods:
        with st.expander(f"{period} PDF", expanded=False):
            tpl = Template.load(os.path.join(sp, f"template_{period}.json"))
            # 회전 설정
            rot_opts = {"자동": "auto", "회전 없음": 0, "90° 시계방향": 90,
                        "90° 반시계방향": -90, "180°": 180}
            cur_rot = get_rotation(sp, period, tpl)
            cur_label = next((k for k, v in rot_opts.items() if v == cur_rot), "자동")
            rot_label = st.selectbox("스캔 방향", list(rot_opts.keys()),
                                     index=list(rot_opts.keys()).index(cur_label),
                                     key=f"rot_{period}",
                                     help="가로 답안지가 90도 돌아가서 스캔됐다면 90°를 선택하세요.")
            import json
            with open(os.path.join(sp, f"rot_{period}.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"rot": rot_opts[rot_label]}, f)
            pdfs = st.file_uploader(f"{period} 스캔 PDF", type=["pdf"],
                                    accept_multiple_files=True, key=f"pdf_{period}")
            # 미리보기: 첫 페이지 회전 적용 확인
            if pdfs:
                try:
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix=".pdf",
                                                     delete=False) as tf:
                        tf.write(pdfs[0].read())
                        tfp = tf.name
                    pdfs[0].seek(0)
                    p0 = pdfio.render_pages(tfp, dpi=80)[0]
                    os.unlink(tfp)
                    rr = resolve_rotation(rot_opts[rot_label], p0, tpl)
                    prev = rotate_img(p0, rr)
                    prev_small = cv2.resize(prev, (300, int(300 * prev.shape[0] / prev.shape[1])))
                    st.image(cv2.cvtColor(prev_small, cv2.COLOR_BGR2RGB),
                             caption="첫 페이지 미리보기 (회전 적용됨)")
                except Exception:
                    pass
            if pdfs and st.button(f"{period} 판독 시작", key=f"read_{period}"):
                tmp_paths = []
                for p in pdfs:
                    tp = os.path.join(sp, f"upload_{period}_{p.name}")
                    with open(tp, "wb") as f:
                        f.write(p.read())
                    tmp_paths.append(tp)
                prog = st.progress(0)
                total_pages = sum(len(pdfio.render_pages(tp, dpi=120)) for tp in tmp_paths)
                done, results = 0, []
                rot_val = rot_opts[rot_label]
                for tp in tmp_paths:
                    for img, rect, angle in PL.read_pdf_pages(tp, dpi=200):
                        img = rotate_img(img, resolve_rotation(rot_val, img, tpl))
                        s = PL.read_sheet(img, tpl)
                        s["src"] = os.path.basename(tp)
                        results.append(s)
                        done += 1
                        prog.progress(done / max(1, total_pages))
                import pickle
                rp = os.path.join(sp, f"read_{period}.pkl")
                old = load_results(period)
                merged = merge_results(old, results)
                save_results(period, merged)
                added = len(results)
                st.success(f"{period}: {added}장 판독 완료"
                           + (f" (기존 {len(old)}장과 병합 → 총 {len(merged)}장)"
                              if old else ""))
                show_check(period, merged)
            # 저장된 판독 결과 다시 보기
            rp = os.path.join(sp, f"read_{period}.pkl")
            if os.path.exists(rp) and st.button(f"{period} 대조 결과 다시 보기",
                                                key=f"recheck_{period}"):
                import pickle
                with open(rp, "rb") as f:
                    results = pickle.load(f)
                show_check(period, results)

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
        roster, _cfg = load_roster_full(sp)
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
            roster, _cfg = load_roster_full(sp)
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
