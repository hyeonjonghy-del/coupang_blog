import streamlit as st
import requests
from bs4 import BeautifulSoup
import json, re, urllib.parse, os, csv
from datetime import datetime

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# ── 포스팅 이력 관리 ──────────────────────────────────────────
HISTORY_FILE = "/tmp/posting_history.json"

def load_history():
    """포스팅 이력 불러오기"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_history(history):
    """포스팅 이력 저장"""
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def add_history(product_name, partner_url, post_title, post_link, category=""):
    """이력에 새 항목 추가"""
    history = load_history()
    history.append({
        "date":         datetime.now().strftime("%Y-%m-%d %H:%M"),
        "product_name": product_name,
        "category":     category,
        "partner_url":  partner_url,
        "post_title":   post_title,
        "post_link":    post_link,
    })
    save_history(history)

def check_duplicate(product_name):
    """중복 상품 체크"""
    history = load_history()
    for item in history:
        # 상품명 70% 이상 유사하면 중복으로 판단
        name1 = product_name.replace(" ", "").lower()
        name2 = item["product_name"].replace(" ", "").lower()
        if name1 in name2 or name2 in name1:
            return item
    return None

def history_to_csv(history):
    """이력을 CSV 형식으로 변환"""
    import io
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["date","product_name","category","partner_url","post_title","post_link"])
    writer.writeheader()
    writer.writerows(history)
    return output.getvalue().encode("utf-8-sig")

# ── Gemini ────────────────────────────────────────────────────
def gemini_call(prompt, api_key):
    preferred = [
        "gemini-2.5-flash-preview-04-17",
        "gemini-2.5-pro-preview-03-25",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
        "gemini-1.0-pro",
    ]
    # 사용 가능한 모델 조회
    try:
        data = requests.get(f"{GEMINI_BASE}?key={api_key}", timeout=10).json()
        available = [
            m["name"].replace("models/", "")
            for m in data.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
        if available:
            # preferred 순서로 있는 것만, 없으면 available 전체
            ordered = [m for m in preferred if m in set(available)]
            models  = ordered if ordered else available
        else:
            models = preferred
    except Exception:
        models = preferred

    last_err = None
    for model in models:
        try:
            r = requests.post(
                f"{GEMINI_BASE}/{model}:generateContent?key={api_key}",
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.7, "maxOutputTokens": 8000},
                },
                timeout=90,
            )
            if r.status_code in (503, 429, 404):
                last_err = f"{model}: {r.status_code}"
                continue
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception as e:
            last_err = str(e)
            continue
    raise RuntimeError(f"모든 모델 실패: {last_err}")


# ── 블로그 글 생성 ────────────────────────────────────────────
def generate_post(product, partner_url, api_key, category_hint=""):
    name  = product.get("name", "")
    price = product.get("price", "") or "미상"
    cat   = category_hint or ""

    # 1단계: 제목/태그/슬러그 생성
    meta_prompt = (
        "상품명: " + name + "\n"
        "가격: " + price + "\n"
        + ("카테고리: " + cat + "\n" if cat else "")
        + "이 상품에 대해 잘 아는 전문가로서 아래 JSON만 출력 (백틱 없이):\n"
        '{"title":"SEO 최적화 제목 60자 이내",'
        '"meta_description":"검색결과 설명 160자 이내",'
        '"slug":"english-lowercase-hyphen-slug-max5words",'
        '"tags":["태그1","태그2","태그3","태그4","태그5"],'
        '"focus_keyword":"핵심키워드"}'
    )
    meta_raw = gemini_call(meta_prompt, api_key)
    meta_raw = re.sub(r"^```json\s*|\s*```$", "", meta_raw, flags=re.MULTILINE).strip()
    meta_raw = re.sub(r"^```\s*|\s*```$",     "", meta_raw, flags=re.MULTILINE).strip()
    meta = json.loads(meta_raw)
    keyword = meta.get("focus_keyword", name)

    # 2단계: 본문 생성
    link_html = '<a href="' + partner_url + '" target="_blank" rel="noopener">쿠팡에서 최저가 확인하기</a>'
    notice    = "<p><em>이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.</em></p>"

    # 2단계: 본문 전반부 (도입부 + 특징 3가지)
    part1_prompt = (
        "한국어 SEO 블로그 리뷰 전반부를 HTML로 작성하세요.\n\n"
        "상품명: " + name + "\n"
        "가격: " + price + "\n"
        + ("카테고리: " + cat + "\n" if cat else "")
        + "핵심 키워드: " + keyword + "\n\n"
        "순서대로 작성:\n"
        "1. p 도입부 2개 (공감 유도 + 상품 소개)\n"
        "2. 파트너스 링크 1회: " + link_html + "\n"
        "3. h2 핵심 특징 3가지 (각각 h3 + p + ul 3항목)\n\n"
        "HTML만 출력 (설명 없이)\n"
    )

    # 본문 후반부 (장단점 + 가격 + 구매추천)
    part2_prompt = (
        "한국어 SEO 블로그 리뷰 후반부를 HTML로 작성하세요.\n\n"
        "상품명: " + name + "\n"
        "가격: " + price + "\n"
        "핵심 키워드: " + keyword + "\n\n"
        "순서대로 작성:\n"
        "1. h2 장단점: 장점3개(p) 단점1개(p)\n"
        "2. h2 가격 및 가성비: p 1개\n"
        "3. h2 구매 추천: p + 파트너스 링크: " + link_html + "\n"
        "4. 마지막: " + notice + "\n\n"
        "HTML만 출력 (설명 없이)\n"
    )

    part1 = gemini_call(part1_prompt, api_key)
    part1 = re.sub(r"^```html\s*|\s*```$", "", part1, flags=re.MULTILINE).strip()
    part1 = re.sub(r"^```\s*|\s*```$",     "", part1, flags=re.MULTILINE).strip()

    part2 = gemini_call(part2_prompt, api_key)
    part2 = re.sub(r"^```html\s*|\s*```$", "", part2, flags=re.MULTILINE).strip()
    part2 = re.sub(r"^```\s*|\s*```$",     "", part2, flags=re.MULTILINE).strip()

    content = part1 + "\n\n" + part2

    return {**meta, "content": content}


# ── 워드프레스 ────────────────────────────────────────────────
def wp_get_categories(wp_url, user, pw):
    try:
        r = requests.get(
            wp_url.rstrip("/") + "/wp-json/wp/v2/categories?per_page=50",
            auth=(user, pw), timeout=10,
        )
        return {c["name"]: c["id"] for c in r.json()}
    except Exception:
        return {}


def wp_post(wp_url, user, pw, post_data, status="draft", category_id=None):
    tag_ids = []
    for tag in post_data.get("tags", [])[:5]:
        try:
            r = requests.post(
                wp_url.rstrip("/") + "/wp-json/wp/v2/tags",
                json={"name": tag}, auth=(user, pw), timeout=10,
            )
            if r.status_code in (200, 201):
                tag_ids.append(r.json()["id"])
            elif r.status_code == 400:
                tid = r.json().get("data", {}).get("term_id")
                if tid:
                    tag_ids.append(tid)
        except Exception:
            pass

    payload = {
        "title":   post_data["title"],
        "content": post_data["content"],
        "excerpt": post_data.get("meta_description", ""),
        "slug":    post_data.get("slug", ""),
        "status":  status,
        "tags":    tag_ids,
    }
    if category_id:
        payload["categories"] = [category_id]

    r = requests.post(
        wp_url.rstrip("/") + "/wp-json/wp/v2/posts",
        json=payload, auth=(user, pw), timeout=30,
    )
    r.raise_for_status()
    return r.json()


# ── Streamlit UI ──────────────────────────────────────────────
def main():
    st.set_page_config(page_title="쿠팡 파트너스 블로그 자동화",
                       page_icon="🛒", layout="wide")
    st.markdown("<style>.stButton>button{border-radius:10px;font-weight:bold;}</style>",
                unsafe_allow_html=True)
    st.title("🛒 쿠팡 파트너스 블로그 자동화")
    st.caption("상품명 + 파트너스 링크 → AI 리뷰 글 → 워드프레스 자동 포스팅")

    # 사이드바
    with st.sidebar:
        st.header("⚙️ 설정")
        gemini_key = st.secrets.get("GEMINI_API_KEY", "")
        if gemini_key:
            st.success("🔑 Gemini: secrets 적용됨")
        else:
            gemini_key = st.text_input("🔑 Gemini API Key", type="password", placeholder="AIza...")

        st.divider()
        st.subheader("🌐 워드프레스")
        wp_url  = st.secrets.get("WP_URL", "")  or st.text_input("블로그 주소", value="https://sesyhj-happy24.com")
        wp_user = st.secrets.get("WP_USER", "") or st.text_input("관리자 아이디", placeholder="ses0507")
        wp_pass = st.secrets.get("WP_APP_PASSWORD", "") or \
                  st.text_input("앱 비밀번호", type="password", placeholder="xxxx xxxx xxxx xxxx xxxx xxxx")

        st.divider()
        if st.button("🔌 워드프레스 연결 테스트", use_container_width=True):
            with st.spinner("확인 중..."):
                try:
                    r = requests.get(wp_url.rstrip("/") + "/wp-json/wp/v2/posts?per_page=1",
                                     auth=(wp_user, wp_pass), timeout=10)
                    if r.status_code == 200:
                        st.success("✅ 연결 성공!")
                    else:
                        st.error(f"❌ {r.status_code}")
                except Exception as e:
                    st.error(str(e))

        st.divider()

        # 이력 현황
        history = load_history()
        st.metric("📊 총 포스팅 수", f"{len(history)}개")
        if history:
            st.caption(f"최근: {history[-1]['product_name'][:15]}...")

        if st.button("🔍 Gemini 모델 목록 확인", use_container_width=True):
            with st.spinner("조회 중..."):
                try:
                    r = requests.get(f"{GEMINI_BASE}?key={gemini_key}", timeout=10)
                    st.caption(f"HTTP: {r.status_code}")
                    data = r.json()
                    if "error" in data:
                        st.error(f"API 오류: {data['error'].get('message','')}")
                    else:
                        names = [
                            m["name"].replace("models/","")
                            for m in data.get("models",[])
                            if "generateContent" in m.get("supportedGenerationMethods",[])
                        ]
                        if names:
                            st.success(f"✅ {len(names)}개 모델")
                            for n in names: st.code(n)
                        else:
                            st.warning("사용 가능한 모델 없음")
                            st.json(data)
                except Exception as e:
                    st.error(str(e))

        st.divider()
        st.info("💡 앱 비밀번호:\n워드프레스 관리자\n→ 사용자 → 프로필\n→ 애플리케이션 비밀번호\n→ 새로 추가")

    if not gemini_key:
        st.warning("👈 Gemini API Key를 입력해주세요")
        st.stop()

    # 메인 탭
    tab_write, tab_history = st.tabs(["✍️ 글 작성", "📋 포스팅 이력"])

    # ══════════════════════════════════════════════════════════
    # 이력 탭
    # ══════════════════════════════════════════════════════════
    with tab_history:
        st.subheader("📋 포스팅 이력 관리")
        history = load_history()

        if not history:
            st.info("아직 포스팅 이력이 없습니다. 글을 작성하고 포스팅하면 자동으로 기록됩니다.")
        else:
            # 통계
            c1, c2, c3 = st.columns(3)
            with c1: st.metric("총 포스팅", f"{len(history)}개")
            with c2: st.metric("오늘 포스팅", f"{sum(1 for h in history if h['date'].startswith(datetime.now().strftime('%Y-%m-%d')))}개")
            with c3:
                # CSV 다운로드
                csv_data = history_to_csv(history)
                st.download_button("⬇️ 엑셀로 내보내기", data=csv_data,
                                   file_name=f"posting_history_{datetime.now().strftime('%Y%m%d')}.csv",
                                   mime="text/csv", use_container_width=True)

            st.divider()

            # 검색
            search = st.text_input("🔍 상품명 검색", placeholder="찾을 상품명 입력")
            filtered = [h for h in reversed(history)
                       if search.lower() in h["product_name"].lower()] if search else list(reversed(history))

            # 목록 표시
            for h in filtered:
                with st.container(border=True):
                    col1, col2, col3 = st.columns([3, 2, 2])
                    with col1:
                        st.markdown(f"**{h['product_name']}**")
                        if h.get("category"):
                            st.caption(f"카테고리: {h['category']}")
                    with col2:
                        st.caption(f"📅 {h['date']}")
                        if h.get("partner_url"):
                            st.caption(f"🔗 파트너스 링크 있음")
                    with col3:
                        if h.get("post_link"):
                            st.link_button("글 보기 →", h["post_link"], use_container_width=True)

    # ══════════════════════════════════════════════════════════
    # 글 작성 탭
    # ══════════════════════════════════════════════════════════
    with tab_write:

    # ① 상품 정보 입력
        st.subheader("① 상품 정보 입력")
        c1, c2 = st.columns(2)
        with c1:
            partner_url = st.text_input(
                "🤝 쿠팡 파트너스 링크",
                placeholder="https://link.coupang.com/a/...",
            )
        with c2:
            st.write("")

        st.markdown("**상품명과 가격을 입력하세요** (10초면 됩니다)")
        with st.form("product_form"):
            col1, col2 = st.columns(2)
            with col1:
                mn = st.text_input("상품명 *", placeholder="예: 아미니 플라워테라피 바디워시 1L 2개")
            with col2:
                mp = st.text_input("가격", placeholder="예: 22,110원")
            submitted = st.form_submit_button("✅ 입력 완료", type="primary")
            if submitted and mn:
                # 중복 체크
                dup = check_duplicate(mn)
                if dup:
                    st.warning(f"⚠️ 이미 포스팅한 상품과 유사합니다!\n\n"
                              f"**{dup['product_name']}** ({dup['date']} 포스팅)\n\n"
                              f"그래도 계속 진행하려면 다시 한번 입력 완료를 눌러주세요.")
                    st.session_state["dup_warned"] = True
                else:
                    st.session_state["dup_warned"] = False

                st.session_state["product"] = {
                    "name": mn, "price": mp,
                    "review_count": "", "features": [],
                    "image_url": "", "url": "",
                }
                if not dup:
                    st.success(f"✅ '{mn}' 입력 완료!")

        # 상품 확인
        if "product" in st.session_state:
            p = st.session_state["product"]
            with st.container(border=True):
                st.markdown(f"**📦 {p['name']}**  |  💰 {p.get('price','')}")

            # ② 글 생성
            st.divider()
            st.subheader("② AI 블로그 글 생성")
            category_hint = st.text_input("카테고리 힌트 (선택)", placeholder="예: 바디워시, 주방가전, 다이어트")

            if st.button("✍️ SEO 블로그 글 자동 생성", type="primary", use_container_width=True):
                if not partner_url:
                st.warning("⚠️ 파트너스 링크를 먼저 입력해주세요!")
            else:
                with st.spinner("✨ Gemini가 SEO 최적화 글 작성 중... (30~60초)"):
                    try:
                        post = generate_post(p, partner_url, gemini_key, category_hint)
                        st.session_state["post"] = post
                        st.success("✅ 글 생성 완료!")
                    except Exception as e:
                        st.error(f"생성 실패: {e}")
                        import traceback
                        with st.expander("상세 오류"):
                            st.code(traceback.format_exc())

    # ③ 글 확인 & 포스팅
    if "post" in st.session_state:
        post = st.session_state["post"]
        st.divider()
        st.subheader("③ 글 확인 & 워드프레스 포스팅")

        t_col, m_col, s_col = st.columns(3)
        with t_col:
            edited_title = st.text_input("📌 제목", value=post.get("title", ""))
        with m_col:
            edited_meta  = st.text_input("🔍 메타 설명", value=post.get("meta_description", ""))
        with s_col:
            edited_slug  = st.text_input("🔗 슬러그", value=post.get("slug", ""),
                                         help="영문 소문자 + 하이픈")

        st.caption(f"🏷️ 태그: {', '.join(post.get('tags', []))}  |  🎯 키워드: {post.get('focus_keyword','')}")

        tab1, tab2 = st.tabs(["👁️ 미리보기", "✏️ HTML 수정"])
        with tab1:
            st.markdown(post.get("content", ""), unsafe_allow_html=True)
        with tab2:
            edited_html = st.text_area("HTML 본문", value=post.get("content", ""), height=400)
            if st.button("💾 수정 반영"):
                st.session_state["post"]["content"]          = edited_html
                st.session_state["post"]["title"]            = edited_title
                st.session_state["post"]["meta_description"] = edited_meta
                st.session_state["post"]["slug"]             = edited_slug
                st.success("반영됨!")
                st.rerun()

        st.divider()
        pa, pb, pc = st.columns(3)
        with pa:
            post_status = st.radio("발행 상태", ["📝 임시저장", "🚀 바로 발행"])
        with pb:
            cats     = wp_get_categories(wp_url, wp_user, wp_pass) if (wp_url and wp_user and wp_pass) else {}
            cat_name = st.selectbox("카테고리", ["선택 안 함"] + list(cats.keys()))
        with pc:
            st.write("")
            st.write("")
            post_btn = st.button("🚀 워드프레스에 포스팅!", type="primary", use_container_width=True)

        if post_btn:
            if not (wp_url and wp_user and wp_pass):
                st.error("❌ 워드프레스 설정을 사이드바에 입력해주세요!")
            else:
                status   = "draft" if "임시저장" in post_status else "publish"
                cat_id   = cats.get(cat_name) if cat_name != "선택 안 함" else None
                final    = {
                    **post,
                    "title":            edited_title,
                    "meta_description": edited_meta,
                    "slug":             edited_slug,
                }
                with st.spinner("워드프레스 포스팅 중..."):
                    try:
                        result = wp_post(wp_url, wp_user, wp_pass, final, status, cat_id)
                        link   = result.get("link", "")
                        st.success("🎉 포스팅 완료!")
                        if link:
                            st.markdown(f"**📎 글 주소:** [{link}]({link})")
                        # 이력 자동 저장
                        add_history(
                            product_name = p["name"],
                            partner_url  = partner_url,
                            post_title   = edited_title,
                            post_link    = link,
                            category     = cat_name if cat_name != "선택 안 함" else "",
                        )
                        st.toast("📋 포스팅 이력에 저장됐습니다!", icon="✅")
                        st.balloons()
                    except Exception as e:
                        st.error(f"포스팅 실패: {e}")
                        import traceback
                        with st.expander("상세 오류"):
                            st.code(traceback.format_exc())

        with st.expander("📋 HTML 복사 (수동 붙여넣기용)"):
            st.code(post.get("content", ""), language="html")

        if st.button("🔄 새 글 작성", use_container_width=True):
            for k in ["product", "post"]:
                st.session_state.pop(k, None)
            st.rerun()


if __name__ == "__main__":
    main()
