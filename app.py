"""
🛒 쿠팡 파트너스 블로그 자동화
- 쿠팡 상품 URL → 정보 추출
- Gemini API → SEO 최적화 리뷰 글 작성
- 워드프레스 REST API → 자동 포스팅
"""

import streamlit as st
import requests
from bs4 import BeautifulSoup
import json, re

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# ── Gemini ────────────────────────────────────────────────────
def _get_model(api_key):
    preferred = ["gemini-2.5-flash-preview-04-17","gemini-2.5-pro-exp-03-25",
                 "gemini-1.5-flash-002","gemini-1.5-flash-001","gemini-1.5-pro-001"]
    try:
        data = requests.get(f"{GEMINI_BASE}?key={api_key}", timeout=10).json()
        avail = [m["name"].replace("models/","") for m in data.get("models",[])
                 if "generateContent" in m.get("supportedGenerationMethods",[])]
        for p in preferred:
            if p in set(avail): return p
        if avail: return avail[0]
    except: pass
    return preferred[0]

def gemini(prompt, api_key):
    model = _get_model(api_key)
    r = requests.post(f"{GEMINI_BASE}/{model}:generateContent?key={api_key}",
        json={"contents":[{"parts":[{"text":prompt}]}],
              "generationConfig":{"temperature":0.7,"maxOutputTokens":4000}},
        timeout=60)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

# ── 쿠팡 상품 정보 추출 ───────────────────────────────────────
def extract_coupang(url):
    try:
        h = {**HEADERS, "Referer":"https://www.coupang.com/"}
        soup = BeautifulSoup(requests.get(url,headers=h,timeout=15,allow_redirects=True).text,"html.parser")

        name = ""
        for sel in ["h1.prod-buy-header__title","h2.prod-buy-header__title","title"]:
            el = soup.select_one(sel)
            if el:
                name = el.get_text(strip=True)
                if sel=="title": name = re.sub(r"\s*[\||\-]\s*쿠팡.*","",name).strip()
                break

        price = ""
        for sel in ["span.total-price strong","span[class*='price-value']","strong[class*='price']"]:
            el = soup.select_one(sel)
            if el: price = el.get_text(strip=True); break

        rating = ""
        for sel in ["span.ratingValue","span[class*='rating']"]:
            el = soup.select_one(sel)
            if el: rating = el.get_text(strip=True); break

        review_count = ""
        for sel in ["span.count","span[class*='count']"]:
            el = soup.select_one(sel)
            if el: review_count = el.get_text(strip=True).strip("()"); break

        features = []
        for sel in ["ul.prod-description-attribute li","div[class*='description'] li"]:
            items = soup.select(sel)
            if items: features = [i.get_text(strip=True) for i in items[:8]]; break

        img_url = ""
        for sel in ["img#rep-image","img[class*='prod-image']"]:
            el = soup.select_one(sel)
            if el:
                img_url = el.get("src") or el.get("data-src","")
                if img_url.startswith("//"): img_url = "https:" + img_url
                break

        return {"name":name or "상품명 추출 실패","price":price or "","rating":rating,
                "review_count":review_count,"features":features,"image_url":img_url,"url":url}
    except Exception as e:
        return {"error":str(e)}

# ── 블로그 글 생성 ────────────────────────────────────────────
def generate_post(product, partner_url, api_key, category_hint=""):
    features_text = "\n".join([f"- {f}" for f in product.get("features",[])]) or "- 제품 특징"
    prompt = f"""당신은 쿠팡 파트너스 수익을 위한 한국어 SEO 블로그 전문가입니다.

[상품 정보]
- 상품명: {product['name']}
- 가격: {product.get('price','')}
- 별점: {product.get('rating','')}
- 리뷰 수: {product.get('review_count','')}
- 주요 특징:
{features_text}
- 파트너스 링크: {partner_url}
{"- 카테고리: " + category_hint if category_hint else ""}

[작성 조건]
1. 제목: 검색 의도 반영, 60자 이내 (상품명+혜택)
2. 글 길이: 1500~2000자
3. 구조:
   - 도입부: 이 상품이 필요한 상황 공감
   - 상품 핵심 특징 3가지 (h2 소제목 사용)
   - 장점 위주 후기, 단점 1가지 솔직하게
   - 가격 분석 및 가성비
   - 구매 추천 마무리
4. 파트너스 링크는 본문에 2~3회 자연스럽게:
   <a href="{partner_url}" target="_blank" rel="noopener">👉 쿠팡에서 최저가 확인하기</a>
5. 말투: 친근하고 솔직한 리뷰어 (광고 티 안 나게)
6. HTML 형식 (h2, h3, p, ul, li, a, strong 태그 사용)

JSON만 출력 (백틱 없이):
{{
  "title": "SEO 최적화 제목",
  "meta_description": "검색 결과 설명 160자 이내",
  "tags": ["태그1","태그2","태그3","태그4","태그5"],
  "content": "HTML 본문 전체",
  "focus_keyword": "핵심 키워드"
}}"""

    raw = gemini(prompt, api_key)
    raw = re.sub(r"^```json\s*|\s*```$","",raw,flags=re.MULTILINE).strip()
    raw = re.sub(r"^```\s*|\s*```$","",raw,flags=re.MULTILINE).strip()
    return json.loads(raw)

# ── 워드프레스 포스팅 ─────────────────────────────────────────
def wp_get_categories(wp_url, user, pw):
    try:
        r = requests.get(f"{wp_url.rstrip('/')}/wp-json/wp/v2/categories?per_page=50",
                         auth=(user,pw), timeout=10)
        return {c["name"]:c["id"] for c in r.json()}
    except: return {}

def wp_post(wp_url, user, pw, post_data, status="draft", category_id=None):
    # 태그 생성
    tag_ids = []
    for tag in post_data.get("tags",[])[:5]:
        try:
            r = requests.post(f"{wp_url.rstrip('/')}/wp-json/wp/v2/tags",
                              json={"name":tag}, auth=(user,pw), timeout=10)
            if r.status_code in (200,201):
                tag_ids.append(r.json()["id"])
            elif r.status_code==400:
                tid = r.json().get("data",{}).get("term_id")
                if tid: tag_ids.append(tid)
        except: pass

    payload = {
        "title":   post_data["title"],
        "content": post_data["content"],
        "excerpt": post_data.get("meta_description",""),
        "status":  status,
        "tags":    tag_ids,
    }
    if category_id:
        payload["categories"] = [category_id]

    r = requests.post(f"{wp_url.rstrip('/')}/wp-json/wp/v2/posts",
                      json=payload, auth=(user,pw), timeout=30)
    r.raise_for_status()
    return r.json()

# ── Streamlit UI ──────────────────────────────────────────────
def main():
    st.set_page_config(page_title="쿠팡 파트너스 블로그 자동화",
                       page_icon="🛒", layout="wide")
    st.markdown("<style>.stButton>button{border-radius:10px;font-weight:bold;}</style>",
                unsafe_allow_html=True)
    st.title("🛒 쿠팡 파트너스 블로그 자동화")
    st.caption("쿠팡 상품 URL → AI 리뷰 글 작성 → 워드프레스 자동 포스팅")

    # ── 사이드바 ──────────────────────────────────────────────
    with st.sidebar:
        st.header("⚙️ 설정")

        gemini_key = st.secrets.get("GEMINI_API_KEY","")
        if gemini_key: st.success("🔑 Gemini: secrets 적용됨")
        else: gemini_key = st.text_input("🔑 Gemini API Key", type="password", placeholder="AIza...")

        st.divider()
        st.subheader("🌐 워드프레스")

        wp_url  = st.secrets.get("WP_URL","")  or st.text_input("블로그 주소", value="https://sesyhj-happy24.com")
        wp_user = st.secrets.get("WP_USER","") or st.text_input("관리자 아이디", placeholder="HappyRich")
        wp_pass = st.secrets.get("WP_APP_PASSWORD","") or \
                  st.text_input("앱 비밀번호", type="password",
                                placeholder="xxxx xxxx xxxx xxxx xxxx xxxx")

        st.divider()
        if st.button("🔌 연결 테스트", use_container_width=True):
            with st.spinner("확인 중..."):
                try:
                    r = requests.get(f"{wp_url.rstrip('/')}/wp-json/wp/v2/posts?per_page=1",
                                     auth=(wp_user,wp_pass), timeout=10)
                    if r.status_code==200: st.success("✅ 연결 성공!")
                    else: st.error(f"❌ {r.status_code}: {r.text[:100]}")
                except Exception as e: st.error(f"❌ {e}")

        st.divider()
        st.info("💡 앱 비밀번호:\n워드프레스 관리자\n→ 사용자 → 프로필\n→ 애플리케이션 비밀번호\n→ 새로 추가")

    if not gemini_key:
        st.warning("👈 Gemini API Key를 입력해주세요"); st.stop()

    # ① 상품 입력
    st.subheader("① 상품 정보 입력")
    c1, c2 = st.columns(2)
    with c1:
        coupang_url = st.text_input("🔗 쿠팡 상품 URL",
                                    placeholder="https://www.coupang.com/vp/products/...")
    with c2:
        partner_url = st.text_input("🤝 쿠팡 파트너스 링크",
                                    placeholder="https://link.coupang.com/a/...")

    if coupang_url:
        col_btn, _ = st.columns([1,3])
        with col_btn:
            if st.button("🔍 상품 정보 자동 추출"):
                with st.spinner("쿠팡 상품 정보 가져오는 중..."):
                    result = extract_coupang(coupang_url)
                    if "error" in result:
                        st.warning(f"자동 추출 실패: {result['error']}\n아래에서 직접 입력해주세요.")
                    else:
                        st.session_state["product"] = result
                        st.success("✅ 상품 정보 추출 완료!")

    # 수동 입력
    with st.expander("✏️ 상품 정보 직접 입력 (자동 추출 안 될 때)"):
        with st.form("manual"):
            mn = st.text_input("상품명 *")
            mp = st.text_input("가격 (예: 89,900원)")
            mr = st.text_input("리뷰 수 (예: 12,847개)")
            mf = st.text_area("주요 특징 (한 줄에 하나씩)", height=100,
                              placeholder="에어프라이어 4.2L 대용량\n기름 없이 바삭하게\n디지털 온도 조절...")
            if st.form_submit_button("입력 완료", type="primary") and mn:
                st.session_state["product"] = {
                    "name":mn,"price":mp,"review_count":mr,
                    "features":[f.strip() for f in mf.split("\n") if f.strip()],
                    "image_url":"","url":coupang_url or "",
                }
                st.success("✅ 입력 완료!")

    # 상품 정보 표시
    if "product" in st.session_state:
        p = st.session_state["product"]
        with st.container(border=True):
            cc1, cc2 = st.columns([1,4])
            with cc1:
                if p.get("image_url"):
                    try: st.image(p["image_url"], width=120)
                    except: pass
            with cc2:
                st.markdown(f"**📦 {p['name']}**")
                st.markdown(f"💰 {p.get('price','')}  |  ⭐ {p.get('rating','')}  |  💬 리뷰 {p.get('review_count','')}")
                if p.get("features"):
                    st.markdown("**특징:** " + " · ".join(p["features"][:3]))

        # ② 글 생성
        st.divider()
        st.subheader("② AI 블로그 글 생성")
        category_hint = st.text_input("카테고리 힌트 (선택)",
                                      placeholder="예: 주방가전, 다이어트, 육아용품")

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
                        with st.expander("상세 오류"): st.code(traceback.format_exc())

    # ③ 글 확인 & 포스팅
    if "post" in st.session_state:
        post = st.session_state["post"]
        st.divider()
        st.subheader("③ 글 확인 & 워드프레스 포스팅")

        t_col, m_col = st.columns(2)
        with t_col:
            edited_title = st.text_input("📌 제목", value=post.get("title",""))
        with m_col:
            edited_meta  = st.text_input("🔍 메타 설명", value=post.get("meta_description",""))

        st.caption(f"🏷️ 태그: {', '.join(post.get('tags',[]))}  |  🎯 키워드: {post.get('focus_keyword','')}")

        tab1, tab2 = st.tabs(["👁️ 미리보기", "✏️ HTML 수정"])
        with tab1:
            st.markdown(post.get("content",""), unsafe_allow_html=True)
        with tab2:
            edited_html = st.text_area("HTML 본문 직접 수정", value=post.get("content",""), height=400)
            if st.button("💾 수정 반영"):
                st.session_state["post"]["content"]          = edited_html
                st.session_state["post"]["title"]            = edited_title
                st.session_state["post"]["meta_description"] = edited_meta
                st.success("반영됨!"); st.rerun()

        st.divider()

        # 포스팅 옵션
        pa, pb, pc = st.columns(3)
        with pa:
            post_status = st.radio("발행 상태", ["📝 임시저장","🚀 바로 발행"],
                                   help="처음엔 임시저장 후 검토 권장")
        with pb:
            cats = wp_get_categories(wp_url, wp_user, wp_pass) if (wp_url and wp_user and wp_pass) else {}
            cat_name = st.selectbox("카테고리", ["선택 안 함"] + list(cats.keys()))
        with pc:
            st.write(""); st.write("")
            post_btn = st.button("🚀 워드프레스에 포스팅!", type="primary", use_container_width=True)

        if post_btn:
            if not (wp_url and wp_user and wp_pass):
                st.error("❌ 워드프레스 설정을 사이드바에 입력해주세요!")
            else:
                status = "draft" if "임시저장" in post_status else "publish"
                cat_id = cats.get(cat_name) if cat_name != "선택 안 함" else None
                final  = {**post, "title":edited_title, "meta_description":edited_meta}
                with st.spinner("워드프레스 포스팅 중..."):
                    try:
                        result = wp_post(wp_url, wp_user, wp_pass, final, status, cat_id)
                        link   = result.get("link","")
                        st.success("🎉 포스팅 완료!")
                        if link: st.markdown(f"**📎 글 주소:** [{link}]({link})")
                        st.balloons()
                    except Exception as e:
                        st.error(f"포스팅 실패: {e}")
                        import traceback
                        with st.expander("상세 오류"): st.code(traceback.format_exc())

        # 수동 복사용
        with st.expander("📋 HTML 복사 (티스토리 등 수동 붙여넣기용)"):
            st.code(post.get("content",""), language="html")

        if st.button("🔄 새 글 작성", use_container_width=True):
            for k in ["product","post"]: st.session_state.pop(k,None)
            st.rerun()

if __name__ == "__main__":
    main()
