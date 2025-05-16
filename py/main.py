from flask import Flask, render_template, url_for, session, redirect, jsonify , request, flash, get_flashed_messages, g, abort
import sqlite3
import re
from dotenv import load_dotenv
import os
import csv
from admin import admin_bp
from slugify import slugify
import logging
import smtplib
from email.mime.text import MIMEText
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
import requests
import json
from datetime import datetime

load_dotenv()  # Nạp các biến từ file .env

app = Flask(__name__, template_folder='../templates', static_folder='../static')
DATABASE = 'database/database.db'
UPLOAD_FOLDER = 'exams_csv'
ALLOWED_EXTENSIONS = {'csv'}
app.secret_key = 'your_secret_key'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Cấu hình Google OAuth
GOOGLE_CLIENT_ID = "541263464377-tov1471eb666h9tpifcao7bvclelrf3l.apps.googleusercontent.com"  # Thay bằng Client ID của bạn
GOOGLE_CLIENT_SECRET = "GOCSPX-OcyoQIry2crosrf_pFHGOmOiVSV"  # Thay bằng Client Secret của bạn
GOOGLE_REDIRECT_URI = "http://localhost:5000/callback"  # Đảm bảo khớp với URI trong Google Console
GOOGLE_SCOPES = ['https://www.googleapis.com/auth/userinfo.email', 'https://www.googleapis.com/auth/userinfo.profile', 'openid']

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db

def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def teacher_or_admin_required():
    if 'username' not in session or not (session.get('is_teacher') or session.get('is_admin')):
        flash('Bạn cần quyền giáo viên hoặc admin để thực hiện hành động này.', 'error')
        return redirect(url_for('show_exams_page'))
    return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/exams')
def show_exams_page():
    conn = get_db()
    db = conn.cursor()
    exams = db.execute('SELECT id, name, subject, grade, duration, creator_username FROM exams').fetchall()
    conn.close()
    return render_template('exams.html', exams=exams)

@app.route('/document')
def document():
    return render_template('document.html')

@app.route('/class')
def class_page():
    return render_template('class.html')

@app.route('/class/create', methods=['GET', 'POST'])
def create_class():
    auth_check = teacher_or_admin_required()
    if auth_check:
        return auth_check

    if request.method == 'POST':
        class_name = request.form['class_name']
        subject = request.form['subject']
        description = request.form.get('description', '')

        if not class_name:
            flash('Vui lòng nhập tên lớp học', 'error')
            return render_template('create_class.html')

        conn = get_db()
        try:
            conn.execute(
                'INSERT INTO classes (name, subject, description, creator_username) VALUES (?, ?, ?, ?)',
                (class_name, subject, description, session['username'])
            )
            conn.commit()
            flash('Tạo lớp học thành công!', 'success')
            return redirect(url_for('class_list'))
        except sqlite3.IntegrityError:
            flash('Tên lớp học đã tồn tại', 'error')
        finally:
            conn.close()

    return render_template('create_class.html')

@app.route('/class/list')
def class_list():
    if 'username' not in session:
        flash('Vui lòng đăng nhập để xem danh sách lớp học.', 'error')
        return redirect(url_for('login'))

    conn = get_db()
    db = conn.cursor()
    try:
        classes = db.execute('''
            SELECT c.id, c.name, c.subject, c.description, c.creator_username, 
                   COUNT(cm.user_id) as member_count 
            FROM classes c 
            LEFT JOIN class_members cm ON c.id = cm.class_id 
            GROUP BY c.id
        ''').fetchall()

        # Lấy danh sách class_id mà user đã tham gia
        joined_class_ids = set()
        if not (session.get('is_admin') or session.get('is_teacher')):
            rows = db.execute('SELECT class_id FROM class_members WHERE user_id = ?', (session.get('user_id'),)).fetchall()
            joined_class_ids = set(row['class_id'] for row in rows)

        return render_template('class.html', classes=classes, joined_class_ids=joined_class_ids)
    except Exception as e:
        print(f"Error in class_list: {str(e)}")
        flash('Có lỗi xảy ra khi tải danh sách lớp học.', 'error')
        return redirect(url_for('index'))
    finally:
        conn.close()

@app.route('/class/<int:class_id>')
def class_detail(class_id):
    if 'username' not in session:
        flash('Vui lòng đăng nhập để xem chi tiết lớp học.', 'error')
        return redirect(url_for('login'))

    conn = get_db()
    db = conn.cursor()
    
    try:
        # Kiểm tra quyền truy cập
        class_info = db.execute('''
            SELECT c.*, 
                   CASE WHEN c.creator_username = ? OR EXISTS 
                        (SELECT 1 FROM class_members WHERE class_id = ? AND user_id = ?) 
                   THEN 1 ELSE 0 END as has_access
            FROM classes c WHERE c.id = ?
        ''', (session.get('username'), class_id, session.get('user_id'), class_id)).fetchone()
        
        if not class_info:
            abort(404)
            
        if not class_info['has_access'] and not (session.get('is_admin') or session.get('is_teacher')):
            abort(403)

        # Lấy thành viên lớp
        members = db.execute('''
            SELECT u.id, u.username, u.fullname, u.email, u.phone 
            FROM class_members cm 
            JOIN users u ON cm.user_id = u.id 
            WHERE cm.class_id = ?
        ''', (class_id,)).fetchall()
        
        # Lấy bảng xếp hạng đơn giản
        rankings = db.execute('''
            SELECT 
                u.id,
                u.fullname,
                COALESCE(AVG(er.score), 0) as avg_score,
                COUNT(er.id) as exam_count,
                COALESCE(
                    (SELECT COUNT(*) * 100.0 / 
                        (SELECT COUNT(DISTINCT date) FROM class_attendance WHERE class_id = ?)
                    FROM class_attendance ca 
                    WHERE ca.class_id = ? AND ca.user_id = u.id AND ca.status = 'present'),
                    0
                ) as attendance_rate
            FROM users u
            JOIN class_members cm ON u.id = cm.user_id
            LEFT JOIN exam_results er ON u.id = er.user_id AND er.class_id = ?
            WHERE cm.class_id = ?
            GROUP BY u.id
            ORDER BY avg_score DESC, attendance_rate DESC
        ''', (class_id, class_id, class_id, class_id)).fetchall()
        
        # Lấy danh sách bài thi trong lớp
        exams = db.execute('''
            SELECT e.id, e.name, e.subject, e.grade, e.duration 
            FROM class_exams ce 
            JOIN exams e ON ce.exam_id = e.id 
            WHERE ce.class_id = ?
        ''', (class_id,)).fetchall()
        
        # Lấy tất cả đề thi có thể thêm vào lớp (cho giáo viên)
        all_exams = []
        if session.get('is_admin') or session.get('is_teacher'):
            all_exams = db.execute('''
                SELECT e.id, e.name, e.subject 
                FROM exams e
                WHERE e.id NOT IN (
                    SELECT exam_id FROM class_exams WHERE class_id = ?
                )
                AND (e.creator_username = ? OR ? = 1)
            ''', (class_id, session.get('username'), session.get('is_admin'))).fetchall()

        # Thêm nút xem báo cáo điểm danh cho giáo viên
        if session.get('is_admin') or session.get('is_teacher'):
            return render_template('class_detail.html', 
                                 class_info=class_info, 
                                 members=members,
                                 exams=exams,
                                 all_exams=all_exams,
                                 rankings=rankings,
                                 show_attendance_report=True)
        else:
            return render_template('class_detail.html', 
                                 class_info=class_info, 
                                 members=members,
                                 exams=exams,
                                 all_exams=all_exams,
                                 rankings=rankings,
                                 show_attendance_report=False)
                             
    except Exception as e:
        print(f"Error in class_detail: {str(e)}")
        flash('Có lỗi xảy ra khi tải thông tin lớp học.', 'error')
        return redirect(url_for('class_list'))
    finally:
        conn.close()

@app.route('/class/join/<int:class_id>', methods=['POST'])
def join_class(class_id):
    if 'username' not in session or 'user_id' not in session:
        flash('Vui lòng đăng nhập để tham gia lớp học.', 'error')
        return redirect(url_for('login'))

    conn = get_db()
    try:
        # Kiểm tra đã là thành viên chưa
        existing = conn.execute('''
            SELECT 1 FROM class_members 
            WHERE class_id = ? AND user_id = ?
        ''', (class_id, session['user_id'])).fetchone()
        
        if existing:
            flash('Bạn đã là thành viên của lớp này', 'info')
        else:
            conn.execute('''
                INSERT INTO class_members (class_id, user_id, join_date) 
                VALUES (?, ?, datetime('now'))
            ''', (class_id, session['user_id']))
            conn.commit()
            flash('Tham gia lớp thành công!', 'success')
    finally:
        conn.close()
    
    return redirect(url_for('class_detail', class_id=class_id))

@app.route('/class/invite/<int:class_id>', methods=['POST'])
def invite_to_class(class_id):
    auth_check = teacher_or_admin_required()
    if auth_check:
        return auth_check

    email_or_phone = request.form['contact']
    conn = get_db()
    try:
        # Tìm user bằng email hoặc phone
        user = conn.execute('''
            SELECT id, username, email FROM users 
            WHERE email = ? OR phone = ?
        ''', (email_or_phone, email_or_phone)).fetchone()
        
        if not user:
            flash('Không tìm thấy người dùng với thông tin này', 'error')
            return redirect(url_for('class_detail', class_id=class_id))

        # Kiểm tra đã là thành viên chưa
        existing = conn.execute('''
            SELECT 1 FROM class_members 
            WHERE class_id = ? AND user_id = ?
        ''', (class_id, user['id'])).fetchone()
        
        if existing:
            flash('Người dùng đã là thành viên của lớp này', 'info')
        else:
            conn.execute('''
                INSERT INTO class_members (class_id, user_id, join_date) 
                VALUES (?, ?, datetime('now'))
            ''', (class_id, user['id']))
            conn.commit()
            flash('Đã thêm học sinh vào lớp', 'success')
    finally:
        conn.close()
    
    return redirect(url_for('class_detail', class_id=class_id))

@app.route('/class/manage_attendance/<int:class_id>', methods=['GET', 'POST'])
def manage_attendance(class_id):
    auth_check = teacher_or_admin_required()
    if auth_check:
        return auth_check

    conn = get_db()
    try:
        # Lấy thông tin lớp học
        class_info = conn.execute('SELECT * FROM classes WHERE id = ?', (class_id,)).fetchone()
        
        if request.method == 'POST':
            date = request.form['date']
            
            # Lưu điểm danh cho tất cả học sinh
            for user_id in request.form.getlist('user_ids'):
                status = request.form.get(f'status_{user_id}', 'absent')
                conn.execute('''
                    INSERT INTO class_attendance (class_id, user_id, date, status) 
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(class_id, user_id, date) 
                    DO UPDATE SET status = ?
                ''', (class_id, user_id, date, status, status))
            
            conn.commit()
            flash('Đã lưu điểm danh', 'success')
            return redirect(url_for('class_detail', class_id=class_id))

        # Lấy danh sách học sinh
        members = conn.execute('''
            SELECT u.id, u.username, u.fullname 
            FROM class_members cm 
            JOIN users u ON cm.user_id = u.id 
            WHERE cm.class_id = ?
        ''', (class_id,)).fetchall()
        
        # Lấy lịch sử điểm danh
        attendance_history = conn.execute('''
            SELECT ca.date, u.username, u.fullname, ca.status
            FROM class_attendance ca
            JOIN users u ON ca.user_id = u.id
            WHERE ca.class_id = ?
            ORDER BY ca.date DESC, u.fullname
        ''', (class_id,)).fetchall()

    finally:
        conn.close()
    
    return render_template('take_attendance.html', 
                         class_info=class_info,
                         members=members,
                         attendance_history=attendance_history)

@app.route('/class/mark_attendance/<int:class_id>', methods=['POST'])
def mark_attendance(class_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    if not (session.get('is_admin') or session.get('is_teacher')):
        flash('Bạn không có quyền thực hiện thao tác này', 'error')
        return redirect(url_for('class_detail', class_id=class_id))

    conn = get_db()
    try:
        date = request.form.get('date')
        if not date:
            flash('Vui lòng chọn ngày điểm danh', 'error')
            return redirect(url_for('manage_attendance', class_id=class_id))

        # Xóa điểm danh cũ nếu có
        conn.execute('''
            DELETE FROM class_attendance 
            WHERE class_id = ? AND date = ?
        ''', (class_id, date))

        # Lưu điểm danh mới
        for student_id in request.form.getlist('students'):
            status = request.form.get(f'status_{student_id}')
            if status in ['present', 'absent']:
                conn.execute('''
                    INSERT INTO class_attendance (class_id, user_id, date, status)
                    VALUES (?, ?, ?, ?)
                ''', (class_id, student_id, date, status))

        conn.commit()
        flash('Đã lưu điểm danh thành công', 'success')

    except Exception as e:
        flash(f'Lỗi khi lưu điểm danh: {str(e)}', 'error')
    finally:
        conn.close()

    return redirect(url_for('manage_attendance', class_id=class_id))

@app.route('/class/view_attendance/<int:class_id>')
def view_attendance(class_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    conn = get_db()
    try:
        # Kiểm tra quyền truy cập
        is_teacher = session.get('is_admin') or session.get('is_teacher')
        is_member = conn.execute('''
            SELECT 1 FROM class_members 
            WHERE class_id = ? AND user_id = ?
        ''', (class_id, session.get('user_id'))).fetchone()
        
        if not (is_teacher or is_member):
            flash('Bạn không có quyền xem điểm danh của lớp này', 'error')
            return redirect(url_for('class_detail', class_id=class_id))

        # Lấy thông tin lớp học
        class_info = conn.execute('SELECT * FROM classes WHERE id = ?', (class_id,)).fetchone()
        
        # Lấy lịch sử điểm danh
        if is_teacher:
            # Giáo viên xem được tất cả điểm danh
            attendance_records = conn.execute('''
                SELECT ca.date, u.username, u.fullname, ca.status
                FROM class_attendance ca
                JOIN users u ON ca.user_id = u.id
                WHERE ca.class_id = ?
                ORDER BY ca.date DESC, u.fullname
            ''', (class_id,)).fetchall()
        else:
            # Học sinh chỉ xem được điểm danh của mình
            attendance_records = conn.execute('''
                SELECT date, status
                FROM class_attendance
                WHERE class_id = ? AND user_id = ?
                ORDER BY date DESC
            ''', (class_id, session.get('user_id'))).fetchall()

    finally:
        conn.close()
    
    return render_template('view_attendance.html',
                         class_info=class_info,
                         attendance_records=attendance_records,
                         is_teacher=is_teacher)

@app.route('/class/start_exam/<int:class_id>/<int:exam_id>')
def start_class_exam(class_id, exam_id):
    auth_check = teacher_or_admin_required()
    if auth_check:
        return auth_check

    # Kiểm tra exam thuộc class
    conn = get_db()
    try:
        valid = conn.execute('''
            SELECT 1 FROM class_exams 
            WHERE class_id = ? AND exam_id = ?
        ''', (class_id, exam_id)).fetchone()
        
        if not valid:
            flash('Đề thi không thuộc lớp học này', 'error')
            return redirect(url_for('class_detail', class_id=class_id))
    finally:
        conn.close()
    
    # Bắt đầu bài thi (có thể thêm logic như set thời gian làm bài, etc.)
    return redirect(url_for('take_exam', exam_id=exam_id, from_class=class_id))

@app.route('/contact')
def contact():
    return render_template('contact.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = get_db()
        db = conn.cursor()
        user = db.execute(
            'SELECT id, username, password, is_admin, is_teacher FROM users WHERE username = ?', (username,)
        ).fetchone()
        conn.close()

        if user is None:
            flash('Tên đăng nhập không tồn tại.', 'error')
        elif user['password'] != password:
            flash('Mật khẩu không đúng.', 'error')
        else:
            session['username'] = user['username']
            session['user_id'] = user['id']
            session['is_admin'] = user['is_admin']
            session['is_teacher'] = user['is_teacher']
            flash('Đăng nhập thành công!', 'success')
            return redirect(url_for('show_exams_page'))
    return render_template('login.html')

@app.route('/signup')
def signup():
    return render_template('signup.html')

@app.route('/register', methods=['POST'])
def register():
    fullname = request.form['fullname']
    phone = "".join(request.form['phone'].split())
    gender = request.form['gender']
    username = request.form['username']
    email = request.form['email']
    password = request.form['password']
    confirm_password = request.form['confirm_password']

    error = None
    if password != confirm_password:
        error = 'Mật khẩu và xác nhận mật khẩu không khớp.'
    elif not fullname:
        error = 'Vui lòng nhập họ tên.'
    elif not phone:
        error = 'Vui lòng nhập số điện thoại.'
    elif not re.match(r'^0\d{9}$', phone):
        error = 'Số điện thoại phải có dạng 0xxxxxxxxx với 10 chữ số.'
    elif not username:
        error = 'Vui lòng nhập tên đăng nhập.'
    elif not email:
        error = 'Vui lòng nhập địa chỉ email.'
    elif not password:
        error = 'Vui lòng nhập mật khẩu.'
    else:
        conn = get_db()
        db = conn.cursor()
        if db.execute(
            'SELECT id FROM users WHERE username = ?', (username,)
        ).fetchone() is not None:
            error = f'Tên đăng nhập "{username}" đã tồn tại.'
        elif db.execute(
            'SELECT id FROM users WHERE email = ?', (email,)
        ).fetchone() is not None:
            error = f'Địa chỉ email "{email}" đã được sử dụng.'
        conn.close()

    if error:
        flash(error, 'error')
        return render_template('signup.html')
    else:
        conn = get_db()
        db = conn.cursor()
        db.execute(
            'INSERT INTO users (fullname, phone, gender, username, email, password, is_admin, is_teacher) VALUES (?, ?, ?, ?, ?, ?, 0, 0)',
            (fullname, phone, gender, username, email, password)
        )
        conn.commit()
        conn.close()
        flash('Đăng ký thành công!', 'success')
        return render_template('signup.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    session.pop('user_id', None)
    session.pop('is_admin', None)
    session.pop('is_teacher', None)
    return redirect(url_for('index'))


@app.route('/user')
def user_profile():
    if 'username' in session:
        conn = get_db()
        db = conn.cursor()
        user_data = db.execute(
            'SELECT fullname, email, phone FROM users WHERE username = ?', (session['username'],)
        ).fetchone()
        conn.close()
        return render_template('user.html', username=session['username'], user_data=user_data)
    else:
        return redirect(url_for('login'))

@app.route('/update_profile', methods=['GET', 'POST'])
def update_profile():
    if 'username' not in session:
        return redirect(url_for('login'))

    conn = get_db()
    db = conn.cursor()

    if request.method == 'POST':
        fullname = request.form['fullname']
        phone = "".join(request.form['phone'].split())
        email = request.form['email']

        error = None
        if not fullname:
            error = 'Vui lòng nhập họ tên.'
        elif not phone:
            error = 'Vui lòng nhập số điện thoại.'
        elif not re.match(r'^0\d{9}$', phone):
            error = 'Số điện thoại phải có dạng 0xxxxxxxxx với 10 chữ số.'
        elif not email:
            error = 'Vui lòng nhập địa chỉ email.'
        else:
            existing_email = db.execute(
                'SELECT id FROM users WHERE email = ? AND username != ?', (email, session['username'])
            ).fetchone()
            if existing_email:
                error = f'Địa chỉ email "{email}" đã được sử dụng bởi người dùng khác.'

        if error:
            flash(error, 'error')
        else:
            db.execute(
                'UPDATE users SET fullname = ?, phone = ?, email = ? WHERE username = ?',
                (fullname, phone, email, session['username'])
            )
            conn.commit()
            flash('Cập nhật thông tin thành công!', 'success')
            return redirect(url_for('user_profile'))

    user_data = db.execute(
        'SELECT fullname, email, phone FROM users WHERE username = ?', (session['username'],)
    ).fetchone()
    conn.close()
    return render_template('update_profile.html', user_data=user_data)

@app.route('/create_exam')
def create_exam():
    auth_check = teacher_or_admin_required()
    if auth_check:
        return auth_check
    return render_template('create_exam_form.html')

@app.route('/save_exam', methods=['POST'])
def save_exam():
    auth_check = teacher_or_admin_required()
    if auth_check:
        return auth_check

    exam_name = request.form['exam_name']
    exam_subject = request.form['exam_subject']
    exam_grade = request.form['exam_grade']
    exam_duration = request.form['exam_duration']
    questions = request.form.getlist('questions')
    answers_a = request.form.getlist('answers[A]')
    answers_b = request.form.getlist('answers[B]') if 'answers[B]' in request.form else []
    answers_c = request.form.getlist('answers[C]') if 'answers[C]' in request.form else []
    answers_d = request.form.getlist('answers[D]') if 'answers[D]' in request.form else []
    correct_answers = request.form.getlist('correct_answer') if 'correct_answer' in request.form else []

    if not exam_name or not questions:
        flash('Vui lòng nhập tên đề và ít nhất một câu hỏi.', 'error')
        return render_template('create_exam_form.html')

    csv_filename = f"{slugify(exam_name)}.csv"
    csv_filepath = os.path.join(app.config['UPLOAD_FOLDER'], csv_filename)

    try:
        with open(csv_filepath, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(['Câu hỏi', 'Đáp án A', 'Đáp án B', 'Đáp án C', 'Đáp án D', 'Đáp án đúng'])
            for i in range(len(questions)):
                answer_a = answers_a[i] if i < len(answers_a) else ''
                answer_b = answers_b[i] if i < len(answers_b) else ''
                answer_c = answers_c[i] if i < len(answers_c) else ''
                answer_d = answers_d[i] if i < len(answers_d) else ''
                correct_answer = correct_answers[i] if i < len(correct_answers) else ''
                writer.writerow([
                    questions[i],
                    answer_a,
                    answer_b,
                    answer_c,
                    answer_d,
                    correct_answer
                ])

        conn = get_db()
        db = conn.cursor()
        db.execute(
            'INSERT INTO exams (name, subject, grade, duration, creator_username, file_path) VALUES (?, ?, ?, ?, ?, ?)',
            (exam_name, exam_subject, exam_grade, exam_duration, session['username'], csv_filename)
        )
        conn.commit()
        conn.close()
        flash(f'Đề thi "{exam_name}" đã được lưu thành công!', 'success')
        return redirect(url_for('show_exams_page'))
    except Exception as e:
        if os.path.exists(csv_filepath):
            os.remove(csv_filepath)
        flash(f'Đã xảy ra lỗi khi lưu đề thi: {e}', 'error')
        return render_template('create_exam_form.html')

@app.route('/exam/<int:exam_id>', methods=['GET', 'POST'])
def exam(exam_id):
    conn = get_db()
    db = conn.cursor()
    exam = db.execute('SELECT id, name, subject, grade, duration, file_path, creator_username, created_at FROM exams WHERE id = ?', (exam_id,)).fetchone()

    if not exam:
        conn.close()
        flash('Không tìm thấy đề thi.', 'error')
        return redirect(url_for('show_exams_page'))

    questions = []
    csv_filepath = os.path.join(app.config['UPLOAD_FOLDER'], exam['file_path'])
    try:
        with open(csv_filepath, 'r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                questions.append(row)
    except FileNotFoundError:
        conn.close()
        flash('Không tìm thấy file đề thi.', 'error')
        return redirect(url_for('show_exams_page'))
    except Exception as e:
        conn.close()
        flash(f'Lỗi khi đọc file đề thi: {e}', 'error')
        return redirect(url_for('show_exams_page'))

    can_edit = session.get('username') == exam['creator_username'] or session.get('is_admin', False)

    if request.method == 'POST':
        if not can_edit:
            conn.close()
            flash('Bạn không có quyền chỉnh sửa đề thi này.', 'error')
            return redirect(url_for('show_exams_page'))

        name = request.form['exam_name']
        subject = request.form['exam_subject']
        grade = request.form['exam_grade']
        duration = request.form['exam_duration']
        questions_form = request.form.getlist('questions')
        answers_a = request.form.getlist('answers[A]')
        answers_b = request.form.getlist('answers[B]') if 'answers[B]' in request.form else []
        answers_c = request.form.getlist('answers[C]') if 'answers[C]' in request.form else []
        answers_d = request.form.getlist('answers[D]') if 'answers[D]' in request.form else []
        correct_answers = request.form.getlist('correct_answer') if 'correct_answer' in request.form else []

        if not all([name, subject, grade, duration]) or not questions_form:
            conn.close()
            flash('Vui lòng điền đầy đủ thông tin và ít nhất một câu hỏi.', 'error')
            return render_template('exam.html', exam=exam, questions=questions, can_edit=can_edit)

        try:
            duration = int(duration)
            if duration <= 0:
                raise ValueError('Thời gian phải lớn hơn 0.')
        except ValueError:
            conn.close()
            flash('Thời gian phải là số nguyên dương.', 'error')
            return render_template('exam.html', exam=exam, questions=questions, can_edit=can_edit)

        new_csv_filename = f"{slugify(name)}_{session['username']}.csv"
        new_csv_filepath = os.path.join(app.config['UPLOAD_FOLDER'], new_csv_filename)

        try:
            with open(new_csv_filepath, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(['Câu hỏi', 'Đáp án A', 'Đáp án B', 'Đáp án C', 'Đáp án D', 'Đáp án đúng'])
                for i in range(len(questions_form)):
                    answer_a = answers_a[i] if i < len(answers_a) else ''
                    answer_b = answers_b[i] if i < len(answers_b) else ''
                    answer_c = answers_c[i] if i < len(answers_c) else ''
                    answer_d = answers_d[i] if i < len(answers_d) else ''
                    correct_answer = correct_answers[i] if i < len(correct_answers) else ''
                    writer.writerow([
                        questions_form[i],
                        answer_a,
                        answer_b,
                        answer_c,
                        answer_d,
                        correct_answer
                    ])

            old_csv_filepath = os.path.join(app.config['UPLOAD_FOLDER'], exam['file_path'])
            if os.path.exists(old_csv_filepath) and new_csv_filename != exam['file_path']:
                os.remove(old_csv_filepath)

            db.execute('UPDATE exams SET name = ?, subject = ?, grade = ?, duration = ?, file_path = ? WHERE id = ?',
                       (name, subject, grade, duration, new_csv_filename, exam_id))
            conn.commit()
            flash('Cập nhật đề thi thành công!', 'success')
            conn.close()
            return redirect(url_for('show_exams_page'))
        except sqlite3.IntegrityError:
            conn.close()
            flash('Tên đề thi hoặc file trùng lặp.', 'error')
            if os.path.exists(new_csv_filepath):
                os.remove(new_csv_filepath)
            return render_template('exam.html', exam=exam, questions=questions, can_edit=can_edit)
        except Exception as e:
            conn.close()
            flash(f'Lỗi khi lưu đề thi: {e}', 'error')
            if os.path.exists(new_csv_filepath):
                os.remove(new_csv_filepath)
            return render_template('exam.html', exam=exam, questions=questions, can_edit=can_edit)

    conn.close()
    return render_template('exam.html', exam=exam, questions=questions, can_edit=can_edit)

@app.route('/exams/list')
def list_exams():
    conn = get_db()
    db = conn.cursor()
    exams = db.execute('SELECT id, name, subject, grade, duration, creator_username FROM exams').fetchall()
    conn.close()
    return render_template('exams.html', exams=exams)



@app.route('/search_exams', methods=['POST'])
def search_exams():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    data = request.get_json()
    print("Received data:", data)  # Thêm dòng này
    search_term = data.get('search', '')
    time_filter = data.get('time', '')
    grade_filter = data.get('grade', '')
    subject_filter = data.get('subject', '')

    query = "SELECT * FROM exams WHERE 1=1"
    params = []

    if search_term:
        query += " AND (name LIKE ? COLLATE NOCASE OR creator_username LIKE ? COLLATE NOCASE)"
        params.extend([f'%{search_term}%', f'%{search_term}%'])

    if subject_filter:
        query += " AND subject = ?"
        params.append(subject_filter)

    if grade_filter:
        query += " AND grade = ?"
        params.append(grade_filter)

    if time_filter == 'newest':
        query += " ORDER BY created_at DESC"
    elif time_filter == 'oldest':
        query += " ORDER BY created_at ASC"

    print("Executing query:", query, params)  # Thêm dòng này
    cursor.execute(query, params)
    exams = cursor.fetchall()
    print("Query result:", exams)  # Thêm dòng này

    exams_list = []
    for exam in exams:
        exams_list.append({
            'id': exam[0],
            'name': exam[1],
            'subject': exam[2],
            'grade': exam[3],
            'duration': exam[4],
            'creator_username': exam[5]
        })

    conn.close()
    return jsonify({'exams': exams_list})

@app.route('/take_exam/<int:exam_id>')
def take_exam(exam_id):
    logging.debug(f"take_exam called with exam_id: {exam_id}")
    conn = get_db()
    db = conn.cursor()
    exam = db.execute('SELECT name, duration, file_path, id FROM exams WHERE id = ?', (exam_id,)).fetchone()
    conn.close()

    if exam:
        questions = []
        csv_filepath = os.path.join(app.config['UPLOAD_FOLDER'], exam['file_path'])
        try:
            with open(csv_filepath, 'r', encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    questions.append(row)
            logging.debug(f"Exam data: {exam}")
            logging.debug(f"Questions: {questions}")
            return render_template('take_exam.html', exam=exam, questions=questions)
        except FileNotFoundError:
            logging.error(f"File not found for exam_id {exam_id}")
            flash('Không tìm thấy file đề thi.', 'error')
            return redirect(url_for('show_exams_page'))
        except Exception as e:
            logging.exception(f"Error reading file for exam_id {exam_id}: {e}")
            flash(f'Lỗi khi đọc file đề thi: {e}', 'error')
            return redirect(url_for('show_exams_page'))
    else:
        logging.warning(f"Exam not found with exam_id: {exam_id}")
        flash('Không tìm thấy đề thi.', 'error')
        return redirect(url_for('show_exams_page'))

@app.route('/submit_exam/<int:exam_id>', methods=['POST'])
def submit_exam(exam_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    conn = get_db()
    try:
        exam = conn.execute('SELECT file_path, name FROM exams WHERE id = ?', (exam_id,)).fetchone()
        
        if not exam:
            flash('Không tìm thấy đề thi.', 'error')
            return redirect(url_for('show_exams_page'))

        csv_filepath = os.path.join(app.config['UPLOAD_FOLDER'], exam['file_path'])
        correct_answers = {}
        try:
            with open(csv_filepath, 'r', encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    question = row['Câu hỏi']
                    correct_answers[question] = row['Đáp án đúng']
        except FileNotFoundError:
            flash('Không tìm thấy file đề thi.', 'error')
            return redirect(url_for('show_exams_page'))
        except Exception as e:
            flash(f'Lỗi khi đọc file đề thi: {e}', 'error')
            return redirect(url_for('show_exams_page'))

        score = 0
        total_questions = len(correct_answers)
        user_answers = request.form.to_dict()

        for question, correct_answer in correct_answers.items():
            if question in user_answers and user_answers[question] == correct_answer:
                score += 1

        points = round((score / total_questions) * 10, 2) if total_questions > 0 else 0.0

        # Lấy class_id từ form nếu có
        class_id = request.form.get('class_id')
        
        # Lưu kết quả bài thi
        conn.execute('''
            INSERT INTO exam_results (user_id, exam_id, class_id, score, points, submitted_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
        ''', (session.get('user_id'), exam_id, class_id, score, points))
        conn.commit()

        # Lưu chi tiết câu trả lời
        for question, user_answer in user_answers.items():
            if question in correct_answers:
                is_correct = user_answer == correct_answers[question]
                conn.execute('''
                    INSERT INTO exam_answers (user_id, exam_id, question, user_answer, correct_answer, is_correct)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (session.get('user_id'), exam_id, question, user_answer, correct_answers[question], is_correct))
        conn.commit()

        flash('Đã lưu kết quả bài thi', 'success')
        return render_template('exam_result.html', 
                             points=points, 
                             score=score, 
                             total=total_questions, 
                             exam_name=exam['name'])

    except Exception as e:
        flash(f'Lỗi khi lưu kết quả bài thi: {e}', 'error')
        return redirect(url_for('show_exams_page'))
    finally:
        conn.close()

@app.route('/review_exam/<int:exam_id>')
def review_exam(exam_id):
    if 'exam_result' not in session or session['exam_result']['exam_id'] != exam_id:
        flash('Không tìm thấy kết quả bài thi.', 'error')
        return redirect(url_for('show_exams_page'))

    exam_result = session['exam_result']
    user_answers = exam_result['user_answers']
    correct_answers = exam_result['correct_answers']
    exam_name = exam_result['exam_name']

    questions = []
    conn = get_db()
    db = conn.cursor()
    exam = db.execute('SELECT file_path FROM exams WHERE id = ?', (exam_id,)).fetchone()
    conn.close()

    if exam:
        csv_filepath = os.path.join(app.config['UPLOAD_FOLDER'], exam['file_path'])
        try:
            with open(csv_filepath, 'r', encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    question = row['Câu hỏi']
                    user_answer = user_answers.get(question, None)
                    correct_answer = correct_answers.get(question)
                    is_correct = user_answer == correct_answer if user_answer else False
                    questions.append({
                        'question': question,
                        'answer_a': row['Đáp án A'],
                        'answer_b': row['Đáp án B'],
                        'answer_c': row['Đáp án C'],
                        'answer_d': row['Đáp án D'],
                        'correct_answer': correct_answer,
                        'user_answer': user_answer,
                        'is_correct': is_correct
                    })
        except FileNotFoundError:
            flash('Không tìm thấy file đề thi.', 'error')
            return redirect(url_for('show_exams_page'))
        except Exception as e:
            flash(f'Lỗi khi đọc file đề thi: {e}', 'error')
            return redirect(url_for('show_exams_page'))

    return render_template('review_exam.html', exam_name=exam_name, questions=questions)

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        conn = get_db()
        db = conn.cursor()
        user = db.execute('SELECT email, username FROM users WHERE email = ?', (email,)).fetchone()
        conn.close()

        if not user:
            flash('Email không tồn tại trong hệ thống.', 'error')
        else:
            sender_email = "your_email@gmail.com"
            receiver_email = email
            password = "your_app_password"
            msg = MIMEText(f"Xin chào {user['username']},\n\nVui lòng nhấp vào liên kết sau để đặt lại mật khẩu của bạn: {url_for('reset_password', token='token_here', _external=True)}\n\nTrân trọng,\nĐội ngũ Luyện Thi Online")
            msg['Subject'] = 'Yêu cầu đặt lại mật khẩu'
            msg['From'] = sender_email
            msg['To'] = receiver_email

            try:
                with smtplib.SMTP('smtp.gmail.com', 587) as server:
                    server.starttls()
                    server.login(sender_email, password)
                    server.sendmail(sender_email, receiver_email, msg.as_string())
                flash('Một email chứa liên kết đặt lại mật khẩu đã được gửi đến bạn. Vui lòng kiểm tra hộp thư.', 'success')
            except Exception as e:
                flash(f'Đã xảy ra lỗi khi gửi email: {e}', 'error')

        return redirect(url_for('forgot_password'))

    return render_template('forgot_password.html')

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if request.method == 'POST':
        new_password = request.form['password']
        conn = get_db()
        db = conn.cursor()
        db.execute('UPDATE users SET password = ? WHERE email = ?', (new_password, 'email_from_token'))
        conn.commit()
        conn.close()
        flash('Mật khẩu đã được đặt lại thành công!', 'success')
        return redirect(url_for('login'))
    return render_template('reset_password.html', token=token)

@app.route('/change_password', methods=['GET', 'POST'])
def change_password():
    if 'username' not in session:
        return redirect(url_for('login'))

    conn = get_db()
    db = conn.cursor()

    if request.method == 'POST':
        current_password = request.form['current_password']
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']

        user = db.execute(
            'SELECT password FROM users WHERE username = ?', (session['username'],)
        ).fetchone()

        error = None
        if not current_password or not new_password or not confirm_password:
            error = 'Vui lòng điền đầy đủ thông tin.'
        elif user['password'] != current_password:
            error = 'Mật khẩu hiện tại không đúng.'
        elif new_password != confirm_password:
            error = 'Mật khẩu mới và xác nhận mật khẩu không khớp.'
        elif len(new_password) < 6:
            error = 'Mật khẩu mới phải có ít nhất 6 ký tự.'

        if error:
            flash(error, 'error')
        else:
            db.execute(
                'UPDATE users SET password = ? WHERE username = ?',
                (new_password, session['username'])
            )
            conn.commit()
            flash('Đổi mật khẩu thành công!', 'success')
            return redirect(url_for('user_profile'))

        conn.close()
        return render_template('change_password.html')

    conn.close()
    return render_template('change_password.html')

app.register_blueprint(admin_bp)

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store'
    return response

@app.route('/class/add_exam/<int:class_id>', methods=['POST'])
def add_exam_to_class(class_id):
    # Lấy exam_id từ form
    exam_id = request.form.get('exam_id')
    if not exam_id:
        flash('Vui lòng chọn đề thi.', 'error')
        return redirect(url_for('class_detail', class_id=class_id))

    conn = get_db()
    try:
        # Kiểm tra exam đã được thêm chưa
        existing = conn.execute('''
            SELECT 1 FROM class_exams 
            WHERE class_id = ? AND exam_id = ?
        ''', (class_id, exam_id)).fetchone()
        if existing:
            flash('Đề thi đã được thêm vào lớp này', 'info')
            return redirect(url_for('class_detail', class_id=class_id))

        # Thêm exam vào lớp
        conn.execute('''
            INSERT INTO class_exams (class_id, exam_id, added_by, add_time) 
            VALUES (?, ?, ?, datetime('now'))
        ''', (class_id, exam_id, session['username']))
        conn.commit()
        flash('Đã thêm đề thi vào lớp học', 'success')
    finally:
        conn.close()
    return redirect(url_for('class_detail', class_id=class_id))

@app.route('/class/remove_member/<int:class_id>/<int:user_id>', methods=['POST'])
def remove_member(class_id, user_id):
    # Kiểm tra quyền giáo viên/admin
    auth_check = teacher_or_admin_required()
    if auth_check:
        return auth_check

    conn = get_db()
    try:
        # Kiểm tra xem người dùng có phải là thành viên của lớp không
        is_member = conn.execute('''
            SELECT 1 FROM class_members 
            WHERE class_id = ? AND user_id = ?
        ''', (class_id, user_id)).fetchone()
        
        if not is_member:
            flash('Người dùng không phải là thành viên của lớp này', 'error')
            return redirect(url_for('class_detail', class_id=class_id))

        # Xóa thành viên khỏi lớp
        conn.execute('''
            DELETE FROM class_members 
            WHERE class_id = ? AND user_id = ?
        ''', (class_id, user_id))
        conn.commit()
        flash('Đã xóa học sinh khỏi lớp', 'success')
    finally:
        conn.close()
    
    return redirect(url_for('class_detail', class_id=class_id))

@app.route('/class/attendance_report/<int:class_id>')
def attendance_report(class_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    conn = get_db()
    try:
        # Lấy thông tin lớp học - sửa lại câu truy vấn
        class_info = conn.execute('''
            SELECT c.*, u.username as creator_username 
            FROM classes c 
            JOIN users u ON c.creator_id = u.id 
            WHERE c.id = ?
        ''', (class_id,)).fetchone()

        if not class_info:
            flash('Không tìm thấy lớp học', 'error')
            return redirect(url_for('class_page'))

        # Lấy danh sách học sinh trong lớp
        students = conn.execute('''
            SELECT u.id, u.fullname, u.username
            FROM class_members cm
            JOIN users u ON cm.user_id = u.id
            WHERE cm.class_id = ? AND cm.role = 'student'
        ''', (class_id,)).fetchall()

        # Lấy tất cả các ngày điểm danh
        dates = conn.execute('''
            SELECT DISTINCT date
            FROM class_attendance
            WHERE class_id = ?
            ORDER BY date
        ''', (class_id,)).fetchall()

        # Lấy dữ liệu điểm danh cho từng học sinh
        attendance_data = {}
        for student in students:
            attendance_data[student['id']] = {
                'attendance': {},
                'total_days': 0,
                'total_present': 0
            }

            # Lấy điểm danh của học sinh này
            records = conn.execute('''
                SELECT date, status
                FROM class_attendance
                WHERE class_id = ? AND user_id = ?
                ORDER BY date
            ''', (class_id, student['id'])).fetchall()

            for record in records:
                attendance_data[student['id']]['attendance'][record['date']] = record['status']
                attendance_data[student['id']]['total_days'] += 1
                if record['status'] == 'present':
                    attendance_data[student['id']]['total_present'] += 1

        return render_template('attendance_report.html',
                             class_info=class_info,
                             students=students,
                             dates=dates,
                             attendance_data=attendance_data)

    except Exception as e:
        flash(f'Lỗi khi tải báo cáo điểm danh: {str(e)}', 'error')
        return redirect(url_for('class_detail', class_id=class_id))
    finally:
        conn.close()

if __name__ == '__main__':
    app.run(debug=True)