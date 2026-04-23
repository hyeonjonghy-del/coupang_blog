"""
pages/policy_blog.py
정책·부업 블로그 자동화
- 네이버 뉴스 API로 매일 정보 수집
- Gemini AI로 글감 선별 + 블로그 글 생성
- WordPress 자동 발행 (기존 쿠팡 블로그와 동일 설정 공유)
"""

import streamlit as st
import requests
import json, re, os, time, csv, io
from datetime import datetime

# ─────────────────────────────────────────────────────────────
GEMINI_BASE   = "https://generativelanguage.googleapis.com/v1beta/models"
HISTORY_FILE  = "/tmp/policy_history.json"

CATEGORY_QUERIES = {
    "정부 지원금/보조금": [
        "정부 지원금 2026",
        "보조금 신청방법 2026",
        "정부 지원사업 공고",
    ],
    "창업/소상공인 지원": [
        "소상공인 지원금 2026",
        "창업 지원사업 신청",
        "중소기업 정책자금",
    ],
    "부업/재테크 정보": [
        "부업 추천 2026",
        "재테크 방법 직장인",
        "투자 정보 ETF",
    ],
    "복지 혜택 (육아·노인 등)": [
        "육아 지원금 신청",
        "노인 복지 혜택 2026",
        "복지 혜택 신청방법",
    ],
}


# ─────────────────────────────────────────────────────────────
# 이력 관리
# ─────────────────────────────────────────────────────────────
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_history(entry: dict):
    history = load_history()
    history.insert(0, entry)
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history[:300], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def history_to_csv(history):
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["date", "title", "category", "status", "post_id", "link"],
        extrasaction="ignore",
    )
    writer.writeheader()
    writer.writerows(history)
    return output.getvalue().encode("utf-8-sig")


# ─────────────────────────────────────────────────────────────
# Gemini 호출 (기존 app.py 방식과 동일)
# ─────────────────────────────────────────────────────────────
def gemini_call(prompt: str, api_key: str, max_tokens: int = 8000) -> str:
    preferred = [
        "gemini-2.5-flash-preview-04-17",
        "gemini-2.5-pro-preview-03-25",
        "gemini-1.5-flash",
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
        ordered = [m for m in preferred if m in set(available)]
        models  = ordered if ordered else (available if available else preferred)
    except Exception:
        models = preferred

    last_err = None
    for model in models:
        try:
            r = requests.post(
                f"{GEMINI_BASE}/{model}:generateContent?key={api_key}",
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.7, "maxOutputTokens": max_tokens},
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
    raise RuntimeError(f"모든 Gemini 모델 실패: {last_err}")


def strip_code_fence(text: str) -> str:
    text = re.sub(r"^```[a-z]*\n?|\n?```$", "", text, flags=re.MULTILINE)
    return text.strip()


def extract_json(text: str) -> dict | list:
    """Gemini 응답에서 JSON을 최대한 안전하게 추출"""
    text = strip_code_fence(text)

    # 1차: 그대로 파싱
    try:
        return json.loads(text)
    except Exception:
        pass

    # 2차: { } 블록 추출 후 파싱
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass

    # 3차: 흔한 오류 자동 수정 후 파싱
    #  - 한국어 내 따옴표 처리
    #  - trailing comma 제거
    fixed = text
    fixed = re.sub(r',\s*([}\]])', r'\1', fixed)          # trailing comma
    fixed = re.sub(r'[\x00-\x1f\x7f]', ' ', fixed)        # 제어문자 제거
    try:
        return json.loads(fixed)
    except Exception:
        pass

    # 4차: 최후 수단 — 중괄호 블록 하나씩 시도
    for m in re.finditer(r'\{[^{}]*\}', text, re.DOTALL):
        try:
            return json.loads(m.group())
        except Exception:
            continue

    raise ValueError(f"JSON 추출 실패: {text[:200]}")


# ─────────────────────────────────────────────────────────────
# 네이버 뉴스 API
# ─────────────────────────────────────────────────────────────
def clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def fetch_naver_news(query: str, client_id: str, client_secret: str, display: int = 6):
    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    params = {"query": query, "display": display, "sort": "date"}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        if res.status_code == 200:
            return res.json().get("items", [])
        elif res.status_code == 401:
            st.error("❌ 네이버 API 인증 실패 — Client ID/Secret을 확인하세요.")
    except Exception as e:
        st.warning(f"수집 오류 ({query}): {e}")
    return []


def collect_all_news(client_id: str, client_secret: str, categories: list) -> list:
    all_items, seen = [], set()
    for cat in categories:
        for q in CATEGORY_QUERIES.get(cat, []):
            items = fetch_naver_news(q, client_id, client_secret)
            for item in items:
                title = clean_html(item.get("title", "")).strip()
                if title and title not in seen:
                    seen.add(title)
                    all_items.append({
                        "category":    cat,
                        "title":       title,
                        "description": clean_html(item.get("description", "")),
                        "link":        item.get("originallink") or item.get("link", ""),
                        "pubDate":     item.get("pubDate", "")[:16],
                    })
            time.sleep(0.15)
    return all_items


# ─────────────────────────────────────────────────────────────
# AI 글감 선별
# ─────────────────────────────────────────────────────────────
def ai_filter(items: list, api_key: str, top_n: int) -> list:
    # 항목이 top_n보다 적으면 그냥 반환
    if len(items) <= top_n:
        for i, item in enumerate(items):
            item.setdefault("blog_title", item["title"])
            item.setdefault("reason", "")
            item.setdefault("rank", i + 1)
        return items

    numbered = "\n".join([
        f"{i+1}. [{item['category']}] {item['title']}"
        for i, item in enumerate(items)
    ])

    prompt = f"""다음 뉴스 목록에서 블로그 글감으로 좋은 항목 {top_n}개를 골라주세요.

[선별 기준]
- 일반인이 실제 혜택을 받을 수 있는 정보
- 검색량이 많을 것 같은 키워드
- 카테고리가 다양하게 섞이도록

[목록]
{numbered}

[출력 규칙]
- 반드시 정확히 {top_n}개 선별
- JSON만 출력 (설명, 백틱 절대 금지)
- blog_title은 반드시 영어 없이 한국어로만 작성
- reason은 10자 이내 한국어

출력 형식:
{{"selected":[{{"rank":1,"index":1,"blog_title":"제목","reason":"이유"}},{{"rank":2,"index":2,"blog_title":"제목","reason":"이유"}}]}}"""

    try:
        text   = gemini_call(prompt, api_key, max_tokens=3000)
        result = extract_json(text)
        selected = []
        for sel in result.get("selected", []):
            idx = sel.get("index", 0) - 1
            if 0 <= idx < len(items):
                item = dict(items[idx])
                item["blog_title"] = sel.get("blog_title", item["title"])
                item["reason"]     = sel.get("reason", "")
                item["rank"]       = sel.get("rank", 99)
                selected.append(item)
        selected.sort(key=lambda x: x["rank"])
        # 파싱은 됐지만 항목이 너무 적으면 나머지 보충
        if len(selected) < min(top_n, len(items)):
            existing_titles = {s["title"] for s in selected}
            for item in items:
                if item["title"] not in existing_titles:
                    item.setdefault("blog_title", item["title"])
                    item.setdefault("reason", "")
                    item.setdefault("rank", len(selected) + 1)
                    selected.append(item)
                    if len(selected) >= top_n:
                        break
        return selected[:top_n]
    except Exception as e:
        st.error(f"AI 선별 오류 (기본 목록 사용): {e}")
        for i, item in enumerate(items[:top_n]):
            item.setdefault("blog_title", item["title"])
            item.setdefault("reason", "")
            item.setdefault("rank", i + 1)
        return items[:top_n]


# ─────────────────────────────────────────────────────────────
# 블로그 글 생성 (기존 app.py generate_post 방식과 동일 구조)
# ─────────────────────────────────────────────────────────────
def generate_post(item: dict, api_key: str) -> dict:
    today   = datetime.now().strftime("%Y년 %m월")
    keyword = item.get("blog_title", item["title"])

    # ① 메타 정보 — 필드를 분리해서 요청 (JSON 오류 최소화)
    meta_prompt = f"""다음 블로그 글의 SEO 메타 정보를 JSON으로 출력하세요.

주제: {keyword}
카테고리: {item['category']}
기준: {today}

규칙:
- JSON만 출력 (백틱, 설명 절대 금지)
- 모든 값은 쌍따옴표 사용
- title: 한국어 50자 이내
- meta_description: 한국어 120자 이내  
- slug: 영문 소문자와 하이픈만 (예: government-support-2026)
- tags: 한국어 태그 5개 배열
- focus_keyword: 한국어 핵심키워드 1개

{{"title":"제목","meta_description":"설명","slug":"slug","tags":["태그1","태그2","태그3","태그4","태그5"],"focus_keyword":"키워드"}}"""

    try:
        meta_raw = gemini_call(meta_prompt, api_key, max_tokens=600)
        meta     = extract_json(meta_raw)
        keyword  = meta.get("focus_keyword", keyword)
    except Exception:
        # 메타 생성 실패 시 기본값으로 진행
        meta = {
            "title":            keyword,
            "meta_description": f"{keyword} 신청방법과 혜택을 알아보세요.",
            "slug":             re.sub(r'[^a-z0-9]+', '-', keyword.lower())[:40],
            "tags":             [item["category"], "지원금", "신청방법", "2026", "혜택"],
            "focus_keyword":    keyword,
        }

    # ② 본문 전반부
    part1_prompt = (
        "한국어 블로그 글 전반부를 HTML로 작성하세요.\n"
        f"주제: {keyword}\n"
        f"카테고리: {item['category']}\n"
        f"참고내용: {item['description']}\n"
        f"핵심키워드: {keyword}\n"
        f"기준: {today}\n\n"
        "순서대로 작성:\n"
        "1. <p> 도입부 2개 (독자 공감 + 이 글에서 알 수 있는 것)\n"
        "2. <h2>✅ 지원 대상은 누구?</h2> — 대상 설명\n"
        "3. <h2>💰 지원 금액은 얼마?</h2> — 금액 설명 (불확실하면 '공식 사이트 확인' 표기)\n\n"
        "친근한 말투(~해요, ~거든요), 각 섹션을 충분히 상세하게 작성해서 전반부만 400자 이상 되도록 해주세요. HTML만 출력 (설명 없이)"
    )

    # ③ 본문 후반부
    part2_prompt = (
        "한국어 블로그 글 후반부를 HTML로 작성하세요.\n"
        f"주제: {keyword}\n"
        f"핵심키워드: {keyword}\n"
        f"참고링크: {item['link']}\n\n"
        "순서대로 작성:\n"
        "1. <h2>📋 신청 방법 step by step</h2> — <ol>로 단계별\n"
        "2. <h2>⚠️ 주의사항 & 꿀팁</h2>\n"
        "3. <h2>마무리</h2> — 행동 유도 + 공유 부탁\n\n"
        "친근한 말투(~해요), 각 섹션을 충분히 상세하게 작성해서 후반부만 400자 이상 되도록 해주세요. HTML만 출력 (설명 없이)"
    )

    part1 = strip_code_fence(gemini_call(part1_prompt, api_key))
    part2 = strip_code_fence(gemini_call(part2_prompt, api_key))

    # 출처 링크를 본문 맨 아래에 직접 삽입
    link = item.get("link", "")
    if link:
        source_html = (
            f'\n<p><em>※ 정확한 정보는 '
            f'<a href="{link}" target="_blank" rel="noopener">관련 공식 사이트</a>'
            f'에서 반드시 확인하세요.</em></p>'
        )
    else:
        source_html = '\n<p><em>※ 정확한 정보는 관련 공식 사이트에서 반드시 확인하세요.</em></p>'

    return {**meta, "content": part1 + "\n\n" + part2 + source_html}



# ─────────────────────────────────────────────────────────────
# Unsplash 대표 이미지
# ─────────────────────────────────────────────────────────────
def translate_keyword(keyword: str, api_key: str) -> str:
    """한국어 키워드를 Unsplash 검색용 영어로 번역"""
    try:
        prompt = (
            f"다음 한국어 키워드를 Unsplash 이미지 검색에 적합한 영어 2-3단어로 번역하세요.\n"
            f"키워드: {keyword}\n"
            "영어 단어만 출력 (설명 없이, 예: government support money)"
        )
        result = gemini_call(prompt, api_key, max_tokens=50)
        return re.sub(r"[^a-zA-Z\s]", "", result).strip()[:50]
    except Exception:
        return "government support benefit"


def search_unsplash(keyword_en: str, access_key: str) -> dict | None:
    """Unsplash에서 이미지 검색 후 최적 결과 반환"""
    try:
        r = requests.get(
            "https://api.unsplash.com/search/photos",
            headers={"Authorization": f"Client-ID {access_key}"},
            params={"query": keyword_en, "per_page": 5, "orientation": "landscape"},
            timeout=10,
        )
        if r.status_code == 200:
            results = r.json().get("results", [])
            if results:
                photo = results[0]
                return {
                    "url":       photo["urls"]["regular"],      # 표시용 (1080px)
                    "url_dl":    photo["urls"]["full"],          # 다운로드용
                    "thumb":     photo["urls"]["small"],         # 썸네일
                    "author":    photo["user"]["name"],
                    "author_url": photo["user"]["links"]["html"],
                    "unsplash_url": photo["links"]["html"],
                }
        elif r.status_code == 401:
            st.error("❌ Unsplash Access Key가 올바르지 않습니다.")
    except Exception as e:
        st.warning(f"Unsplash 검색 오류: {e}")
    return None


def upload_image_to_wp(image_url: str, slug: str, wp_url: str, user: str, pw: str) -> int | None:
    """이미지를 WordPress 미디어 라이브러리에 업로드 후 media ID 반환"""
    try:
        img_res = requests.get(image_url, timeout=20)
        img_res.raise_for_status()
        filename = re.sub(r"[^a-z0-9]", "-", slug.lower())[:40] + ".jpg"
        r = requests.post(
            wp_url.rstrip("/") + "/wp-json/wp/v2/media",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": "image/jpeg",
            },
            data=img_res.content,
            auth=(user, pw),
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("id")
    except Exception as e:
        st.warning(f"이미지 업로드 실패 (이미지 없이 발행): {e}")
        return None

# ─────────────────────────────────────────────────────────────
# WordPress (기존 app.py wp_post 방식과 동일)
# ─────────────────────────────────────────────────────────────
def wp_get_categories(wp_url: str, user: str, pw: str) -> dict:
    try:
        r = requests.get(
            wp_url.rstrip("/") + "/wp-json/wp/v2/categories?per_page=50",
            auth=(user, pw), timeout=10,
        )
        return {c["name"]: c["id"] for c in r.json()}
    except Exception:
        return {}


def wp_post(wp_url: str, user: str, pw: str, post_data: dict,
            status: str = "draft", category_id=None, featured_media_id=None) -> dict:
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
    if featured_media_id:
        payload["featured_media"] = featured_media_id

    r = requests.post(
        wp_url.rstrip("/") + "/wp-json/wp/v2/posts",
        json=payload, auth=(user, pw), timeout=30,
    )
    r.raise_for_status()
    return r.json()


# ─────────────────────────────────────────────────────────────
# Streamlit UI
# ─────────────────────────────────────────────────────────────
def main():
    st.set_page_config(
        page_title="정책·부업 블로그 자동화",
        page_icon="📋",
        layout="wide",
    )
    st.markdown(
        "<style>.stButton>button{border-radius:10px;font-weight:bold;}</style>",
        unsafe_allow_html=True,
    )
    st.title("📋 정책·부업 블로그 자동화")
    st.caption("정부지원금 · 소상공인 · 부업 · 복지혜택 → AI 글 생성 → WordPress 자동 발행")

    # ── 사이드바 ─────────────────────────────────────────────
    with st.sidebar:
        st.header("⚙️ 설정")

        # Gemini — 기존 app.py와 동일 secrets 키 공유
        gemini_key = st.secrets.get("GEMINI_API_KEY", "")
        if gemini_key:
            st.success("🔑 Gemini: secrets 적용됨")
        else:
            gemini_key = st.text_input("🔑 Gemini API Key", type="password", placeholder="AIza...")

        st.divider()
        st.subheader("📰 네이버 뉴스 API")
        st.caption("👉 [developers.naver.com](https://developers.naver.com) 에서 무료 발급")
        naver_id     = st.secrets.get("NAVER_CLIENT_ID", "")     or st.text_input("Client ID",     type="password")
        naver_secret = st.secrets.get("NAVER_CLIENT_SECRET", "") or st.text_input("Client Secret", type="password")
        if naver_id and naver_secret:
            st.success("🔑 Naver: 설정됨")

        st.divider()
        st.subheader("🖼️ Unsplash 대표 이미지")
        st.caption("👉 [unsplash.com/developers](https://unsplash.com/developers) 에서 무료 발급")
        unsplash_key = st.secrets.get("UNSPLASH_ACCESS_KEY", "") or st.text_input("Access Key", type="password")
        if unsplash_key:
            st.success("🔑 Unsplash: 설정됨")

        st.divider()
        st.subheader("🌐 WordPress")
        # 기존 app.py와 동일 secrets 키 공유
        wp_url  = st.secrets.get("WP_URL", "")          or st.text_input("블로그 주소", placeholder="https://yourblog.com")
        wp_user = st.secrets.get("WP_USER", "")         or st.text_input("관리자 아이디")
        wp_pass = st.secrets.get("WP_APP_PASSWORD", "") or st.text_input("앱 비밀번호", type="password")

        st.divider()
        if st.button("🔌 WordPress 연결 테스트", use_container_width=True):
            with st.spinner("확인 중..."):
                try:
                    r = requests.get(
                        wp_url.rstrip("/") + "/wp-json/wp/v2/posts?per_page=1",
                        auth=(wp_user, wp_pass), timeout=10,
                    )
                    st.success("✅ 연결 성공!") if r.status_code == 200 else st.error(f"❌ {r.status_code}")
                except Exception as e:
                    st.error(str(e))

        st.divider()
        history_all = load_history()
        st.metric("📊 총 발행 수", f"{len(history_all)}개")
        if history_all:
            st.caption(f"최근: {history_all[0]['title'][:18]}...")
        st.divider()
        st.info("💡 앱 비밀번호:\nWordPress 관리자\n→ 사용자 → 프로필\n→ 애플리케이션 비밀번호\n→ 새로 추가")

    if not gemini_key:
        st.warning("👈 Gemini API Key를 입력해주세요")
        st.stop()

    tab_collect, tab_write, tab_history = st.tabs([
        "🔍 정보 수집", "✍️ 글 생성 & 발행", "📋 발행 이력"
    ])

    # ════════════════════════════════════════
    # TAB 1 — 정보 수집 & AI 선별
    # ════════════════════════════════════════
    with tab_collect:
        st.subheader("① 카테고리 선택 & 뉴스 수집")

        col1, col2, col3 = st.columns([4, 1, 1])
        with col1:
            selected_cats = st.multiselect(
                "수집할 카테고리 (복수 선택 가능)",
                list(CATEGORY_QUERIES.keys()),
                default=list(CATEGORY_QUERIES.keys()),
            )
        with col2:
            top_n = st.number_input("AI 선별 개수", min_value=3, max_value=20, value=10)
        with col3:
            st.write("")
            st.write("")
            collect_btn = st.button("🔍 수집 시작", type="primary", use_container_width=True)

        if collect_btn:
            if not naver_id or not naver_secret:
                st.error("사이드바에 네이버 API Client ID/Secret을 입력하세요.")
                st.stop()
            if not selected_cats:
                st.warning("카테고리를 최소 1개 선택하세요.")
                st.stop()

            prog = st.progress(0, "뉴스 수집 중...")
            with st.spinner("📰 네이버 뉴스 수집 중..."):
                raw = collect_all_news(naver_id, naver_secret, selected_cats)
                st.session_state["p_raw"] = raw
            prog.progress(50, "AI 선별 중...")

            with st.spinner("✨ Gemini AI 글감 선별 중..."):
                filtered = ai_filter(raw, gemini_key, top_n)
                st.session_state["p_filtered"] = filtered
                st.session_state["p_posts"]    = {}   # 새 수집 시 초기화

            prog.progress(100, "완료!")
            time.sleep(0.4)
            prog.empty()
            st.success(f"✅ {len(raw)}개 수집 → AI 선별 {len(filtered)}개")

        # 선별 결과 표시
        if st.session_state.get("p_filtered"):
            st.markdown("---")
            st.subheader(f"② 선별 결과 — {len(st.session_state['p_filtered'])}개")
            st.caption("글 쓸 항목만 체크하세요")

            for i, item in enumerate(st.session_state["p_filtered"]):
                with st.container(border=True):
                    col_chk, col_body, col_dt = st.columns([0.5, 8, 1.5])
                    with col_chk:
                        st.checkbox("", key=f"p_pick_{i}", value=True)
                    with col_body:
                        st.markdown(f"**#{item['rank']} [{item['category']}]** {item['blog_title']}")
                        st.caption(item["description"][:120])
                        st.caption(f"💡 {item.get('reason','')}")
                    with col_dt:
                        st.caption(item.get("pubDate", "")[:10])

            st.write("")
            if st.button("✍️ 선택 항목 → 글 생성하기", type="primary", use_container_width=True):
                chosen = [
                    item for i, item in enumerate(st.session_state["p_filtered"])
                    if st.session_state.get(f"p_pick_{i}", True)
                ]
                st.session_state["p_to_write"] = chosen
                st.session_state["p_posts"]    = {}
                st.success(f"✅ {len(chosen)}개 선택됨 — '글 생성 & 발행' 탭으로 이동하세요!")

    # ════════════════════════════════════════
    # TAB 2 — 글 생성 & 발행
    # ════════════════════════════════════════
    with tab_write:
        items_to_write = st.session_state.get("p_to_write", [])

        if not items_to_write:
            st.info("📌 '정보 수집' 탭에서 항목을 먼저 선택하세요.")
        else:
            st.subheader(f"✍️ {len(items_to_write)}개 글 대기 중")

            if "p_posts" not in st.session_state:
                st.session_state["p_posts"] = {}

            # 전체 일괄 생성
            if st.button("🚀 전체 일괄 생성 (시간 소요)", type="secondary"):
                prog = st.progress(0)
                for i, item in enumerate(items_to_write):
                    if i not in st.session_state["p_posts"]:
                        with st.spinner(f"[{i+1}/{len(items_to_write)}] '{item['blog_title'][:25]}...' 작성 중"):
                            try:
                                post = generate_post(item, gemini_key)
                                st.session_state["p_posts"][i] = post
                            except Exception as e:
                                st.error(f"항목 {i+1} 실패: {e}")
                    prog.progress((i + 1) / len(items_to_write))
                prog.empty()
                st.success("✅ 전체 생성 완료!")
                st.rerun()

            st.divider()

            for i, item in enumerate(items_to_write):
                done  = i in st.session_state["p_posts"]
                label = f"{'✅' if done else '⬜'} {i+1}. [{item['category']}] {item['blog_title']}"

                with st.expander(label, expanded=not done):
                    st.caption(f"원본 기사: {item['title']}")

                    # 개별 생성
                    if not done:
                        if st.button("✍️ 이 글 생성", key=f"p_gen_{i}", type="primary"):
                            with st.spinner("✨ Gemini가 SEO 최적화 글 작성 중... (30~60초)"):
                                try:
                                    post = generate_post(item, gemini_key)
                                    st.session_state["p_posts"][i] = post
                                    st.success("✅ 글 생성 완료!")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"생성 실패: {e}")

                    # 생성된 글 표시
                    if done:
                        post = st.session_state["p_posts"][i]
                        st.success("✅ 생성 완료 — 수정 후 포스팅하세요")

                        # ── 대표 이미지 ──────────────────────────
                        st.markdown("**🖼️ 대표 이미지**")
                        img_key = f"p_img_{i}"
                        if img_key not in st.session_state:
                            st.session_state[img_key] = None

                        img_col1, img_col2 = st.columns([3, 1])
                        with img_col2:
                            search_img_btn = st.button("🔍 이미지 검색", key=f"p_srch_{i}", use_container_width=True)
                            if st.session_state[img_key]:
                                clear_btn = st.button("❌ 이미지 제거", key=f"p_clr_{i}", use_container_width=True)
                                if clear_btn:
                                    st.session_state[img_key] = None
                                    st.rerun()

                        if search_img_btn:
                            if not unsplash_key:
                                st.warning("사이드바에 Unsplash Access Key를 입력하세요.")
                            else:
                                with st.spinner("Unsplash에서 이미지 검색 중..."):
                                    kw_en = translate_keyword(post.get("focus_keyword", item["blog_title"]), gemini_key)
                                    photo = search_unsplash(kw_en, unsplash_key)
                                    if photo:
                                        st.session_state[img_key] = photo
                                        st.rerun()
                                    else:
                                        st.warning("이미지를 찾지 못했습니다. 다시 시도해보세요.")

                        with img_col1:
                            if st.session_state[img_key]:
                                photo = st.session_state[img_key]
                                st.image(photo["thumb"], use_container_width=True)
                                st.caption(
                                    f"📷 Photo by [{photo['author']}]({photo['author_url']}) "
                                    f"on [Unsplash]({photo['unsplash_url']})"
                                )
                            else:
                                st.info("'이미지 검색' 버튼을 눌러 대표 이미지를 자동으로 찾아보세요.")

                        st.divider()
                        # ─────────────────────────────────────────

                        t_col, m_col, s_col = st.columns(3)
                        with t_col:
                            edited_title = st.text_input("📌 제목", value=post.get("title", ""), key=f"p_t_{i}")
                        with m_col:
                            edited_meta  = st.text_input("🔍 메타 설명", value=post.get("meta_description", ""), key=f"p_m_{i}")
                        with s_col:
                            edited_slug  = st.text_input("🔗 슬러그", value=post.get("slug", ""), key=f"p_sl_{i}",
                                                         help="영문 소문자+하이픈")

                        st.caption(
                            f"🏷️ 태그: {', '.join(post.get('tags', []))}  |  "
                            f"🎯 키워드: {post.get('focus_keyword', '')}"
                        )

                        tab1, tab2 = st.tabs(["👁️ 미리보기", "✏️ HTML 수정"])
                        with tab1:
                            st.markdown(post.get("content", ""), unsafe_allow_html=True)
                        with tab2:
                            edited_html = st.text_area(
                                "HTML 본문", value=post.get("content", ""), height=400, key=f"p_c_{i}"
                            )
                            if st.button("💾 수정 반영", key=f"p_save_{i}"):
                                st.session_state["p_posts"][i]["content"]          = edited_html
                                st.session_state["p_posts"][i]["title"]            = edited_title
                                st.session_state["p_posts"][i]["meta_description"] = edited_meta
                                st.session_state["p_posts"][i]["slug"]             = edited_slug
                                st.success("반영됨!")
                                st.rerun()

                        st.divider()
                        pa, pb, pc = st.columns(3)
                        with pa:
                            post_status = st.radio("발행 상태", ["📝 임시저장", "🚀 바로 발행"], key=f"p_st_{i}")
                        with pb:
                            cats = wp_get_categories(wp_url, wp_user, wp_pass) if (wp_url and wp_user and wp_pass) else {}
                            cat_name = st.selectbox("카테고리", ["선택 안 함"] + list(cats.keys()), key=f"p_cat_{i}")
                        with pc:
                            st.write("")
                            st.write("")
                            post_btn = st.button("🚀 워드프레스에 포스팅!", key=f"p_pub_{i}",
                                                 type="primary", use_container_width=True)

                        if post_btn:
                            if not (wp_url and wp_user and wp_pass):
                                st.error("❌ 사이드바에 WordPress 설정을 입력해주세요!")
                            else:
                                status = "draft" if "임시저장" in post_status else "publish"
                                cat_id = cats.get(cat_name) if cat_name != "선택 안 함" else None
                                final  = {
                                    **post,
                                    "title":            edited_title,
                                    "meta_description": edited_meta,
                                    "slug":             edited_slug,
                                }
                                with st.spinner("워드프레스 포스팅 중..."):
                                    try:
                                        # 대표 이미지 업로드
                                        media_id = None
                                        photo_data = st.session_state.get(f"p_img_{i}")
                                        if photo_data and unsplash_key:
                                            with st.spinner("대표 이미지 업로드 중..."):
                                                media_id = upload_image_to_wp(
                                                    photo_data["url_dl"],
                                                    final.get("slug", "blog-image"),
                                                    wp_url, wp_user, wp_pass,
                                                )
                                        result = wp_post(wp_url, wp_user, wp_pass, final, status, cat_id, media_id)
                                        link   = result.get("link", "")
                                        st.success("🎉 포스팅 완료!")
                                        if link:
                                            st.markdown(f"**📎 글 주소:** [{link}]({link})")
                                        save_history({
                                            "date":     datetime.now().strftime("%Y-%m-%d %H:%M"),
                                            "title":    edited_title,
                                            "category": item["category"],
                                            "status":   status,
                                            "post_id":  str(result.get("id", "")),
                                            "link":     link,
                                        })
                                        st.toast("📋 발행 이력에 저장됐습니다!", icon="✅")
                                        st.balloons()
                                    except Exception as e:
                                        st.error(f"포스팅 실패: {e}")
                                        import traceback
                                        with st.expander("상세 오류"):
                                            st.code(traceback.format_exc())

                        with st.expander("📋 HTML 복사 (수동 붙여넣기용)"):
                            st.code(post.get("content", ""), language="html")

    # ════════════════════════════════════════
    # TAB 3 — 발행 이력
    # ════════════════════════════════════════
    with tab_history:
        st.subheader("📋 발행 이력 관리")
        history = load_history()

        if not history:
            st.info("아직 발행된 글이 없습니다. 포스팅하면 자동 기록됩니다.")
        else:
            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("총 발행", f"{len(history)}개")
            with c2:
                today_str = datetime.now().strftime("%Y-%m-%d")
                st.metric("오늘 발행", f"{sum(1 for h in history if h['date'].startswith(today_str))}개")
            with c3:
                csv_data = history_to_csv(history)
                st.download_button(
                    "⬇️ 엑셀로 내보내기", data=csv_data,
                    file_name=f"policy_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv", use_container_width=True,
                )
            st.divider()

            search     = st.text_input("🔍 제목 검색", placeholder="찾을 제목 입력")
            filter_cat = st.selectbox("카테고리 필터", ["전체"] + list(CATEGORY_QUERIES.keys()))

            filtered = [
                h for h in history
                if (not search or search.lower() in h.get("title", "").lower())
                and (filter_cat == "전체" or h.get("category") == filter_cat)
            ]

            for h in filtered:
                with st.container(border=True):
                    col1, col2, col3, col4 = st.columns([4, 2, 1, 1])
                    with col1:
                        st.markdown(f"**{h.get('title', '')}**")
                        if h.get("category"):
                            st.caption(f"🏷️ {h['category']}")
                    with col2:
                        st.caption(f"📅 {h.get('date', '')}")
                    with col3:
                        if h.get("status") == "publish":
                            st.success("발행")
                        else:
                            st.warning("초안")
                    with col4:
                        if h.get("link"):
                            st.link_button("글 보기 →", h["link"], use_container_width=True)


if __name__ == "__main__":
    main()