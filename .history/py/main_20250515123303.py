from flask import Flask, render_template, url_for, session, redirect, request, flash, get_flashed_messages, g
import sqlite3
import re
import os
import csv
from admin import admin_bp
from slugify import slugify
import logging

app = Flask(__name__, template_folder='../templates', static_folder='../static')
DATABASE = '/database/database.db'
UPLOAD_FOLDER = 'exams_csv'
ALLOWED_EXTENSIONS = {'csv'}
app.secret_key = 'your_secret_key'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

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
    conn = get_db()
    db = conn.cursor()
    exam = db.execute('SELECT file_path, name FROM exams WHERE id = ?', (exam_id,)).fetchone()
    conn.close()

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

    session['exam_result'] = {
        'exam_id': exam_id,
        'exam_name': exam['name'],
        'user_answers': user_answers,
        'correct_answers': correct_answers,
        'score': score,
        'total_questions': total_questions,
        'points': points
    }

    return render_template('exam_result.html', points=points, score=score, total=total_questions, exam_name=exam['name'])

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

app.register_blueprint(admin_bp)

if __name__ == '__main__':
    app.run(debug=True)