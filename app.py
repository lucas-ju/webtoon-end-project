# =====================================================================================
#  파일: app.py (웹 서버 및 API)
#  - [개선됨] /api/subscribe 엔드포인트에 서버 사이드 이메일 유효성 검사를 추가했습니다.
# =====================================================================================

# --- 1. 필요한 라이브러리 불러오기 ---
import sqlite3
import re # 정규표현식을 위한 're' 라이브러리 추가
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

# --- 2. Flask 애플리케이션 초기 설정 ---
app = Flask(__name__)
CORS(app)

DATABASE = 'webtoons.db'

# --- 3. 데이터베이스 연결 함수 ---
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# --- [신규] 이메일 유효성 검사 함수 ---
def is_valid_email(email):
    """정규표현식을 사용하여 기본적인 이메일 형식을 검사합니다."""
    # 매우 복잡한 정규식도 있지만, 대부분의 경우 아래 정도로 충분합니다.
    # a@b.c 형태를 만족하는지 확인합니다.
    if re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        return True
    return False

# --- 4. API 엔드포인트(URL 경로) 정의 ---

@app.route('/')
def index():
    """'templates' 폴더 안의 'index.html' 파일을 찾아 사용자에게 보여줍니다."""
    return render_template('index.html')

@app.route('/api/webtoons', methods=['GET'])
def get_webtoons():
    """데이터베이스에서 웹툰을 가져와 '요일별 연재'와 '완결'로 그룹화하여 반환합니다."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT title_id, title_text, author, weekday, status FROM webtoons WHERE status = '연재중'")
    ongoing_webtoons = [dict(row) for row in cursor.fetchall()]
    
    cursor.execute("SELECT title_id, title_text, author, weekday, status FROM webtoons WHERE status = '완결' ORDER BY rowid DESC LIMIT 100")
    completed_webtoons = [dict(row) for row in cursor.fetchall()]
    
    conn.close()

    grouped_by_day = {
        'mon': [], 'tue': [], 'wed': [], 'thu': [], 'fri': [], 'sat': [], 'sun': [], 'daily': []
    }
    weekday_map_eng = {'월': 'mon', '화': 'tue', '수': 'wed', '목': 'thu', '금': 'fri', '토': 'sat', '일': 'sun', '매일+': 'daily'}

    for webtoon in ongoing_webtoons:
        day_eng = weekday_map_eng.get(webtoon['weekday'])
        if day_eng and day_eng in grouped_by_day:
            grouped_by_day[day_eng].append(webtoon)

    return jsonify({
        'ongoing': grouped_by_day,
        'completed': completed_webtoons
    })


@app.route('/api/subscribe', methods=['POST'])
def subscribe():
    """사용자의 구독 요청을 받아 데이터베이스에 저장합니다."""
    data = request.json
    email = data.get('email')
    title_id = data.get('titleId')

    if not email or not title_id:
        return jsonify({'status': 'error', 'message': '이메일과 웹툰 ID가 필요합니다.'}), 400

    # --- [개선됨] 서버에서 이메일 형식 검증 ---
    if not is_valid_email(email):
        # 형식이 올바르지 않으면 400 에러와 함께 명확한 메시지를 반환합니다.
        return jsonify({'status': 'error', 'message': '올바른 이메일 형식이 아닙니다.'}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO subscriptions (email, title_id) VALUES (?, ?)", (email, title_id))
        conn.commit()
        conn.close()
        return jsonify({'status': 'success', 'message': f'ID {title_id} 구독 완료!'})
    except sqlite3.Error as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- 5. 서버 실행 ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
