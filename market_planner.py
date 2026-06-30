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

now = datetime.datetime.now()

# 오전 실행 시 '어제 장' 마감 데이터로 간주
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
                
            content = f"{display_name} : {current_price:,.2f} ({sign}{change_percent:.2f}%)"
            return {"content": content, "color": color}
    except Exception as e:
        print(f"⚠️ {display_name} 데이터 수집 실패: {e}")
    return {"content": f"{display_name} : 데이터 휴장 또는 실패", "color": "default"}

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
# 4. 노션 블록 생성기
# ==========================================
def create_bullet_block(text, color="default", children=None):
    block = {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": [{"type": "text", "text": {"content": text}, "annotations": {"color": color}}]
        }
    }
    if children:
        block["bulleted_list_item"]["children"] = children
    return block

def create_news_link_block(title, url, summary_text):
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": [{
                "type": "text",
                "text": {"content": title, "link": {"url": url}},
                "annotations": {"color": "default"}
            }],
            "children": [{
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": summary_text}, "annotations": {"color": "gray"}}]
                }
            }]
        }
    }

def create_toggle_block(title_text, children_blocks):
    return {
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": [{"type": "text", "text": {"content": title_text}, "annotations": {"bold": True}}],
            "children": children_blocks if children_blocks else [create_bullet_block("오늘 등록된 뉴스가 없습니다.")]
        }
    }

def get_or_create_month_toggle(page_id, month_name):
    response = notion.blocks.children.list(block_id=page_id)
    for block in response.get('results', []):
        if block['type'] == 'toggle':
            rich_text = block['toggle']['rich_text']
            if rich_text and rich_text[0]['text']['content'] == month_name:
                return block['id']
    new_toggle = notion.blocks.children.append(
        block_id=page_id,
        children=[{"object": "block", "type": "toggle", "toggle": {"rich_text": [{"type": "text", "text": {"content": month_name}, "annotations": {"bold": True}}]}}]
    )
    return new_toggle['results'][0]['id']

def delete_existing_daily_toggle(month_toggle_id, daily_name):
    response = notion.blocks.children.list(block_id=month_toggle_id)
    for block in response.get('results', []):
        if block['type'] == 'toggle':
            rich_text = block['toggle']['rich_text']
            if rich_text and rich_text[0]['text']['content'] == daily_name:
                print(f"♻️ 기존 '{daily_name}' 블록 덮어쓰기를 위해 정리 중...")
                notion.blocks.delete(block_id=block['id'])
                break

# ==========================================
# 5. 메인 실행 (깊이 제한 우회 3단계 로직 적용)
# ==========================================
def main():
    kospi = get_ticker_data("^KS11", "**코스피**")
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

    commodity_children = [
        create_bullet_block(wti["content"], wti["color"]),
        create_bullet_block(gas["content"], gas["color"]),
        create_bullet_block(gold["content"], gold["color"]),
        create_bullet_block(silver["content"], silver["color"]),
        create_bullet_block(copper["content"], copper["color"]),
        create_bullet_block(corn["content"], corn["color"]),
        create_bullet_block(rice["content"], rice["color"])
    ]
    fx_children = [
        create_bullet_block(usdkrw["content"], usdkrw["color"]),
        create_bullet_block(jpykrw["content"], jpykrw["color"])
    ]
    
    # 뉴스 카테고리별 블록 
    news_children = [
        create_toggle_block("🇰🇷 국내 증시 (Google News)", [create_news_link_block(n["title"], n["link"], n["summary"]) for n in ko_news]),
        create_toggle_block("🌟 CNBC Top News", [create_news_link_block(n["title"], n["link"], n["summary"]) for n in cnbc_top]),
        create_toggle_block("🌍 CNBC World News", [create_news_link_block(n["title"], n["link"], n["summary"]) for n in cnbc_world]),
        create_toggle_block("📊 CNBC Economy", [create_news_link_block(n["title"], n["link"], n["summary"]) for n in cnbc_economy]),
        create_toggle_block("💰 CNBC Finance", [create_news_link_block(n["title"], n["link"], n["summary"]) for n in cnbc_finance])
    ]

    try:
        month_toggle_id = get_or_create_month_toggle(PAGE_ID, month_title)
        delete_existing_daily_toggle(month_toggle_id, daily_title)
        
        # [Step 1] 일간 토글(껍데기) 생성
        daily_response = notion.blocks.children.append(
            block_id=month_toggle_id,
            children=[{
                "object": "block",
                "type": "toggle",
                "toggle": {
                    "rich_text": [{"type": "text", "text": {"content": daily_title}, "annotations": {"bold": True}}]
                }
            }]
        )
        daily_toggle_id = daily_response['results'][0]['id']

        # [Step 2] 지수, 상품, 환율 및 '주요 뉴스' 껍데기 추가
        daily_basic_payload = [
            create_bullet_block(kospi["content"], kospi["color"]),
            create_bullet_block(nasdaq["content"], nasdaq["color"]),
            create_toggle_block("📦 상품", commodity_children),
            create_bullet_block(dxy["content"], dxy["color"]),
            create_toggle_block("💱 환율", fx_children),
            {
                "object": "block",
                "type": "toggle",
                "toggle": {
                    "rich_text": [{"type": "text", "text": {"content": "✅ 주요 뉴스"}, "annotations": {"bold": True}}]
                }
            }
        ]
        content_response = notion.blocks.children.append(
            block_id=daily_toggle_id,
            children=daily_basic_payload
        )
        # 방금 넣은 배열의 맨 마지막 요소가 '주요 뉴스' 토글의 ID입니다.
        news_toggle_id = content_response['results'][-1]['id']

        # [Step 3] 에러가 났던 깊은 계층의 뉴스를 해당 '주요 뉴스' 토글 안에 안전하게 주입
        notion.blocks.children.append(
            block_id=news_toggle_id,
            children=news_children
        )
        
        print("✅ 노션 데일리 매크로 노트 업데이트 완벽 성공!")
    except Exception as e:
        print(f"❌ 노션 API 연동 중 오류 발생: {e}")

if __name__ == "__main__":
    main()
