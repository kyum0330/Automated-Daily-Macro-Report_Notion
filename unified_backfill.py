import os
import sys
import datetime
import feedparser
import requests
import urllib.parse
from bs4 import BeautifulSoup
from notion_client import Client
import yfinance as yf

NOTION_TOKEN = os.environ.get('NOTION_TOKEN', '').strip()
PAGE_ID = os.environ.get('NOTION_PAGE_ID', '').strip()

if not NOTION_TOKEN or not PAGE_ID:
    print("❌ 환경 변수 오류")
    sys.exit(1)

notion = Client(auth=NOTION_TOKEN)

# 복구할 대상 날짜 목록 (26일, 27일)
TARGET_DATES = [
    datetime.date(2026, 8, 26),
    datetime.date(2026, 8, 27)
]
days = ["월", "화", "수", "목", "금", "토", "일"]

def get_historical_data(ticker_symbol, display_name, target_date):
    try:
        target_str = target_date.strftime('%Y-%m-%d')
        if ticker_symbol == "JPYKRW=X":
            krw = yf.Ticker("KRW=X").history(period="1mo").dropna()
            jpy = yf.Ticker("JPY=X").history(period="1mo").dropna()
            krw_filtered = krw.loc[:target_str]
            jpy_filtered = jpy.loc[:target_str]
            
            if len(krw_filtered) >= 2 and len(jpy_filtered) >= 2:
                prev_close = (krw_filtered['Close'].iloc[-2] / jpy_filtered['Close'].iloc[-2]) * 100
                current_price = (krw_filtered['Close'].iloc[-1] / jpy_filtered['Close'].iloc[-1]) * 100
            else:
                raise ValueError("교차 환율 데이터 부족")
        else:
            ticker = yf.Ticker(ticker_symbol)
            hist = ticker.history(period="1mo").dropna(subset=['Close'])
            hist_filtered = hist.loc[:target_str]
            
            if len(hist_filtered) >= 2:
                prev_close = hist_filtered['Close'].iloc[-2]
                current_price = hist_filtered['Close'].iloc[-1]
            else:
                raise ValueError("데이터 부족")
        
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
        print(f"⚠️ {display_name} 데이터 수집 실패 ({target_str}): {e}")
    return {"name": display_name, "value": " : 데이터 휴장 또는 실패", "color": "default"}

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

def main():
    try:
        for t_date in TARGET_DATES:
            month_title = f"{t_date.year}년 {t_date.month}월"
            day_of_week = days[t_date.weekday()]
            daily_title = f"{t_date.month}월 {t_date.day}일 ({day_of_week})"
            
            print(f"⏳ {daily_title} 복구 작업 시작...")
            
            month_toggle_id = get_or_create_month_toggle(PAGE_ID, month_title)
            daily_toggle_id = get_or_create_daily_toggle(month_toggle_id, daily_title)
            
            # 국내외 시장 마감 섹션 껍데기 생성
            ko_section_res = notion.blocks.children.append(block_id=daily_toggle_id, children=[create_toggle_block("🇰🇷 국내 시장 마감 브리핑 (오후 16:00)")])
            ko_section_id = ko_section_res['results'][0]['id']
            
            us_section_res = notion.blocks.children.append(block_id=daily_toggle_id, children=[create_toggle_block("🇺🇸 해외 시장 마감 브리핑 (오전 06:00)")])
            us_section_id = us_section_res['results'][0]['id']
            
            # 과거 지표 수집
            kospi = get_historical_data("^KS11", "코스피", t_date)
            nasdaq = get_historical_data("^IXIC", "나스닥", t_date)
            wti = get_historical_data("CL=F", "WTI", t_date)
            gas = get_historical_data("NG=F", "천연가스", t_date)
            gold = get_historical_data("GC=F", "금", t_date)
            silver = get_historical_data("SI=F", "은", t_date)
            copper = get_historical_data("HG=F", "동", t_date)
            corn = get_historical_data("ZC=F", "옥수수", t_date)
            rice = get_historical_data("ZR=F", "쌀", t_date)
            dxy = get_historical_data("DX-Y.NYB", "달러 인덱스", t_date)
            usdkrw = get_historical_data("KRW=X", "원달러", t_date)
            jpykrw = get_historical_data("JPYKRW=X", "원엔화(100엔)", t_date)
            
            commodity_children = [create_split_bullet_block(x) for x in [wti, gas, gold, silver, copper, corn, rice]]
            fx_children = [create_split_bullet_block(usdkrw), create_split_bullet_block(jpykrw)]
            
            # 블록 조립 (과거 뉴스는 RSS 특성상 실시간만 불러와지므로 지표 복구에 집중합니다)
            notion.blocks.children.append(
                block_id=ko_section_id,
                children=[
                    create_split_bullet_block(kospi),
                    create_toggle_block("📰 국내 주요 뉴스 (생략)")
                ]
            )
            
            notion.blocks.children.append(
                block_id=us_section_id,
                children=[
                    create_split_bullet_block(nasdaq),
                    create_toggle_block("📦 상품 (원자재)", commodity_children),
                    create_split_bullet_block(dxy),
                    create_toggle_block("💱 환율 지표", fx_children),
                    create_toggle_block("해외 주요 뉴스 (생략)")
                ]
            )
            print(f"✅ {daily_title} 복구 완료!")
            
    except Exception as e:
        print(f"❌ 복구 중 오류 발생: {e}")

if __name__ == "__main__":
    main()
