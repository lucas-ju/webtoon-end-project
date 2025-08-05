# =====================================================================================
#  파일: app.py (웹 서버 및 API) - v3.4
#  - [로직 개선] 요일별 목록 API가 'weekday' 값이 있는 웹툰만 조회하도록 수정하여,
#    단기 휴재작(요일별)과 장기 휴재작(전체 휴재탭)을 명확히 분리합니다.
#  - [검색 기능 강화] 띄어쓰기에 관계없이 일관된 검색 결과를 제공합니다.
# =====================================================================================

import sqlite3
import re
from flask import Flask, jsonify, request, g, render_template
from flask_cors import CORS
import math

# --- 1. Flask 앱 초기화 및 설정 ---
app = Flask(__name__)
CORS(app)
DATABASE = 'webtoons.db'


# --- 2. 데이터베이스 연결 관리 ---
def get_db():
    """Application Context 내에서 유일한 DB 연결을 가져옵니다."""
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception=None):
    """요청(request)이 끝나면 자동으로 호출되어 DB 연결을 닫습니다."""
    db = g.pop('db', None)
    if db is not None:
        db.close()

# --- 3. 유틸리티 함수 ---
def is_valid_email(email):
    """서버 단에서 이메일 형식의 유효성을 검증합니다."""
    return re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email)

# --- 4. 기본 라우트 ---
@app.route('/')
def index():
    """웹 애플리케이션의 메인 페이지를 렌더링합니다."""
    return render_template('index.html')

# --- 5. API 엔드포인트 ---

@app.route('/api/search', methods=['GET'])
def search_webtoons():
    """전체 DB에서 웹툰 제목을 검색하여 결과를 반환합니다."""
    query = request.args.get('q', '').strip()

    if not query:
        return jsonify([])

    query_no_spaces = query.replace(' ', '')
    
    conn = get_db()
    cursor = conn.cursor()
    
    search_pattern = f'%{query_no_spaces}%'
    cursor.execute(
        """
        SELECT title_id, title_text, author, status 
        FROM webtoons 
        WHERE REPLACE(title_text, ' ', '') LIKE ? 
        ORDER BY rowid DESC 
        LIMIT 100
        """,
        (search_pattern,)
    )
    
    search_results = [dict(row) for row in cursor.fetchall()]
    
    return jsonify(search_results)


@app.route('/api/webtoons/ongoing', methods=['GET'])
def get_ongoing_webtoons():
    """요일별 웹툰 목록(연재 및 단기 휴재 포함)을 그룹화하여 반환합니다."""
    conn = get_db()
    cursor = conn.cursor()
    
    # [핵심 수정] 'weekday' 컬럼에 유효한 요일 값이 있는 웹툰만 조회합니다.
    # 이렇게 하면 '화산귀환'과 같은 장기 휴재작이 요일 탭에 나타나는 것을 방지합니다.
    valid_weekdays = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun', 'daily')
    query = f"SELECT title_id, title_text, author, weekday, status FROM webtoons WHERE weekday IN {valid_weekdays}"
    cursor.execute(query)
    
    all_daily_webtoons = [dict(row) for row in cursor.fetchall()]

    grouped_by_day = { 'mon': [], 'tue': [], 'wed': [], 'thu': [], 'fri': [], 'sat': [], 'sun': [], 'daily': [] }
    for webtoon in all_daily_webtoons:
        day_eng = webtoon.get('weekday')
        if day_eng in grouped_by_day:
            grouped_by_day[day_eng].append(webtoon)
            
    return jsonify(grouped_by_day)

@app.route('/api/webtoons/hiatus', methods=['GET'])
def get_hiatus_webtoons():
    """[페이지네이션] 휴재중인 웹툰 전체 목록을 페이지별로 반환합니다."""
    page = request.args.get('page', 1, type=int)
    per_page = 100
    offset = (page - 1) * per_page

    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM webtoons WHERE status = '휴재'")
    total_items = cursor.fetchone()[0]
    total_pages = math.ceil(total_items / per_page)

    cursor.execute(
        "SELECT title_id, title_text, author, status FROM webtoons WHERE status = '휴재' ORDER BY rowid DESC LIMIT ? OFFSET ?",
        (per_page, offset)
    )
    hiatus_webtoons = [dict(row) for row in cursor.fetchall()]
    
    return jsonify({
        'webtoons': hiatus_webtoons,
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total_pages': total_pages,
            'total_items': total_items
        }
    })

@app.route('/api/webtoons/completed', methods=['GET'])
def get_completed_webtoons():
    """[페이지네이션] 완결된 웹툰 목록을 페이지별로 반환합니다."""
    page = request.args.get('page', 1, type=int)
    per_page = 100
    offset = (page - 1) * per_page

    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM webtoons WHERE status = '완결'")
    total_items = cursor.fetchone()[0]
    total_pages = math.ceil(total_items / per_page)

    cursor.execute(
        "SELECT title_id, title_text, author, status FROM webtoons WHERE status = '완결' ORDER BY rowid DESC LIMIT ? OFFSET ?",
        (per_page, offset)
    )
    completed_webtoons = [dict(row) for row in cursor.fetchall()]
    
    return jsonify({
        'webtoons': completed_webtoons,
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total_pages': total_pages,
            'total_items': total_items
        }
    })


@app.route('/api/subscribe', methods=['POST'])
def subscribe():
    """사용자의 구독 요청을 처리합니다."""
    data = request.json
    email, title_id = data.get('email'), data.get('titleId')

    if not all([email, title_id]):
        return jsonify({'status': 'error', 'message': '이메일과 웹툰 ID가 필요합니다.'}), 400
    if not is_valid_email(email):
        return jsonify({'status': 'error', 'message': '올바른 이메일 형식이 아닙니다.'}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO subscriptions (email, title_id) VALUES (?, ?)", (email, str(title_id)))
        conn.commit()
        return jsonify({'status': 'success', 'message': f'ID {title_id} 구독 완료!'})
    except sqlite3.Error as e:
        return jsonify({'status': 'error', 'message': f'데이터베이스 오류: {e}'}), 500

# --- 6. 실행 ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
