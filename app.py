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
    """실제 사용 가능한 모델 조회 후 순서대로 시도"""
    # 1단계: 사용 가능한 모델 목록 조회
    preferred_order = [
        "gemini-2.5-flash-preview-04-17",
        "gemini-2.5-pro-exp-03-25",
        "gemini-2.5-pro-preview-03-25",
        "gemini-1.5-flash-002",
        "gemini-1.5-flash-001",
        "gemini-1.5-flash",
        "gemini-1.5-pro-002",
        "gemini-1.5-pro-001",
        "gemini-1.5-pro",
        "gemini-1.0-pro",
    ]
    try:
        data = requests.get(f"{GEMINI_BASE}?key={api_key}", timeout=10).json()
        available = [
            m["name"].replace("models/", "")
            for m in data.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
        # preferred 순서대로 available 중에서 선택
        models_to_try = [m for m in preferred_order if m in set(available)]
        # preferred에 없는 것도 뒤에 추가
        for m in available:
            if m not in models_to_try:
                models_to_try.append(m)
    except Exception:
        models_to_try = preferred_order

    if not models_to_try:
        raise RuntimeError("사용 가능한 Gemini 모델이 없습니다.")

    last_err = None
    for model in models_to_try:
        try:
            r = requests.post(
                f"{GEMINI_BASE}/{model}:generateContent?key={api_key}",
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": 0.7, "maxOutputTokens": 8000}},
                timeout=90,
            )
            if r.status_code in (503, 429):
                last_err = f"{model}: 서버 과부하({r.status_code})"
                continue
            if r.status_code == 404:
                last_err = f"{model}: 모델 없음(404)"
                continue
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        except requests.exceptions.Timeout:
            last_err = f"{model}: 타임아웃"
            continue
        except Exception as e:
            last_err = str(e)
            continue

    raise RuntimeError(f"모든 모델 실패. 마지막 오류: {last_err}")

def safe_json_parse(raw):
    """JSON 파싱 - 잘린 경우도 최대한 복구"""
    raw = re.sub(r"^```json\s*|\s*```$","",raw,flags=re.MULTILINE).strip()
    raw = re.sub(r"^```\s*|\s*```$","",raw,flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # content 필드가 잘린 경우 닫아주기 시도
        if '"content"' in raw and not raw.rstrip().endswith("}"):
            for ending in ['"}}\n}', '"}\n}', '..."}']  :
                try:
                    return json.loads(raw + ending)
                except:
                    pass
        raise

# ── 쿠팡 상품 정보 추출 ───────────────────────────────────────
def extract_coupang(url):
    """쿠팡 상품 정보 추출 - 파트너스 링크 리다이렉트 지원"""
    try:
        h = {
            **HEADERS,
            "Referer": "https://www.coupang.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        # 파트너스 링크면 리다이렉트 따라가서 실제 URL 획득
        if "link.coupang.com" in url:
            r = requests.get(url, headers=h, timeout=15,
                             allow_redirects=True)
            url = r.url   # 최종 리다이렉트된 실제 URL

        resp = requests.get(url, headers=h, timeout=15, allow_redirects=True)
        soup = BeautifulSoup(resp.text, "html.parser")

        # ── 상품명 ──────────────────────────────────────────
        name = ""
        for sel in [
            "h1.prod-buy-header__title",
            "h2.prod-buy-header__title",
            "[class*='prod-buy-header__title']",
            "h1[class*='title']",
        ]:
            el = soup.select_one(sel)
            if el:
                name = el.get_text(strip=True)
                break
        if not name:
            # title 태그에서 추출
            t = soup.find("title")
            if t:
                name = re.sub(r"\s*[\||\-]\s*쿠팡.*", "", t.get_text()).strip()

        # ── 가격 ──────────────────────────────────────────
        price = ""
        for sel in [
            "span.total-price strong",
            "span[class*='price-value']",
            "strong[class*='price']",
            "[class*='total-price']",
        ]:
            el = soup.select_one(sel)
            if el:
                price = el.get_text(strip=True)
                break

        # ── 할인율 ────────────────────────────────────────
        discount = ""
        for sel in ["span.discount-rate", "[class*='discount-rate']"]:
            el = soup.select_one(sel)
            if el:
                discount = el.get_text(strip=True)
                break
        if discount:
            price = f"{price} ({discount} 할인)"

        # ── 별점 / 리뷰 수 ────────────────────────────────
        rating = ""
        for sel in ["span.ratingValue", "[class*='ratingValue']", "[class*='rating-star']"]:
            el = soup.select_one(sel)
            if el:
                rating = el.get_text(strip=True)
                break

        review_count = ""
        for sel in ["span.count", "[class*='ratingCount']", "[class*='review-count']"]:
            el = soup.select_one(sel)
            if el:
                review_count = el.get_text(strip=True).strip("()")
                break

        # ── 상품 특징 ─────────────────────────────────────
        features = []
        for sel in [
            "ul.prod-description-attribute li",
            "div[class*='description'] li",
            "ul[class*='prod-attr'] li",
            "[class*='item-detail'] li",
        ]:
            items = soup.select(sel)
            if items:
                features = [i.get_text(strip=True) for i in items[:8]
                            if i.get_text(strip=True)]
                break

        # ── 배송 정보 (특징에 추가) ───────────────────────
        delivery_info = []
        for sel in ["[class*='rocket']", "[class*='delivery-badge']",
                    "span[class*='badge']"]:
            items = soup.select(sel)
            for it in items[:3]:
                txt = it.get_text(strip=True)
                if txt and len(txt) < 15:
                    delivery_info.append(txt)
        if delivery_info:
            features.extend(list(set(delivery_info)))

        # ── 이미지 ────────────────────────────────────────
        img_url = ""
        for sel in ["img#rep-image", "img[class*='prod-image__detail']",
                    "img[class*='main-image']", ".prod-image__detail img"]:
            el = soup.select_one(sel)
            if el:
                img_url = el.get("src") or el.get("data-src", "")
                if img_url.startswith("//"): img_url = "https:" + img_url
                break

        # 상품명이 없으면 실패 처리
        if not name or name == "쿠팡":
            return {"error": "상품명을 추출하지 못했습니다. 수동으로 입력해주세요."}

        return {
            "name": name[:60],
            "price": price or "가격 정보 없음",
            "rating": rating,
            "review_count": review_count,
            "features": features,
            "image_url": img_url,
            "url": url,
        }

    except Exception as e:
        return {"error": f"추출 실패: {str(e)}"}

# ── 블로그 글 생성 ────────────────────────────────────────────
def generate_post(product, partner_url, api_key, category_hint=""):
    features_text = "\n".join([f"- {f}" for f in product.get("features",[])]) or "- 제품 특징"

    # ── 1단계: 제목/태그/키워드만 JSON으로 ──────────────────
    meta_prompt = f"""상품명: {product['name']}
가격: {product.get('price','')}
{"카테고리: " + category_hint if category_hint else ""}

아래 JSON만 출력 (백틱 없이):
{{"title":"SEO 최적화 제목 60자 이내","meta_description":"검색결과 설명 160자 이내","slug":"영문-소문자-하이픈-슬러그-최대5단어","tags":["태그1","태그2","태그3","태그4","태그5"],"focus_keyword":"핵심키워드"}}"""

    meta_raw = gemini(meta_prompt, api_key)
    meta_raw = re.sub(r"^```json\s*|\s*```$","",meta_raw,flags=re.MULTILINE).strip()
    meta_raw = re.sub(r"^```\s*|\s*```$","",meta_raw,flags=re.MULTILINE).strip()
    meta = json.loads(meta_raw)

    # ── 2단계: 본문 HTML만 별도 생성 ────────────────────────
    content_prompt = f"""한국어 SEO 블로그 리뷰 글을 HTML로 작성하세요.

[상품 정보]
- 상품명: {product['name']}
- 가격: {product.get('price','')}
- 별점/리뷰: {product.get('rating','')} / {product.get('review_count','')}
- 특징: {features_text}
- 파트너스 링크: {partner_url}
{"- 카테고리: " + category_hint if category_hint else ""}
- 핵심 키워드: {meta.get('focus_keyword','')}

[구조 - 반드시 아래 순서대로 완성할 것]
1. 도입부 <p> 2개: 공감 유도
2. 파트너스 링크 1회 삽입
3. <h2>핵심 특징 3가지</h2> (각 <h3> + <p> + <ul>)
4. <h2>장단점</h2> 장점3개 단점1개
5. <h2>가격 및 가성비</h2> 1개 <p>
6. <h2>구매 추천</h2> 파트너스 링크 1회 포함
7. 마지막에 반드시 이 문구 추가:
<p><em>이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.</em></p>

파트너스 링크 형식:
<a href="{partner_url}" target="_blank" rel="noopener">👉 쿠팡에서 최저가 확인하기</a>

[주의사항]
- 전체 1000~1200자 (너무 길지 않게)
- 글이 반드시 </p> 로 완전히 끝나야 함
- HTML 태그만 출력 (설명 없이)"""

    content = gemini(content_prompt, api_key)
    # 혹시 마크다운 코드블록으로 감싸진 경우 제거
    content = re.sub(r"^```html\s*|\s*```$","",content,flags=re.MULTILINE).strip()
    content = re.sub(r"^```\s*|\s*```$","",content,flags=re.MULTILINE).strip()

    return {**meta, "content": content}

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
        "slug":    post_data.get("slug",""),
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
        if st.button("🔍 사용 가능한 모델 확인", use_container_width=True):
            with st.spinner("조회 중..."):
                try:
                    r = requests.get(f"{GEMINI_BASE}?key={gemini_key}", timeout=10)
                    data = r.json()
                    names = [
                        m["name"].replace("models/","")
                        for m in data.get("models",[])
                        if "generateContent" in m.get("supportedGenerationMethods",[])
                    ]
                    if names:
                        st.success(f"✅ {len(names)}개 모델 사용 가능")
                        for n in names: st.code(n)
                    else:
                        st.warning("사용 가능한 모델 없음")
                        st.json(data)
                except Exception as e:
                    st.error(str(e))
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
        coupang_url = st.text_input(
            "🔗 쿠팡 상품 URL 또는 파트너스 링크",
            placeholder="https://www.coupang.com/vp/products/... 또는 https://link.coupang.com/a/...",
            help="쿠팡 상품 URL 또는 파트너스 링크 둘 다 가능합니다")
        st.caption("💡 파트너스 링크(`link.coupang.com`)도 입력 가능 — 자동으로 상품 정보를 찾아드립니다")
    with c2:
        partner_url = st.text_input("🤝 쿠팡 파트너스 링크",
                                    placeholder="https://link.coupang.com/a/...")
        # 상품 URL에 파트너스 링크 넣었으면 자동 복사
        if coupang_url and "link.coupang.com" in coupang_url and not partner_url:
            partner_url = coupang_url
            st.caption("✅ 파트너스 링크 자동 적용됨")

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

        t_col, m_col, s_col = st.columns(3)
        with t_col:
            edited_title = st.text_input("📌 제목", value=post.get("title",""))
        with m_col:
            edited_meta  = st.text_input("🔍 메타 설명", value=post.get("meta_description",""))
        with s_col:
            edited_slug  = st.text_input("🔗 슬러그 (URL)", value=post.get("slug",""),
                                         help="영문 소문자 + 하이픈만 사용")

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
                final  = {**post, "title":edited_title, "meta_description":edited_meta, "slug":edited_slug}
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