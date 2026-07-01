import os
import sys
import datetime
import feedparser
from bs4 import BeautifulSoup
from notion_client import Client
import yfinance as yf

# ==========================================
# 1. 환경 변수 및 초기 세팅
# ==========================================
NOTION_TOKEN = os.environ.get('NOTION_TOKEN', '').strip()
PAGE_ID = os.environ.get('NOTION_PAGE_ID', '').strip()

if not NOTION_TOKEN or not PAGE_ID:
    print("❌ 환경 변수(NOTION_TOKEN 또는 NOTION_PAGE_ID)가 설정되지 않았습니다.")
    sys.exit(1)

notion = Client(auth=NOTION_TOKEN)

# 💡 [핵심 반영 1] 깃허브 서버 시간을 무조건 한국 시간(KST)으로 강제 변환
KST = datetime.timezone(datetime.timedelta(hours=9))
now = datetime.datetime.now(KST)

# 한국 시간(KST) 기준으로 오전/오후 판별
if now.hour < 9: 
    target_date = now - datetime.timedelta(days=1)
    print(f"🌅 오전 업데이트 모드: {target_date.strftime('%Y-%m-%d')} 리포트를 최신 해외 데이터로 업데이트합니다.")
else:
    target_date = now
    print(f"🌇 오후 최초 생성 모드: {target_date.strftime('%Y-%m-%d')} 리포트를 최초 생성합니다.")

days = ["월", "화", "수", "목", "금", "토", "일"]
day_of_week = days[target_date.weekday()]

month_title = f"{target_date.year}년 {target_date.month}월"
daily_title = f"{target_date.month}월 {target_date.day}일 ({day_of_week})"

# ==========================================
# 2. 금융 데이터 수집
# ==========================================
def get_ticker_data(ticker_symbol, display_name):
    try:
        ticker = yf.Ticker(ticker_symbol)
        hist = ticker.history(period="5d") 
        if len(hist) >= 2:
            prev_close = hist['Close'].iloc[-2]
            current_price = hist['Close'].iloc[-1]
            
            change = current_price - prev_close
            change_percent = (change / prev_close) * 100
            
            if change > 0:
                color = "red"
                sign = "+"
            elif change < 0:
                color = "blue"
                sign = ""
            else:
                color = "default"
                sign = ""
                
            # 💡 [핵심 반영 2] 볼드체 분리를 위해 name, value, color를 딕셔너리로 쪼개서 반환
            value_text = f" : {current_price:,.2f} ({sign}{change_percent:.2f}%)"
            return {"name": display_name, "value": value_text, "color": color}
    except Exception as e:
        print(f"⚠️ {display_name} 데이터 수집 실패: {e}")
    return {"name": display_name, "value": " : 데이터 휴장 또는 실패", "color": "default"}

# ==========================================
# 3. 뉴스 데이터 수집
# ==========================================
def clean_html_text(html_content):
    if not html_content:
        return "요약 내용 없음"
    soup = BeautifulSoup(html_content, "html.parser")
    text = soup.get_text().strip()
    return text[:150] + "..." if len(text) > 150 else text

def fetch_rss_news(rss_url, limit=4, is_google=False):
    try:
        feed = feedparser.parse(rss_url)
        news_items = []
        for entry in feed.entries[:limit]:
            title = entry.title
            link = entry.link
            if is_google and " - " in title:
                title = title.rsplit(" - ", 1)[0]
                
            summary_raw = entry.get('summary', entry.get('description', '요약 내용 없음'))
            summary = clean_html_text(summary_raw)
            news_items.append({"title": title, "link": link, "summary": summary})
        return news_items
    except Exception as e:
        print(f"⚠️ 뉴스 수집 실패 ({rss_url}): {e}")
        return []

# ==========================================
# 4. 노션 블록 생성기 (깊이 제한 우회 및 스타일링)
# ==========================================
# 💡 [핵심 반영 3] 지표명은 Bold, 수치는 등락 색상으로 구분하는 생성기
def create_split_bullet_block(data_dict):
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": [
                {"type": "text", "text": {"content": data_dict["name"]}, "annotations": {"bold": True, "color": "default"}},
                {"type": "text", "text": {"content": data_dict["value"]}, "annotations": {"bold": False, "color": data_dict["color"]}}
            ]
        }
    }

# 💡 [핵심 반영 4] 노션 중첩 에러를 막기 위해 제목과 요약을 한 블록으로 합침
def create_news_combined_block(title, url, summary_text):
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": [
                {"type": "text", "text": {"content": f"{title}\n", "link": {"url": url}}, "annotations": {"bold": True}},
                {"type": "text", "text": {"content": f"- 요약: {summary_text}"}, "annotations": {"color": "gray"}}
            ]
        }
    }

def create_toggle_block(title_text, children_blocks=None):
    block = {
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": [{"type": "text", "text": {"content": title_text}, "annotations": {"bold": True}}]
        }
    }
    if children_blocks:
        block["toggle"]["children"] = children_blocks
    return block

def get_or_create_month_toggle(page_id, month_name):
    response = notion.blocks.children.list(block_id=page_id)
    for block in response.get('results', []):
        if block['type'] == 'toggle':
            rich_text = block['toggle']['rich_text']
            if rich_text and rich_text[0]['text']['content'] == month_name:
                return block['id']
    new_toggle = notion.blocks.children.append(
        block_id=page_id,
        children=[create_toggle_block(month_name)]
    )
    return new_toggle['results'][0]['id']

def delete_existing_daily_toggle(month_toggle_id, daily_name):
    response = notion.blocks.children.list(block_id=month_toggle_id)
    for block in response.get('results', []):
        if block['type'] == 'toggle':
            rich_text = block['toggle']['rich_text']
            if rich_text and rich_text[0]['text']['content'] == daily_name:
                print(f"♻️ 기존 '{daily_name}' 블록 데이터 동기화를 위해 초기화 후 재생성합니다.")
                notion.blocks.delete(block_id=block['id'])
                break

# ==========================================
# 5. 메인 실행 (3단계 분할 전송 로직)
# ==========================================
def main():
    print("⏳ 데이터 수집 중...")
    kospi = get_ticker_data("^KS11", "코스피")
    nasdaq = get_ticker_data("^IXIC", "나스닥")
    wti = get_ticker_data("CL=F", "WTI")
    gas = get_ticker_data("NG=F", "천연가스")
    gold = get_ticker_data("GC=F", "금")
    silver = get_ticker_data("SI=F", "은")
    copper = get_ticker_data("HG=F", "동")
    corn = get_ticker_data("ZC=F", "옥수수")
    rice = get_ticker_data("ZR=F", "쌀")
    dxy = get_ticker_data("DX-Y.NYB", "달러 인덱스")
    usdkrw = get_ticker_data("KRW=X", "원달러")
    jpykrw = get_ticker_data("JPYKRW=X", "원엔화")

    google_url = "https://news.google.com/rss/search?q=%EA%B5%AD%EB%82%B4%20%EC%A6%9D%EC%8B%9C&hl=ko&gl=KR&ceid=KR:ko"
    ko_news = fetch_rss_news(google_url, limit=5, is_google=True)
    cnbc_top = fetch_rss_news("https://search.cnbc.com/rs/search/combinedcms/view.xml?profile=100003114", limit=4)
    cnbc_world = fetch_rss_news("https://search.cnbc.com/rs/search/combinedcms/view.xml?profile=100727362", limit=4)
    cnbc_economy = fetch_rss_news("https://search.cnbc.com/rs/search/combinedcms/view.xml?profile=100004183", limit=4)
    cnbc_finance = fetch_rss_news("https://search.cnbc.com/rs/search/combinedcms/view.xml?profile=100006626", limit=4)

    commodity_children = [create_split_bullet_block(x) for x in [wti, gas, gold, silver, copper, corn, rice]]
    fx_children = [create_split_bullet_block(usdkrw), create_split_bullet_block(jpykrw)]
    
    news_children = [
        create_toggle_block("🇰🇷 국내 증시 (Google News)", [create_news_combined_block(n["title"], n["link"], n["summary"]) for n in ko_news]),
        create_toggle_block("🌟 CNBC Top News", [create_news_combined_block(n["title"], n["link"], n["summary"]) for n in cnbc_top]),
        create_toggle_block("🌍 CNBC World News", [create_news_combined_block(n["title"], n["link"], n["summary"]) for n in cnbc_world]),
        create_toggle_block("📊 CNBC Economy", [create_news_combined_block(n["title"], n["link"], n["summary"]) for n in cnbc_economy]),
        create_toggle_block("💰 CNBC Finance", [create_news_combined_block(n["title"], n["link"], n["summary"]) for n in cnbc_finance])
    ]

    try:
        month_toggle_id = get_or_create_month_toggle(PAGE_ID, month_title)
        delete_existing_daily_toggle(month_toggle_id, daily_title)
        
        # [Step 1] 일간 토글(껍데기) 생성
        daily_res = notion.blocks.children.append(
            block_id=month_toggle_id,
            children=[create_toggle_block(daily_title)]
        )
        daily_toggle_id = daily_res['results'][0]['id']

        # [Step 2] 지수, 상품, 달러인덱스, 환율, 그리고 '주요 뉴스' 껍데기를 일간 토글에 주입
        daily_items = [
            create_split_bullet_block(kospi),
            create_split_bullet_block(nasdaq),
            create_toggle_block("📦 상품", commodity_children),
            create_split_bullet_block(dxy),
            create_toggle_block("💱 환율", fx_children),
            create_toggle_block("✅ 주요 뉴스") # 자식 없는 빈 토글 생성
        ]
        basic_res = notion.blocks.children.append(block_id=daily_toggle_id, children=daily_items)
        
        # 방금 생성한 '✅ 주요 뉴스' 토글의 ID 추출 (배열의 가장 마지막 요소)
        news_toggle_id = basic_res['results'][-1]['id']

        # [Step 3] 뉴스 카테고리와 기사들을 '주요 뉴스' 토글 안으로 별도 주입
        notion.blocks.children.append(block_id=news_toggle_id, children=news_children)
        
        print("✅ 노션 데일리 매크로 노트 업데이트 완벽 성공!")
    except Exception as e:
        print(f"❌ 노션 API 연동 중 오류 발생: {e}")

if __name__ == "__main__":
    main()
