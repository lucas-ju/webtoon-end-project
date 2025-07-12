# =====================================================================================
#  파일: crawler.py (데이터 수집 및 완결 감지기)
#  - [개선됨] 모든 작업 완료 후, 관리자에게 일일 작업 결과 보고서를 이메일로 발송합니다.
# =====================================================================================

# --- 1. 필요한 라이브러리 불러오기 ---
import sqlite3
import os
import smtplib
from email.mime.text import MIMEText
import requests
import time
import random
from datetime import datetime
import traceback

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

def collect_new_webtoons(cursor):
    """요일별 웹툰 API를 호출하여 DB에 새로운 웹툰을 추가합니다."""
    print("=== 신규 웹툰 수집 시작 (API 방식) ===")
    
    weekdays = {'mon': '월', 'tue': '화', 'wed': '수', 'thu': '목', 'fri': '금', 'sat': '토', 'sun': '일', 'daily': '매일+', 'dailyPlus': '매일+'}
    new_webtoons_count = 0

    for day_eng, day_kor in weekdays.items():
        api_url = f"https://comic.naver.com/api/webtoon/titlelist/weekday?week={day_eng}"
        print(f"'{day_kor}' ({day_eng}) 웹툰 목록 가져오는 중...")
        webtoons_for_day = get_webtoons_from_api(api_url)
        
        for webtoon in webtoons_for_day:
            title_id = webtoon.get('titleId')
            title_text = webtoon.get('titleName')
            author = webtoon.get('author')
            
            if title_id and title_text:
                cursor.execute("SELECT 1 FROM webtoons WHERE title_id = ?", (title_id,))
                if cursor.fetchone() is None:
                    cursor.execute("""
                    INSERT INTO webtoons (title_id, title_text, author, weekday, status) 
                    VALUES (?, ?, ?, ?, ?)
                    """, (title_id, title_text, author, day_kor, '연재중'))
                    new_webtoons_count += 1
        
        time.sleep(random.uniform(0.5, 1.5))

    print("=== 신규 웹툰 수집 완료 ===")
    return new_webtoons_count

# --- 4. 완결 감지 및 알림 발송 함수 ---
def detect_completed_webtoons(cursor):
    print("=== 완결 웹툰 감지 시작 (API 방식) ===")
    
    completed_api_url = "https://comic.naver.com/api/webtoon/titlelist/finished"
    completed_webtoons = get_webtoons_from_api(completed_api_url)
    real_completed_ids = {webtoon.get('titleId') for webtoon in completed_webtoons if webtoon.get('titleId')}
            
    cursor.execute("SELECT title_id FROM webtoons WHERE status = '연재중'")
    db_ongoing_ids = {row[0] for row in cursor.fetchall()}
    
    newly_completed_ids = db_ongoing_ids.intersection(real_completed_ids)
    
    completed_details = []
    if newly_completed_ids:
        print(f"새로운 완결 웹툰 {len(newly_completed_ids)}개 발견!")
        for title_id in newly_completed_ids:
            cursor.execute("UPDATE webtoons SET status = '완결' WHERE title_id = ?", (title_id,))
            
            cursor.execute("SELECT title_text FROM webtoons WHERE title_id = ?", (title_id,))
            title_text = cursor.fetchone()[0]
            
            subscribers_count = send_completion_notification(cursor, title_id, title_text)
            completed_details.append(f"- {title_text} (ID:{title_id}) / {subscribers_count}명에게 알림 발송")
            print(f"  - ID {title_id} ('{title_text}') 상태를 '완결'로 업데이트.")
    else:
        print("새롭게 완결된 웹툰이 없습니다.")
        
    print("=== 완결 웹툰 감지 완료 ===")
    return completed_details

def send_email(recipient_email, subject, body):
    sender_email = os.getenv('EMAIL_ADDRESS')
    sender_password = os.getenv('EMAIL_PASSWORD')
    if not sender_email or not sender_password:
        print("오류: 이메일 발송을 위한 환경 변수가 설정되지 않았습니다.")
        return False
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
        return True
    except Exception as e:
        print(f"오류: {recipient_email}에게 이메일 발송 실패 - {e}")
        return False

def send_completion_notification(cursor, title_id, title_text):
    cursor.execute("SELECT email FROM subscriptions WHERE title_id = ?", (title_id,))
    subscribers = [row[0] for row in cursor.fetchall()]
    
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
    return len(subscribers)

# --- [신규] 5. 관리자에게 상태 보고서 발송 함수 ---
def send_admin_report(report_data):
    # 보고서를 받을 관리자 이메일 주소를 환경변수에서 가져옵니다.
    # GitHub Secrets에 ADMIN_EMAIL 로 당신의 개인 이메일을 추가해야 합니다.
    admin_email = os.getenv('ADMIN_EMAIL')
    if not admin_email:
        print("경고: 보고서를 수신할 ADMIN_EMAIL 환경 변수가 설정되지 않았습니다.")
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if report_data['status'] == '성공':
        subject = f"✅ [성공] 웹툰 알리미 일일 보고서 ({now})"
        body = f"""
안녕하세요, 관리자님.
웹툰 알리미 자동화 작업이 성공적으로 완료되었습니다.

- 작업 시간: {now}
- 실행 시간: {report_data['duration']:.2f}초
- 신규 등록 웹툰: {report_data['new_webtoons']}개

[금일 완결 처리된 웹툰]
"""
        if report_data['completed_details']:
            body += "\n".join(report_data['completed_details'])
        else:
            body += "없음"

    else: # 실패 시
        subject = f"❌ [실패] 웹툰 알리미 자동화 작업 오류 보고서 ({now})"
        body = f"""
안녕하세요, 관리자님.
웹툰 알리미 자동화 작업 중 오류가 발생했습니다.

- 작업 시간: {now}
- 오류 내용:
{report_data['error_message']}

GitHub Actions 로그를 확인해주세요.
"""

    send_email(admin_email, subject, body)


# --- 6. 메인 실행 블록 ---
if __name__ == '__main__':
    start_time = time.time()
    report = {'status': '성공'}

    try:
        setup_database()
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()

        report['new_webtoons'] = collect_new_webtoons(cursor)
        report['completed_details'] = detect_completed_webtoons(cursor)
        
        conn.commit()
        conn.close()

    except Exception as e:
        # 어떤 에러든 잡아서 보고서에 기록
        print(f"치명적 오류 발생: {e}")
        report['status'] = '실패'
        report['error_message'] = traceback.format_exc()

    finally:
        # 성공하든 실패하든 항상 보고서 발송
        end_time = time.time()
        report['duration'] = end_time - start_time
        send_admin_report(report)
