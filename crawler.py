# =====================================================================================
#  파일: crawler.py (데이터 수집 및 완결 감지기) - v2.6
#  - [데이터 정확성 강화] 데이터 분류 우선순위 로직을 도입하여, '요일별 목록'에 없는
#    장기 휴재작(예: 화산귀환)이 누락되는 문제를 해결합니다.
#  - [안정성 강화] 페이지네이션 로직을 유지하여 안정적인 데이터 수집 보장
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
import asyncio
import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential

# --- 2. 상수 및 기본 설정 ---
DATABASE = 'webtoons.db'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
}
WEEKDAYS = {'mon': 'mon', 'tue': 'tue', 'wed': 'wed', 'thu': 'thu', 'fri': 'fri', 'sat': 'sat', 'sun': 'sun', 'daily': 'daily', 'dailyPlus': 'daily'}


# --- 3. 데이터베이스 초기 설정 함수 (변경 없음) ---
def setup_database():
    """데이터베이스와 테이블이 없는 경우 초기 설정"""
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


# --- 4. 비동기 API 호출 및 데이터 수집 함수 ---
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def _fetch_from_api(session, url):
    """aiohttp 세션을 사용해 단일 API URL에서 데이터를 비동기적으로 가져옵니다."""
    async with session.get(url, headers=HEADERS) as response:
        response.raise_for_status()
        data = await response.json()
        return data.get('titleList', data.get('list', []))

async def _fetch_paginated_finished_candidates(session):
    """
    완결 API를 페이지네이션하여 '완결 및 장기 휴재 후보군' 데이터를 수집합니다.
    """
    all_candidates = {}
    page = 1
    MAX_PAGES = 20 # 과도한 요청을 방지하기 위한 안전 장치

    print("\n'완결/장기 휴재 후보' 목록 확보를 위해 페이지네이션 수집 시작...")
    while page <= MAX_PAGES:
        try:
            api_url = f"https://comic.naver.com/api/webtoon/titlelist/finished?order=UPDATE&page={page}&pageSize=100"
            webtoons_on_page = await _fetch_from_api(session, api_url)

            if not webtoons_on_page:
                print(f"  -> {page-1} 페이지에서 수집 종료 (데이터 없음).")
                break

            for webtoon in webtoons_on_page:
                if webtoon['titleId'] not in all_candidates:
                    all_candidates[webtoon['titleId']] = webtoon
            
            print(f"  -> {page} 페이지 수집 완료. (현재 후보군: {len(all_candidates)}개)")
            page += 1
            await asyncio.sleep(0.1)

        except Exception as e:
            print(f"  -> {page} 페이지 수집 중 오류 발생: {e}")
            break
            
    if page > MAX_PAGES:
        print(f"  -> 최대 {MAX_PAGES} 페이지까지 수집하여 종료합니다.")

    return all_candidates


async def _fetch_all_naver_data():
    """모든 웹툰 데이터를 수집하고 상태를 결정합니다."""
    print("네이버 웹툰 서버에서 오늘의 최신 데이터를 가져옵니다...")
    async with aiohttp.ClientSession() as session:
        # 1. 연재/휴재 웹툰 API 병렬 호출 (1순위 데이터 소스)
        ongoing_tasks = []
        for api_day in WEEKDAYS.keys():
            api_url = f"https://comic.naver.com/api/webtoon/titlelist/weekday?week={api_day}"
            ongoing_tasks.append(_fetch_from_api(session, api_url))
        
        ongoing_results = await asyncio.gather(*ongoing_tasks, return_exceptions=True)

        # 2. 완결/장기 휴재 후보군 목록을 페이지네이션으로 수집 (2순위 데이터 소스)
        finished_candidates = await _fetch_paginated_finished_candidates(session)

    # 3. [핵심 로직] 우선순위에 따라 데이터 분류
    print("\n--- 데이터 수집 결과 ---")
    naver_ongoing_today = {}
    naver_hiatus_today = {}
    naver_finished_today = {}
    
    # 3-1. (1순위) 요일별 목록 처리
    api_days = list(WEEKDAYS.keys())
    for i, result in enumerate(ongoing_results):
        day_key = api_days[i]
        if isinstance(result, Exception):
            print(f"❌ '{day_key}'요일 데이터 수집 실패: {result}")
            continue
        
        ongoing_count = sum(1 for w in result if not w.get('rest', False))
        rest_count = sum(1 for w in result if w.get('rest', False))
        print(f"✅ '{day_key}'요일: 연재 {ongoing_count}개 / 휴재 {rest_count}개")

        for webtoon in result:
            webtoon['normalized_weekday'] = WEEKDAYS[day_key]
            if webtoon.get('rest', False):
                naver_hiatus_today[webtoon['titleId']] = webtoon
            else:
                naver_ongoing_today[webtoon['titleId']] = webtoon

    # 3-2. (2순위) 완결/장기 휴재 후보 목록 처리
    print(f"✅ 완결/휴재 후보 목록(페이지네이션): {len(finished_candidates)}개")
    for tid, data in finished_candidates.items():
        # 이미 1순위에서 분류되었다면 건너뜀
        if tid in naver_ongoing_today or tid in naver_hiatus_today:
            continue
        
        # 1순위 목록에 없는 웹툰만 휴재 또는 완결로 분류
        if data.get('rest', False):
            naver_hiatus_today[tid] = data # 장기 휴재
        else:
            naver_finished_today[tid] = data # 진짜 완결
    
    print(f"  -> 최종 연재: {len(naver_ongoing_today)}개")
    print(f"  -> 최종 휴재 (단기+장기): {len(naver_hiatus_today)}개")
    print(f"  -> 최종 완결: {len(naver_finished_today)}개")
    
    # 모든 데이터를 병합
    all_naver_webtoons_today = {**naver_finished_today, **naver_hiatus_today, **naver_ongoing_today}
    print("------------------------")
    print(f"오늘자 데이터 수집 완료: 총 {len(all_naver_webtoons_today)}개 고유 웹툰 확인")
    
    return naver_ongoing_today, naver_hiatus_today, naver_finished_today, all_naver_webtoons_today


# --- 5. 데이터베이스 동기화 함수 ---
def _synchronize_database(conn, all_naver_webtoons_today, naver_ongoing_today, naver_hiatus_today, naver_finished_today):
    print("\nDB를 오늘의 최신 상태로 전체 동기화를 시작합니다...")
    cursor = conn.cursor()
    cursor.execute("SELECT title_id FROM webtoons")
    db_existing_ids = {row[0] for row in cursor.fetchall()}
    updates, inserts = [], []

    for title_id, webtoon_data in all_naver_webtoons_today.items():
        status = ''
        # [핵심 로직] 새로운 기준에 따라 상태 결정
        if title_id in naver_ongoing_today:
            status = '연재중'
        elif title_id in naver_hiatus_today:
            status = '휴재'
        elif title_id in naver_finished_today:
            status = '완결'
        else: 
            continue

        weekday_to_save = webtoon_data.get('normalized_weekday', webtoon_data.get('weekday'))
        record = (webtoon_data['titleName'], webtoon_data['author'], weekday_to_save, status, title_id)
        
        if title_id in db_existing_ids: 
            updates.append(record)
        else: 
            inserts.append((title_id, webtoon_data['titleName'], webtoon_data['author'], weekday_to_save, status))

    if updates:
        cursor.executemany("UPDATE webtoons SET title_text = ?, author = ?, weekday = ?, status = ? WHERE title_id = ?", updates)
        print(f"{len(updates)}개 웹툰 정보 업데이트 완료.")
    if inserts:
        cursor.executemany("INSERT INTO webtoons (title_id, title_text, author, weekday, status) VALUES (?, ?, ?, ?, ?)", inserts)
        print(f"{len(inserts)}개 신규 웹툰 DB 추가 완료.")
    conn.commit()
    print("DB 동기화 완료.")
    return len(inserts)


# --- 6. 이메일 발송 함수들 (변경 없음) ---
def send_email(recipient_email, subject, body, smtp_server=None):
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
        if smtp_server is None:
            with smtplib.SMTP('smtp.gmail.com', 587) as server:
                server.starttls()
                server.login(sender_email, sender_password)
                server.sendmail(sender_email, recipient_email, msg.as_string())
        else:
            smtp_server.sendmail(sender_email, recipient_email, msg.as_string())
        return True
    except Exception as e:
        print(f"오류: {recipient_email}에게 이메일 발송 실패 - {e}")
        return False

def send_completion_notifications(cursor, newly_completed_ids, all_naver_webtoons_today):
    if not newly_completed_ids:
        print("\n새롭게 완결된 웹툰이 없습니다.")
        return [], 0
    print(f"\n🔥 새로운 완결 웹툰 {len(newly_completed_ids)}개 발견! 알림 발송을 시작합니다.")
    completed_details, total_notified_users = [], 0
    sender_email = os.getenv('EMAIL_ADDRESS')
    sender_password = os.getenv('EMAIL_PASSWORD')
    if not sender_email or not sender_password:
        print("오류: 이메일 발송을 위한 환경 변수가 설정되지 않았습니다.")
        return [], 0
    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as smtp_server:
            smtp_server.starttls()
            smtp_server.login(sender_email, sender_password)
            for title_id in newly_completed_ids:
                title_text = all_naver_webtoons_today.get(title_id, {}).get('titleName', f'ID {title_id}')
                cursor.execute("SELECT email FROM subscriptions WHERE title_id = ?", (title_id,))
                subscribers = [row[0] for row in cursor.fetchall()]
                print(f"--- '{title_text}'(ID:{title_id}) 완결 알림 발송 대상: {len(subscribers)}명 ---")
                if not subscribers:
                    completed_details.append(f"- '{title_text}' (ID:{title_id}) : 구독자 없음")
                    continue
                subject = f"웹툰 완결 알림: '{title_text}'가 완결되었습니다!"
                body = f"안녕하세요! 웹툰 완결 알리미입니다.\n\n회원님께서 구독하신 웹툰 '{title_text}'가 완결되었습니다.\n지금 바로 정주행을 시작해보세요!\n\n감사합니다."
                for email in subscribers:
                    send_email(email, subject, body, smtp_server)
                total_notified_users += len(subscribers)
                completed_details.append(f"- '{title_text}' (ID:{title_id}) : {len(subscribers)}명에게 알림 발송")
    except Exception as e:
        print(f"❌ 이메일 서버 연결 또는 발송 중 심각한 오류 발생: {e}")
    return completed_details, total_notified_users

def send_admin_report(report_data):
    admin_email = os.getenv('ADMIN_EMAIL')
    if not admin_email:
        print("경고: 보고서를 수신할 ADMIN_EMAIL 환경 변수가 설정되지 않았습니다.")
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if report_data['status'] == '성공':
        subject = f"✅ [성공] 웹툰 알리미 일일 보고서 ({now})"
        body = f"안녕하세요, 관리자님.\n웹툰 알리미 자동화 작업이 성공적으로 완료되었습니다.\n\n- 작업 시간: {now}\n- 실행 시간: {report_data['duration']:.2f}초\n- 신규 DB 등록 웹툰: {report_data.get('new_webtoons', 0)}개\n- 총 알림 발송 인원: {report_data.get('total_notified', 0)}명\n\n[금일 완결 처리 및 알림 발송 내역]\n"
        body += "\n".join(report_data['completed_details']) if report_data['completed_details'] else "없음"
    else:
        subject = f"❌ [실패] 웹툰 알리미 자동화 작업 오류 보고서 ({now})"
        body = f"안녕하세요, 관리자님.\n웹툰 알리미 자동화 작업 중 오류가 발생했습니다.\n\n- 작업 시간: {now}\n- 오류 내용:\n{report_data['error_message']}\n\nGitHub Actions 로그를 확인해주세요."
    send_email(admin_email, subject, body)


# --- 7. 메인 실행 함수 ---
async def run_daily_check(conn):
    cursor = conn.cursor()
    print("=== 일일 웹툰 상태 점검 시작 ===")
    cursor.execute("SELECT title_id, status FROM webtoons")
    db_state_before_sync = {row[0]: row[1] for row in cursor.fetchall()}
    print(f"어제자 DB 상태 기준: 총 {len(db_state_before_sync)}개 웹툰")
    
    naver_ongoing_today, naver_hiatus_today, naver_finished_today, all_naver_webtoons_today = await _fetch_all_naver_data()
    
    # [핵심 로직] 완결 감지 로직 수정
    newly_completed_ids = {
        title_id for title_id, status in db_state_before_sync.items()
        if status in ('연재중', '휴재') 
        and title_id in naver_finished_today
    }
    
    completed_details, total_notified_users = send_completion_notifications(cursor, newly_completed_ids, all_naver_webtoons_today)
    newly_added_to_db = _synchronize_database(conn, all_naver_webtoons_today, naver_ongoing_today, naver_hiatus_today, naver_finished_today)
    print("\n=== 일일 점검 완료 ===")
    return newly_added_to_db, completed_details, total_notified_users


# --- 8. 메인 실행 블록 ---
if __name__ == '__main__':
    start_time = time.time()
    report = {'status': '성공'}
    try:
        setup_database()
        conn = sqlite3.connect(DATABASE)
        new_webtoons, completed_details, total_notified = asyncio.run(run_daily_check(conn))
        report.update({'new_webtoons': new_webtoons, 'completed_details': completed_details, 'total_notified': total_notified})
        conn.close()
    except Exception as e:
        print(f"치명적 오류 발생: {e}")
        report['status'] = '실패'
        report['error_message'] = traceback.format_exc()
    finally:
        report['duration'] = time.time() - start_time
        send_admin_report(report)
