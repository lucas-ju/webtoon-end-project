# ===================================================================
# 파일: test_email.py (이메일 발송 기능 테스트 스크립트)
# - [수정됨] 수신자 이메일 주소를 확인하는 안전장치 코드의
#   논리 오류를 수정했습니다.
# ===================================================================

import sqlite3
import os
import sys

# 현재 파일의 경로를 기준으로 상위 폴더를 시스템 경로에 추가
# 이렇게 해야 다른 폴더에 있는 crawler.py를 불러올 수 있습니다.
sys.path.append(os.path.dirname(os.path.abspath(os.path.dirname(__file__))))

# crawler.py 파일에서 send_email 함수만 가져옵니다.
from crawler import send_email

DATABASE = 'webtoons.db'

def run_test():
    """
    이메일 발송 기능 자체를 직접 테스트합니다.
    """
    # --- 1. 테스트 설정 ---
    # 테스트에 사용할 웹툰 ID를 현재 연재중인 '전지적 독자 시점'으로 변경
    TEST_TITLE_ID = 747269

    # 테스트 결과를 수신할 본인의 이메일 주소를 입력하세요.
    TEST_RECIPIENT_EMAIL = "lucasj8359@gmail.com"
    # --------------------

    # [수정됨] 초기값("당신의 개인 이메일 주소")과 일치하는지 확인합니다.
    if TEST_RECIPIENT_EMAIL == "당신의 개인 이메일 주소":
        print("❌ [설정 필요] test_email.py 파일의 TEST_RECIPIENT_EMAIL 변수를 실제 이메일 주소로 변경해주세요.")
        return

    print("=== 이메일 발송 기능 테스트 시작 ===")
    
    # DB에서 테스트용 웹툰 제목 가져오기
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT title_text FROM webtoons WHERE title_id = ?", (TEST_TITLE_ID,))
    result = cursor.fetchone()
    conn.close()

    if not result:
        print(f"❌ 오류: DB에서 웹툰 ID {TEST_TITLE_ID}를 찾을 수 없습니다.")
        print("   'python3 crawler.py'를 실행하여 DB에 데이터가 올바르게 수집되었는지 확인해주세요.")
        return
        
    webtoon_title = result[0]
    print(f"테스트 대상 웹툰: '{webtoon_title}' (ID: {TEST_TITLE_ID})")
    print(f"테스트 메일 수신 주소: {TEST_RECIPIENT_EMAIL}")

    # 테스트용 이메일 제목과 본문 직접 만들기
    subject = f"[테스트] 웹툰 완결 알림: '{webtoon_title}'가 완결되었습니다!"
    body = f"""안녕하세요! 웹툰 완결 알리미입니다.

이것은 이메일 발송 기능 테스트를 위해 전송된 메일입니다.
회원님께서 구독하신 웹툰 '{webtoon_title}'가 완결될 경우, 이와 같은 형식으로 메일이 발송됩니다.

감사합니다.
"""

    # crawler.py에 있는 send_email 함수를 직접 호출하여 이메일 발송
    print("\n이메일 발송을 시도합니다...")
    send_email(TEST_RECIPIENT_EMAIL, subject, body)
    
    print("=== 테스트 종료 ===")


if __name__ == '__main__':
    # 이메일 발송을 위한 환경 변수가 설정되었는지 확인
    if not os.getenv('EMAIL_ADDRESS') or not os.getenv('EMAIL_PASSWORD'):
        print("="*60)
        print("❌ [오류] 이메일 발송을 위한 환경 변수가 설정되지 않았습니다.")
        print("   테스트를 진행하려면 터미널에 아래 명령어를 먼저 입력해주세요.")
        print("\n   export EMAIL_ADDRESS='당신의G메일주소'")
        print("   export EMAIL_PASSWORD='당신의16자리앱비밀번호'\n")
        print("="*60)
    else:
        run_test()
