import os
import sys
import datetime
import feedparser
import requests
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

# 깃허브 서버 시간을 한국 시간(KST)으로 변환
KST = datetime.timezone(datetime.timedelta(hours=9))
now = datetime.datetime.now(KST)

# 시간대에 따라 타겟팅할 날짜 및 모드 분리
if now.hour < 9: 
    target_date = now - datetime.timedelta(days=1)
    run_mode = "OVERSEAS"
    market_section_title = "🇺🇸 해외 시장 마감 브리핑 (오전 06:00)"
else:
    target_date = now
    run_mode = "DOMESTIC"
    market_section_title = "🇰🇷 국내 시장 마감 브리핑 (오후 16:00)"

days = ["월", "화", "수", "목", "금", "토", "일"]
day_of_week = days[target_date.weekday()]

month_title = f"{target_date.year}년 {target_date.month}월"
daily_title = f"{target_date.month}월 {target_date.day}일 ({day_of_week})"

# ==========================================
# 2. 금융 데이터 수집 함수 (원엔화 100배 가공 포함)
# ==========================================
def get_ticker_data(ticker_symbol, display_name):
    try:
        ticker = yf.Ticker(ticker_symbol)
        hist = ticker.history(period="5d") 
        if len(hist) >= 2:
            prev_close = hist['Close'].iloc[-2]
            current_price = hist['Close'].iloc[-1]
            
            # 💡 [핵심 수정] 원엔화 티커일 경우 1엔 단위이므로 100을 곱해 '100엔당 원화'로 조절합니다.
            if ticker_symbol == "JPYKRW=X":
                prev_close = prev_close * 100
                current_price = current_price * 100
            
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
                
            value_text = f" : {current_price:,.2f} ({sign}{change_percent:.2f}%)"
            return {"name": display_name, "value": value_text, "color": color}
    except Exception as e:
        print(f"⚠️ {display_name} 데이터 수집 실패: {e}")
    return {"name": display_name, "value": " : 데이터 휴장 또는 실패", "color": "default"}

# ==========================================
# 3. 뉴스 데이터 수집 및 정제
# ==========================================
def clean_html_text(html_content):
    if not html_content:
        return "요약 내용 없음"
    soup = BeautifulSoup(html_content, "html.parser")
    text = soup.get_text().strip()
    return text[:150] + "..." if len(text) > 150 else text

def fetch_rss_news(rss_url, limit=4, is_google=False):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.get(rss_url, headers=headers, timeout=10)
        feed = feedparser.parse(response.content)
        
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
# 4. 노션 BLOCKS 생성 헬퍼
# ==========================================
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

# ==========================================
# 5. 노션 트리 구조 제어 및 중복 방지
# ==========================================
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

def get_or_create_daily_toggle(month_toggle_id, daily_name):
    response = notion.blocks.children.list(block_id=month_toggle_id)
    for block in response.get('results', []):
        if block['type'] == 'toggle':
            rich_text = block['toggle']['rich_text']
            if rich_text and rich_text[0]['text']['content'] == daily_name:
                return block['id']
    new_daily = notion.blocks.children.append(
        block_id=month_toggle_id,
        children=[create_toggle_block(daily_name)]
    )
    return new_daily['results'][0]['id']

def delete_existing_section(parent_id, section_title):
    response = notion.blocks.children.list(block_id=parent_id)
    for block in response.get('results', []):
        if block['type'] == 'toggle':
            rich_text = block['toggle']['rich_text']
            if rich_text and rich_text[0]['text']['content'] == section_title:
                notion.blocks.delete(block_id=block['id'])
                break

# ==========================================
# 6. 메인 실행 (이원화 순환 파이프라인)
# ==========================================
def main():
    try:
        month_toggle_id = get_or_create_month_toggle(PAGE_ID, month_title)
        daily_toggle_id = get_or_create_daily_toggle(month_toggle_id, daily_title)
        delete_existing_section(daily_toggle_id, market_section_title)

        # --------------------------------------------------
        # 오후 4시 : 국내 마감 브리핑 세션 가동
        # --------------------------------------------------
        if run_mode == "DOMESTIC":
            print("Base: 국내 장 마감 브리핑 조립 중...")
            kospi = get_ticker_data("^KS11", "코스피")
            google_url = "https://news.google.com/rss/search?q=%EA%B5%AD%EB%82%B4%20%EC%A6%9D%EC%8B%9C&hl=ko&gl=KR&ceid=KR:ko"
            ko_news = fetch_rss_news(google_url, limit=5, is_google=True)
            
            domestic_payload = [
                create_split_bullet_block(kospi),
                create_toggle_block("📰 국내 주요 뉴스 (Google)", [create_news_combined_block(n["title"], n["link"], n["summary"]) for n in ko_news])
            ]
            
            notion.blocks.children.append(
                block_id=daily_toggle_id,
                children=[create_toggle_block(market_section_title, domestic_payload)]
            )
            print("✅ 국내 시장 마감 브리핑 동기화 완료!")

        # --------------------------------------------------
        # 오전 6시 : 해외 마감 브리핑 세션 가동 (원엔화 100배 포함)
        # --------------------------------------------------
        elif run_mode == "OVERSEAS":
            print("Base: 해외 장 마감 브리핑 조립 중...")
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
            
            # 💡 [디스플레이 명 가공] 지표 명칭을 '원엔화(100엔)'로 알기 쉽게 변경합니다.
            jpykrw = get_ticker_data("JPYKRW=X", "원엔화(100엔)")

            cnbc_top = fetch_rss_news("https://search.cnbc.com/rs/search/combinedcms/view.xml?profile=100003114", limit=4)
            cnbc_world = fetch_rss_news("https://search.cnbc.com/rs/search/combinedcms/view.xml?profile=100727362", limit=4)
            cnbc_economy = fetch_rss_news("https://search.cnbc.com/rs/search/combinedcms/view.xml?profile=100004183", limit=4)
            cnbc_finance = fetch_rss_news("https://search.cnbc.com/rs/search/combinedcms/view.xml?profile=100006626", limit=4)

            commodity_children = [create_split_bullet_block(x) for x in [wti, gas, gold, silver, copper, corn, rice]]
            fx_children = [create_split_bullet_block(usdkrw), create_split_bullet_block(jpykrw)]
            
            overseas_news_children = [
                create_toggle_block("🌟 CNBC Top News", [create_news_combined_block(n["title"], n["link"], n["summary"]) for n in cnbc_top]),
                create_toggle_block("🌍 CNBC World News", [create_news_combined_block(n["title"], n["link"], n["summary"]) for n in cnbc_world]),
                create_toggle_block("📊 CNBC Economy", [create_news_combined_block(n["title"], n["link"], n["summary"]) for n in cnbc_economy]),
                create_toggle_block("💰 CNBC Finance", [create_news_combined_block(n["title"], n["link"], n["summary"]) for n in cnbc_finance])
            ]

            overseas_payload = [
                create_split_bullet_block(nasdaq),
                create_toggle_block("📦 상품 (원자재)", commodity_children),
                create_split_bullet_block(dxy),
                create_toggle_block("💱 환율 지표", fx_children),
                create_toggle_block("해외 주요 뉴스 (CNBC)", overseas_news_children)
            ]
            
            notion.blocks.children.append(
                block_id=daily_toggle_id,
                children=[create_toggle_block(market_section_title, overseas_payload)]
            )
            print("✅ 해외 시장 마감 브리핑 동기화 완료!")

    except Exception as e:
        print(f"❌ 노션 API 연동 중 오류 발생: {e}")

if __name__ == "__main__":
    main()
