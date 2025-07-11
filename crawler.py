# =====================================================================================
#  파일: crawler.py (데이터 수집 및 완결 감지기)
#  - [최종 수정] 'dailyPlus' API를 추가로 호출하여, 누락되었던 '매일+' 웹툰을 모두 수집합니다.
# =====================================================================================

# --- 1. 필요한 라이브러리 불러오기 ---
import sqlite3
import os
import smtplib
from email.mime.text import MIMEText
import requests
import time
import random

DATABASE = 'webtoons.db'

# --- 2. 데이터베이스 초기 설정 함수 (변경 없음) ---
def setup_database():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS webtoons (
        title_id INTEGER PRIMARY KEY,
        title_text TEXT NOT NULL,
        author TEXT,
        weekday TEXT,
        status TEXT NOT NULL
    )""")
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL,
        title_id INTEGER NOT NULL,
        UNIQUE(email, title_id)
    )""")
    
    conn.commit()
    conn.close()

# --- 3. API 호출 및 데이터 수집 함수 ---
def get_webtoons_from_api(api_url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
    }
    try:
        response = requests.get(api_url, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data.get('titleList', data.get('list', []))
    except requests.exceptions.RequestException as e:
        print(f"❌ API 호출 중 오류 발생: {e}")
        return []

def collect_new_webtoons():
    """요일별 웹툰 API를 호출하여 DB에 새로운 웹툰을 추가합니다."""
    print("=== 신규 웹툰 수집 시작 (API 방식) ===")
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    # [수정됨] 'dailyPlus' 키를 추가하여 모든 '매일+' 웹툰을 수집합니다.
    weekdays = {'mon': '월', 'tue': '화', 'wed': '수', 'thu': '목', 'fri': '금', 'sat': '토', 'sun': '일', 'daily': '매일+', 'dailyPlus': '매일+'}
    all_webtoons_count = 0

    for day_eng, day_kor in weekdays.items():
        api_url = f"https://comic.naver.com/api/webtoon/titlelist/weekday?week={day_eng}"
        print(f"'{day_kor}' ({day_eng}) 웹툰 목록 가져오는 중...")
        webtoons_for_day = get_webtoons_from_api(api_url)
        all_webtoons_count += len(webtoons_for_day)
        
        for webtoon in webtoons_for_day:
            title_id = webtoon.get('titleId')
            title_text = webtoon.get('titleName')
            author = webtoon.get('author')
            
            if title_id and title_text:
                # 'daily'와 'dailyPlus' 모두 '매일+'이라는 한글 요일로 저장합니다.
                cursor.execute("""
                INSERT OR IGNORE INTO webtoons (title_id, title_text, author, weekday, status) 
                VALUES (?, ?, ?, ?, ?)
                """, (title_id, title_text, author, day_kor, '연재중'))
        
        time.sleep(random.uniform(0.5, 1.5))

    print(f"총 {all_webtoons_count}개의 웹툰 아이템을 확인했습니다.")
    conn.commit()
    conn.close()
    print("=== 신규 웹툰 수집 완료 ===")

# --- 4. 완결 감지 및 알림 발송 함수 (변경 없음) ---
def detect_completed_webtoons():
    print("=== 완결 웹툰 감지 시작 (API 방식) ===")
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    completed_api_url = "https://comic.naver.com/api/webtoon/titlelist/finished"
    completed_webtoons = get_webtoons_from_api(completed_api_url)
    real_completed_ids = {webtoon.get('titleId') for webtoon in completed_webtoons if webtoon.get('titleId')}
            
    cursor.execute("SELECT title_id FROM webtoons WHERE status = '연재중'")
    db_ongoing_ids = {row[0] for row in cursor.fetchall()}
    
    newly_completed_ids = db_ongoing_ids.intersection(real_completed_ids)
    
    if newly_completed_ids:
        print(f"새로운 완결 웹툰 {len(newly_completed_ids)}개 발견!")
        for title_id in newly_completed_ids:
            cursor.execute("UPDATE webtoons SET status = '완결' WHERE title_id = ?", (title_id,))
            print(f"  - ID {title_id} 상태를 '완결'로 업데이트.")
            send_completion_notification(cursor, title_id)
    else:
        print("새롭게 완결된 웹툰이 없습니다.")
    conn.commit()
    conn.close()
    print("=== 완결 웹툰 감지 완료 ===")

def send_email(recipient_email, subject, body):
    sender_email = os.getenv('EMAIL_ADDRESS')
    sender_password = os.getenv('EMAIL_PASSWORD')
    if not sender_email or not sender_password:
        print("오류: 이메일 발송을 위한 환경 변수가 설정되지 않았습니다.")
        return
    msg = MIMEText(body, _charset='utf-8')
    msg['Subject'] = subject
    msg['From'] = sender_email
    msg['To'] = recipient_email
    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as smtp_server:
            smtp_server.starttls()
            smtp_server.login(sender_email, sender_password)
            smtp_server.sendmail(sender_email, recipient_email, msg.as_string())
        print(f"성공: {recipient_email}에게 이메일 발송 완료.")
    except Exception as e:
        print(f"오류: {recipient_email}에게 이메일 발송 실패 - {e}")

def send_completion_notification(cursor, title_id):
    cursor.execute("SELECT email FROM subscriptions WHERE title_id = ?", (title_id,))
    subscribers = [row[0] for row in cursor.fetchall()]
    cursor.execute("SELECT title_text FROM webtoons WHERE title_id = ?", (title_id,))
    title_text = cursor.fetchone()[0]
    print(f"--- '{title_text}'(ID:{title_id}) 완결 알림 발송 대상 ---")
    if subscribers:
        subject = f"웹툰 완결 알림: '{title_text}'가 완결되었습니다!"
        body = f"""안녕하세요! 웹툰 완결 알리미입니다.

회원님께서 구독하신 웹툰 '{title_text}'가 완결되었습니다.
지금 바로 정주행을 시작해보세요!

감사합니다.
"""
        for email in subscribers:
            send_email(email, subject, body)
    else:
        print("  -> 구독자가 없습니다.")
    print("-------------------------------------------------")

# --- 5. 메인 실행 블록 ---
if __name__ == '__main__':
    setup_database()
    collect_new_webtoons()
    detect_completed_webtoons()
