from flask import Blueprint, render_template, session, redirect, url_for, g, abort, flash
import sqlite3

admin_bp = Blueprint('admin', __name__, url_prefix='/admin', template_folder='templates/admin')

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect('../database/database.db')
        g.db.row_factory = sqlite3.Row
    return g.db

def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def admin_required():
    if 'username' not in session or not session.get('is_admin'):
        abort(403)
    return None

@admin_bp.before_request
def before_request():
    auth_check = admin_required()
    if auth_check:
        return auth_check
    g.db = get_db()

@admin_bp.teardown_request
def teardown_request(exception):
    close_db()

@admin_bp.errorhandler(403)
def forbidden(error):
    return render_template('admin/forbidden.html'), 403

@admin_bp.route('/')
def index():
    return render_template('admin/index.html')

@admin_bp.route('/users')
def manage_users():
    db = get_db()
    users = db.execute('SELECT id, username, fullname, email, is_admin, is_teacher FROM users').fetchall()
    return render_template('admin/manage_users.html', users=users)

@admin_bp.route('/promote_user/<username>')
def promote_user(username):
    db = get_db()
    db.execute('UPDATE users SET is_admin = 1, is_teacher = 1 WHERE username = ?', (username,))
    db.commit()
    flash(f'Đã nâng cấp {username} thành admin.', 'success')
    return redirect(url_for('admin.manage_users'))

@admin_bp.route('/demote_user/<username>')
def demote_user(username):
    db = get_db()
    db.execute('UPDATE users SET is_admin = 0 WHERE username = ?', (username,))
    db.commit()
    flash(f'Đã hạ cấp {username} khỏi quyền admin.', 'success')
    return redirect(url_for('admin.manage_users'))

@admin_bp.route('/promote_teacher/<username>')
def promote_teacher(username):
    db = get_db()
    db.execute('UPDATE users SET is_teacher = 1 WHERE username = ?', (username,))
    db.commit()
    flash(f'Đã nâng cấp {username} thành giáo viên.', 'success')
    return redirect(url_for('admin.manage_users'))

@admin_bp.route('/demote_teacher/<username>')
def demote_teacher(username):
    db = get_db()
    db.execute('UPDATE users SET is_teacher = 0 WHERE username = ? AND is_admin = 0', (username,))
    db.commit()
    flash(f'Đã hạ cấp {username} khỏi quyền giáo viên.', 'success')
    return redirect(url_for('admin.manage_users'))

@admin_bp.route('/stats')
def view_stats():
    db = get_db()
    user_count = db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    return render_template('admin/stats.html', user_count=user_count)