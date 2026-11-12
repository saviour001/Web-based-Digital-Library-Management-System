from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, Response, jsonify, abort
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import HTTPException
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed
from wtforms import StringField, PasswordField, SubmitField, SelectField, BooleanField, TextAreaField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from io import BytesIO, StringIO
import csv
from datetime import datetime
from flask_paginate import Pagination, get_page_args
from flask_wtf.csrf import generate_csrf   # ← ADD THIS LINE HERE
import os

def allowed_file(filename, allowed_extensions):
    """Check if a filename has an allowed extension."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in allowed_extensions

app = Flask(__name__)
# Run correctly behind a reverse proxy / hosting router (Render, Railway, Heroku, Nginx)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

# Core config is environment-driven so the app is safe to host anywhere.
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-me-in-production-please')

_db_url = os.environ.get('DATABASE_URL', 'sqlite:///library.db')
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Scalability: resilient connection pool (works for SQLite and Postgres/MySQL)
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True, 'pool_recycle': 280}
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['ALLOWED_IMAGES'] = {'jpg', 'png', 'jpeg'}
app.config['ALLOWED_PDF'] = {'pdf'}
app.config['ADMIN_EMAIL'] = os.environ.get('ADMIN_EMAIL', 'admin@fpnasarawa.edu.ng')
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB upload cap
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# Set SESSION_COOKIE_SECURE=1 in the environment once served over HTTPS
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', '0') == '1'

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Create upload folders
for folder in ['passports', 'receipts', 'staff_ids', 'books', 'notes', 'covers']:
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], folder), exist_ok=True)

# ────────────────────────────────────────────────
# MODELS
# ────────────────────────────────────────────────

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, index=True)  # student / lecturer / admin
    approved = db.Column(db.Boolean, default=False)
    last_login = db.Column(db.DateTime)
    last_logout = db.Column(db.DateTime)

    # ─── NEW FIELD ───
    passport_filename = db.Column(db.String(255))  # can be null if no photo uploaded
	
	

class StudentProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    matric_number = db.Column(db.String(50), unique=True, nullable=False)
    level = db.Column(db.String(20), nullable=False)
    passport_filename = db.Column(db.String(255))
    receipt_filename = db.Column(db.String(255))

class LecturerProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    passport_filename = db.Column(db.String(255))
    staff_id_filename = db.Column(db.String(255))

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)

class Resource(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    filename = db.Column(db.String(255), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False, index=True)
    uploader_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    resource_type = db.Column(db.String(20), nullable=False, index=True)  # book / note
    upload_date = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    view_count = db.Column(db.Integer, default=0)
    download_count = db.Column(db.Integer, default=0)
    author = db.Column(db.String(200))
    year = db.Column(db.String(10))
    edition = db.Column(db.String(50))
    cover_filename = db.Column(db.String(255))
    license = db.Column(db.String(255))
    source_url = db.Column(db.String(500))

    category = db.relationship('Category', backref='resources')
    uploader = db.relationship('User', backref='resources')

class ActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    resource_id = db.Column(db.Integer, db.ForeignKey('resource.id'))
    action = db.Column(db.String(50), nullable=False, index=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # ADD THESE TWO LINES
    user = db.relationship('User', backref='activity_logs')
    resource = db.relationship('Resource', backref='activity_logs')

class Favorite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    resource_id = db.Column(db.Integer, db.ForeignKey('resource.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_id', 'resource_id', name='uq_fav_user_resource'),)

    user = db.relationship('User', backref='favorites')
    resource = db.relationship('Resource', backref='favorited_by')

class AllowedStudent(db.Model):
    """Whitelist of students permitted to self-register (no admin approval)."""
    id = db.Column(db.Integer, primary_key=True)
    matric_number = db.Column(db.String(50), unique=True, nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    level = db.Column(db.String(20))
    department = db.Column(db.String(100))
    email = db.Column(db.String(120))

class AllowedLecturer(db.Model):
    """Whitelist of lecturers permitted to self-register (no admin approval)."""
    id = db.Column(db.Integer, primary_key=True)
    staff_id = db.Column(db.String(50), unique=True, nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    department = db.Column(db.String(100))
    email = db.Column(db.String(120))
# ────────────────────────────────────────────────
# DB INIT + DEFAULTS
# ────────────────────────────────────────────────

with app.app_context():
    db.create_all()

    # Default admin
    if not User.query.filter_by(username='admin').first():
        admin = User(
            full_name='Administrator',
            username='admin',
            email=app.config['ADMIN_EMAIL'],
            password_hash=generate_password_hash('admin2025'),
            role='admin',
            approved=True
        )
        db.session.add(admin)
        db.session.commit()
        print("Default admin created → username: admin | password: admin2025")

    # Default categories
    if Category.query.count() == 0:
        for name in [
            'Software Engineering', 'Database Systems', 'Web Development',
            'Networking & Security', 'Programming Languages', 'General IT',
            'Handout/Lecture Note', 'Past Questions'
        ]:
            db.session.add(Category(name=name))
        db.session.commit()

    # Seed registration whitelist (sample rows; manage these from Admin > Allow List)
    if AllowedStudent.query.count() == 0:
        for m, n, lv, d in [
            ('FPN/CS/22/0001', 'Aisha Bello', 'ND2', 'Computer Science'),
            ('FPN/CS/22/0002', 'Chinedu Okafor', 'ND2', 'Computer Science'),
            ('FPN/SE/23/0010', 'Grace Adeyemi', 'HND1', 'Software Engineering'),
            ('FPN/SE/23/0011', 'Ibrahim Musa', 'HND1', 'Software Engineering'),
        ]:
            db.session.add(AllowedStudent(matric_number=m, full_name=n, level=lv, department=d))
        db.session.commit()
    if AllowedLecturer.query.count() == 0:
        for s, n, d in [
            ('FPN/STAFF/SE/001', 'Dr. Samuel Eze', 'Software Engineering'),
            ('FPN/STAFF/CS/002', 'Mrs. Fatima Sani', 'Computer Science'),
        ]:
            db.session.add(AllowedLecturer(staff_id=s, full_name=n, department=d))
        db.session.commit()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ────────────────────────────────────────────────
# FORMS
# ────────────────────────────────────────────────

class LoginForm(FlaskForm):
    username = StringField('Email or Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

class PasswordChangeForm(FlaskForm):
    old_password = PasswordField('Old Password', validators=[DataRequired()])
    new_password = PasswordField('New Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm New Password', validators=[DataRequired(), EqualTo('new_password')])
    submit = SubmitField('Change Password')

class StudentRegForm(FlaskForm):
    full_name = StringField('Full Name', validators=[DataRequired()])
    matric_number = StringField('Matric Number', validators=[DataRequired()])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    level = SelectField('Level', choices=[('ND1', 'ND1'), ('ND2', 'ND2'), ('HND1', 'HND1'), ('HND2', 'HND2')], validators=[DataRequired()])
    passport = FileField('Passport Photo', validators=[FileRequired(), FileAllowed(app.config['ALLOWED_IMAGES'], 'Images only')])
    receipt = FileField('School Fee Receipt', validators=[FileRequired(), FileAllowed(app.config['ALLOWED_PDF'], 'PDF only')])
    accept_terms = BooleanField('I accept the Terms and Conditions', validators=[DataRequired()])
    submit = SubmitField('Register')

class LecturerRegForm(FlaskForm):
    full_name = StringField('Full Name', validators=[DataRequired()])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    passport = FileField('Passport Photo', validators=[FileRequired(), FileAllowed(app.config['ALLOWED_IMAGES'], 'Images only')])
    staff_id = FileField('Staff ID Card', validators=[FileRequired(), FileAllowed(app.config['ALLOWED_IMAGES'], 'Images only')])
    accept_terms = BooleanField('I accept the Terms and Conditions', validators=[DataRequired()])
    submit = SubmitField('Register')

class UploadForm(FlaskForm):
    title = StringField('Title', validators=[DataRequired(), Length(max=200)])
    description = TextAreaField('Description')
    file = FileField('PDF File', validators=[FileRequired(), FileAllowed(app.config['ALLOWED_PDF'], 'PDF files only')])
    category = SelectField('Category', coerce=int, validators=[DataRequired()])
    submit = SubmitField('Upload')

class CategoryForm(FlaskForm):
    name = StringField('Category Name', validators=[DataRequired()])
    submit = SubmitField('Add Category')

class BookUploadForm(FlaskForm):
    title = StringField('Book Title', validators=[DataRequired(), Length(max=200)])
    author = StringField('Author(s)', validators=[Optional(), Length(max=200)])
    edition = StringField('Edition', validators=[Optional(), Length(max=50)])
    year = StringField('Year of Publication', validators=[Optional(), Length(max=10)])
    category = SelectField('Category', coerce=int, validators=[DataRequired()])
    description = TextAreaField('Abstract / Description', validators=[Optional()])
    cover = FileField('Cover Image', validators=[Optional(), FileAllowed(app.config['ALLOWED_IMAGES'], 'Images only (jpg, png)')])
    file = FileField('Book PDF', validators=[FileRequired(), FileAllowed(app.config['ALLOWED_PDF'], 'PDF files only')])
    submit = SubmitField('Publish to Library')

def get_lecturer_allowed_categories():
    # List of category names lecturers are allowed to upload to
    allowed_names = [
        'Past Questions',
		'Handout/Lecture Note'
        # Add more if you want lecturers to upload to specific categories
        # e.g. 'Software Engineering' if you allow it
    ]
    return Category.query.filter(Category.name.in_(allowed_names)).all()
	
# ────────────────────────────────────────────────
# ROUTES
# ────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter(
            (User.username == form.username.data) | (User.email == form.username.data)
        ).first()

        if user and check_password_hash(user.password_hash, form.password.data):
            user.last_login = datetime.utcnow()
            db.session.add(ActivityLog(user_id=user.id, action='login'))
            db.session.commit()
            login_user(user)
            flash('Logged in successfully.', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email/username or password.', 'danger')

    return render_template('login.html', form=form)

@app.route('/logout')
@login_required
def logout():
    current_user.last_logout = datetime.utcnow()
    db.session.add(ActivityLog(user_id=current_user.id, action='logout'))
    db.session.commit()
    logout_user()
    flash('Logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/forgot_password')
def forgot_password():
    flash(f'Reset link sent to your email. If not received in 10 minutes, email {app.config['ADMIN_EMAIL']} for assistance.', 'info')
    return redirect(url_for('login'))

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/register/student', methods=['GET', 'POST'])
def register_student():
    # Step 1: verify matric number against the approved list.
    # Step 2: show the matched details and collect the remaining info, then
    # create the account immediately (no admin approval required).
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        step = request.form.get('step', 'verify')
        matric = (request.form.get('matric_number') or '').strip()
        if not matric:
            flash('Please enter your matriculation number.', 'warning')
            return render_template('register_student.html', step='verify')

        allowed = AllowedStudent.query.filter(
            db.func.lower(AllowedStudent.matric_number) == matric.lower()).first()
        if not allowed:
            flash('This matriculation number is not on the approved list. '
                  'Please contact the library administrator.', 'danger')
            return render_template('register_student.html', step='verify', matric_number=matric)

        if User.query.filter_by(username=allowed.matric_number).first():
            flash('An account already exists for this matriculation number. Please log in instead.', 'info')
            return redirect(url_for('login'))

        if step == 'verify':
            # Details found -> present them and the remaining fields.
            return render_template('register_student.html', step='complete', allowed=allowed)

        # step == 'complete' -> validate and create the account.
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''
        confirm = request.form.get('confirm_password') or ''
        errors = []
        if not email or '@' not in email:
            errors.append('Enter a valid email address.')
        if len(password) < 6:
            errors.append('Password must be at least 6 characters long.')
        if password != confirm:
            errors.append('The two passwords do not match.')
        if email and User.query.filter_by(email=email).first():
            errors.append('That email address is already registered.')

        passport = request.files.get('passport')
        receipt = request.files.get('receipt')
        if not passport or passport.filename == '':
            errors.append('Upload your passport photograph.')
        elif not allowed_file(passport.filename, app.config['ALLOWED_IMAGES']):
            errors.append('Passport must be a JPG or PNG image.')
        if not receipt or receipt.filename == '':
            errors.append('Upload your school fee receipt (PDF).')
        elif not allowed_file(receipt.filename, app.config['ALLOWED_PDF']):
            errors.append('The receipt must be a PDF file.')

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('register_student.html', step='complete', allowed=allowed, form_email=email)

        try:
            user = User(
                full_name=allowed.full_name,
                username=allowed.matric_number,
                email=email,
                password_hash=generate_password_hash(password),
                role='student',
                approved=True
            )
            db.session.add(user)
            db.session.flush()
            p_ext = passport.filename.rsplit('.', 1)[1].lower()
            r_ext = receipt.filename.rsplit('.', 1)[1].lower()
            p_fn = secure_filename(f"passport_{user.id}.{p_ext}")
            r_fn = secure_filename(f"receipt_{user.id}.{r_ext}")
            passport.save(os.path.join(app.config['UPLOAD_FOLDER'], 'passports', p_fn))
            receipt.save(os.path.join(app.config['UPLOAD_FOLDER'], 'receipts', r_fn))
            user.passport_filename = p_fn
            profile = StudentProfile(
                user_id=user.id,
                matric_number=allowed.matric_number,
                level=allowed.level,
                passport_filename=p_fn,
                receipt_filename=r_fn
            )
            db.session.add(profile)
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash('Something went wrong while creating your account. Please try again.', 'danger')
            return render_template('register_student.html', step='verify')

        flash('Account created successfully. You can now log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register_student.html', step='verify')

@app.route('/register/lecturer', methods=['GET', 'POST'])
def register_lecturer():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        step = request.form.get('step', 'verify')
        staff_id = (request.form.get('staff_id') or '').strip()
        if not staff_id:
            flash('Please enter your staff ID.', 'warning')
            return render_template('register_lecturer.html', step='verify')

        allowed = AllowedLecturer.query.filter(
            db.func.lower(AllowedLecturer.staff_id) == staff_id.lower()).first()
        if not allowed:
            flash('This staff ID is not on the approved list. '
                  'Please contact the library administrator.', 'danger')
            return render_template('register_lecturer.html', step='verify', staff_id=staff_id)

        username = allowed.staff_id.lower().replace(' ', '_').replace('/', '_')
        if User.query.filter_by(username=username).first():
            flash('An account already exists for this staff ID. Please log in instead.', 'info')
            return redirect(url_for('login'))

        if step == 'verify':
            return render_template('register_lecturer.html', step='complete', allowed=allowed)

        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''
        confirm = request.form.get('confirm_password') or ''
        errors = []
        if not email or '@' not in email:
            errors.append('Enter a valid email address.')
        if len(password) < 6:
            errors.append('Password must be at least 6 characters long.')
        if password != confirm:
            errors.append('The two passwords do not match.')
        if email and User.query.filter_by(email=email).first():
            errors.append('That email address is already registered.')

        passport = request.files.get('passport')
        staff_card = request.files.get('staff_id_card')
        if not passport or passport.filename == '':
            errors.append('Upload your passport photograph.')
        elif not allowed_file(passport.filename, app.config['ALLOWED_IMAGES']):
            errors.append('Passport must be a JPG or PNG image.')
        if not staff_card or staff_card.filename == '':
            errors.append('Upload a photo of your staff ID card.')
        elif not allowed_file(staff_card.filename, app.config['ALLOWED_IMAGES']):
            errors.append('Staff ID card must be a JPG or PNG image.')

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('register_lecturer.html', step='complete', allowed=allowed, form_email=email)

        try:
            user = User(
                full_name=allowed.full_name,
                username=username,
                email=email,
                password_hash=generate_password_hash(password),
                role='lecturer',
                approved=True
            )
            db.session.add(user)
            db.session.flush()
            p_ext = passport.filename.rsplit('.', 1)[1].lower()
            s_ext = staff_card.filename.rsplit('.', 1)[1].lower()
            p_fn = secure_filename(f"passport_{user.id}.{p_ext}")
            s_fn = secure_filename(f"staffid_{user.id}.{s_ext}")
            passport.save(os.path.join(app.config['UPLOAD_FOLDER'], 'passports', p_fn))
            staff_card.save(os.path.join(app.config['UPLOAD_FOLDER'], 'staff_ids', s_fn))
            user.passport_filename = p_fn
            profile = LecturerProfile(
                user_id=user.id,
                passport_filename=p_fn,
                staff_id_filename=s_fn
            )
            db.session.add(profile)
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash('Something went wrong while creating your account. Please try again.', 'danger')
            return render_template('register_lecturer.html', step='verify')

        flash('Account created successfully. You can now log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register_lecturer.html', step='verify')

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'admin':
        user_count = User.query.count()
        student_count = User.query.filter_by(role='student', approved=True).count()
        lecturer_count = User.query.filter_by(role='lecturer', approved=True).count()
        book_count = Resource.query.filter_by(resource_type='book').count()
        note_count = Resource.query.filter_by(resource_type='note').count()
        category_counts = db.session.query(Category.name, db.func.count(Resource.id)).join(Resource).group_by(Category.name).all()

        # Most viewed and downloaded books (top 5)
        most_viewed = Resource.query.order_by(Resource.view_count.desc()).limit(5).all()
        most_downloaded = Resource.query.order_by(Resource.download_count.desc()).limit(5).all()

        # Latest user activities (paginated)
        page, per_page, offset = get_page_args(page_parameter='page', per_page_parameter='per_page')
        total = ActivityLog.query.count()
        activities = ActivityLog.query.order_by(ActivityLog.timestamp.desc()).offset(offset).limit(per_page).all()
        pagination = Pagination(page=page, per_page=per_page, total=total, css_framework='bootstrap5')

        return render_template('admin_dashboard.html',
                               user_count=user_count,
                               student_count=student_count,
                               lecturer_count=lecturer_count,
                               book_count=book_count,
                               note_count=note_count,
                               category_counts=category_counts,
                               most_viewed=most_viewed,
                               most_downloaded=most_downloaded,
                               activities=activities,
                               pagination=pagination)

    # Student / Lecturer Dashboard
    profile_class = StudentProfile if current_user.role == 'student' else LecturerProfile
    profile = profile_class.query.filter_by(user_id=current_user.id).first()
    passport_url = url_for('static', filename=f'uploads/passports/{profile.passport_filename}') if profile and profile.passport_filename else None

    # My activities
    viewed_books = ActivityLog.query.filter_by(user_id=current_user.id, action='view').order_by(ActivityLog.timestamp.desc()).limit(10).all()
    downloaded_books = ActivityLog.query.filter_by(user_id=current_user.id, action='download').order_by(ActivityLog.timestamp.desc()).limit(10).all()
    recent_logs = ActivityLog.query.filter_by(user_id=current_user.id).order_by(ActivityLog.timestamp.desc()).limit(15).all()
    login_logs = ActivityLog.query.filter_by(user_id=current_user.id, action='login').order_by(ActivityLog.timestamp.desc()).limit(10).all()
    logout_logs = ActivityLog.query.filter_by(user_id=current_user.id, action='logout').order_by(ActivityLog.timestamp.desc()).limit(10).all()

    return render_template('user_dashboard.html',
                           passport_url=passport_url,
                           recent_logs=recent_logs,
                           viewed_books=viewed_books,
                           downloaded_books=downloaded_books,
                           login_logs=login_logs,
                           logout_logs=logout_logs)

@app.route('/password/change', methods=['GET', 'POST'])
@login_required
def change_password():
    form = PasswordChangeForm()
    if form.validate_on_submit():
        if check_password_hash(current_user.password_hash, form.old_password.data):
            current_user.password_hash = generate_password_hash(form.new_password.data)
            db.session.commit()
            flash('Password updated successfully.', 'success')
            return redirect(url_for('dashboard'))
        flash('Old password incorrect.', 'danger')
    return render_template('change_password.html', form=form)


@app.route('/admin/approvals', methods=['GET', 'POST'])
@login_required
def admin_approvals():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))

    pending_students = User.query.filter_by(role='student', approved=False).all()
    pending_lecturers = User.query.filter_by(role='lecturer', approved=False).all()
    student_profiles = {p.user_id: p for p in StudentProfile.query.all()}
    lecturer_profiles = {p.user_id: p for p in LecturerProfile.query.all()}

    if request.method == 'POST':
        user_id = int(request.form['user_id'])
        role = request.form['role']
        action = request.form['action']
        user = User.query.get(user_id)

        if action == 'approve':
            user.approved = True
            flash(f'{user.full_name} ({role}) approved.', 'success')
        elif action == 'reject':
            db.session.delete(user)
            profile_dict = student_profiles if role == 'student' else lecturer_profiles
            if user_id in profile_dict:
                db.session.delete(profile_dict[user_id])
            flash(f'{user.full_name} ({role}) rejected and removed.', 'warning')

        db.session.commit()
        return redirect(url_for('admin_approvals'))

    return render_template('admin_approvals.html', pending_students=pending_students, pending_lecturers=pending_lecturers,
                           student_profiles=student_profiles, lecturer_profiles=lecturer_profiles)

@app.route('/admin/add_category', methods=['GET', 'POST'])
@login_required
def add_category():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))

    form = CategoryForm()
    if form.validate_on_submit():
        if Category.query.filter_by(name=form.name.data).first():
            flash('Category already exists.', 'warning')
        else:
            cat = Category(name=form.name.data)
            db.session.add(cat)
            db.session.commit()
            flash('Category added.', 'success')
        return redirect(url_for('add_category'))

    categories = Category.query.order_by(Category.name).all()
    return render_template('add_category.html', form=form, categories=categories)

@app.route('/admin/delete_category/<int:category_id>', methods=['POST'])
@login_required
def delete_category(category_id):
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))

    category = Category.query.get_or_404(category_id)

    # Protect the fallback bucket so resources always have a home.
    if category.name == 'Uncategorized':
        flash('The "Uncategorized" category is protected and cannot be deleted.', 'warning')
        return redirect(url_for('add_category'))

    # Reassign any books/notes in this category to "Uncategorized" before deleting,
    # so no resource is ever orphaned (keeps the library scalable and safe).
    resources = Resource.query.filter_by(category_id=category.id).all()
    moved = len(resources)
    if resources:
        fallback = Category.query.filter_by(name='Uncategorized').first()
        if not fallback:
            fallback = Category(name='Uncategorized')
            db.session.add(fallback)
            db.session.commit()
        for r in resources:
            r.category_id = fallback.id

    name = category.name
    db.session.delete(category)
    db.session.commit()

    if moved:
        flash(f'Category "{name}" deleted. {moved} resource(s) moved to "Uncategorized".', 'success')
    else:
        flash(f'Category "{name}" deleted.', 'success')
    return redirect(url_for('add_category'))

@app.route('/upload/book', methods=['GET', 'POST'])
@login_required
def upload_book():
    if current_user.role != 'admin':
        flash('Only admins can upload books.', 'danger')
        return redirect(url_for('dashboard'))

    form = BookUploadForm()
    form.category.choices = [(c.id, c.name) for c in Category.query.order_by(Category.name).all()]

    if form.validate_on_submit():
        file = form.file.data
        if file.mimetype == 'application/pdf':
            import time
            slug = secure_filename(form.title.data.replace(' ', '_')) or 'book'
            stamp = int(time.time())
            filename = f"book_{stamp}_{slug}.pdf"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'books', filename))

            # Optional cover image for a richer, academic-style catalogue.
            cover_rel = None
            cover = form.cover.data
            if cover and getattr(cover, 'filename', ''):
                if allowed_file(cover.filename, app.config['ALLOWED_IMAGES']):
                    ext = cover.filename.rsplit('.', 1)[1].lower()
                    cover_fn = f"cover_{stamp}_{slug}.{ext}"
                    cover.save(os.path.join(app.config['UPLOAD_FOLDER'], 'covers', cover_fn))
                    cover_rel = f"covers/{cover_fn}"
                else:
                    flash('Cover image must be JPG or PNG. Book saved without a cover.', 'warning')

            resource = Resource(
                title=form.title.data,
                description=form.description.data,
                filename=f"books/{filename}",
                category_id=form.category.data,
                uploader_id=current_user.id,
                resource_type='book',
                author=(form.author.data or None),
                year=(form.year.data or None),
                edition=(form.edition.data or None),
                cover_filename=cover_rel
            )
            db.session.add(resource)
            db.session.commit()
            flash('Book published to the library successfully.', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Only PDF files are allowed.', 'danger')

    return render_template('upload_book.html', form=form)

@app.route('/upload/note', methods=['GET', 'POST'])
@login_required
def upload_note():
    if current_user.role != 'lecturer':
        flash('Only lecturers can upload notes.', 'danger')
        return redirect(url_for('dashboard'))

    form = UploadForm()
    
    # Restrict category choices to only allowed ones for lecturers
    allowed_categories = get_lecturer_allowed_categories()
    if not allowed_categories:
        flash('No categories available for lecturer uploads. Contact admin.', 'danger')
        return redirect(url_for('dashboard'))
    
    form.category.choices = [(c.id, c.name) for c in allowed_categories]

    if form.validate_on_submit():
        file = form.file.data
        if file.mimetype == 'application/pdf':
            filename = secure_filename(f"note_{form.title.data.replace(' ', '_')}.pdf")
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'notes', filename))

            resource = Resource(
                title=form.title.data,
                description=form.description.data,
                filename=f"notes/{filename}",
                category_id=form.category.data,
                uploader_id=current_user.id,
                resource_type='note'
            )
            db.session.add(resource)
            db.session.commit()
            flash('Note uploaded successfully.', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Only PDF files are allowed.', 'danger')

    return render_template('upload_note.html', form=form)

@app.route('/categories')
@login_required
def categories():
    return render_template('categories.html', categories=Category.query.all())

@app.route('/category/<int:category_id>')
@login_required
def category_detail(category_id):
    category = Category.query.get_or_404(category_id)
    resources = Resource.query.filter_by(category_id=category_id).all()
    return render_template('books_by_category.html', category=category, resources=resources)

@app.route('/search')
@login_required
def search():
    query = request.args.get('q', '').strip()
    
    if not query:
        # Optional: show recent/popular resources if no query
        results = Resource.query.order_by(Resource.upload_date.desc()).limit(12).all()
        return render_template('search.html', results=results, query='', message="Showing recent resources")
    
    # Real search: title OR description (add author if you have it later)
    results = Resource.query.filter(
        db.or_(
            Resource.title.ilike(f'%{query}%'),
            Resource.description.ilike(f'%{query}%')
        )
    ).order_by(Resource.upload_date.desc()).all()
    
    message = f"Found {len(results)} result{'s' if len(results) != 1 else ''} for \"{query}\"" if results else f"No results found for \"{query}\". Try different keywords."
    
    return render_template('search.html', results=results, query=query, message=message)	

@app.route('/pdf/<path:filename>')
@login_required
def serve_pdf(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename, mimetype='application/pdf', as_attachment=False)

@app.route('/view/<int:resource_id>')
@login_required
def view_resource(resource_id):
    resource = Resource.query.get_or_404(resource_id)
    resource.view_count += 1
    db.session.add(ActivityLog(user_id=current_user.id, resource_id=resource_id, action='view'))
    db.session.commit()
    return send_from_directory(app.config['UPLOAD_FOLDER'], resource.filename, mimetype='application/pdf', as_attachment=False)

@app.route('/edit/<int:resource_id>', methods=['GET', 'POST'])
@login_required
def edit_resource(resource_id):
    if current_user.role != 'admin':
        flash('Only admins can edit resources.', 'danger')
        return redirect(url_for('dashboard'))

    resource = Resource.query.get_or_404(resource_id)
    form = UploadForm()
    form.category.choices = [(c.id, c.name) for c in Category.query.all()]

    if request.method == 'GET':
        form.title.data = resource.title
        form.description.data = resource.description
        form.category.data = resource.category_id

    if form.validate_on_submit():
        resource.title = form.title.data
        resource.description = form.description.data
        resource.category_id = form.category.data

        if form.file.data and form.file.data.mimetype == 'application/pdf':
            old_path = os.path.join(app.config['UPLOAD_FOLDER'], resource.filename)
            if os.path.exists(old_path):
                os.remove(old_path)
            filename = secure_filename(f"{resource.resource_type}_{form.title.data.replace(' ', '_')}.pdf")
            form.file.data.save(os.path.join(app.config['UPLOAD_FOLDER'], resource.resource_type + 's', filename))
            resource.filename = f"{resource.resource_type}s/{filename}"

        db.session.commit()
        flash('Resource updated.', 'success')
        return redirect(url_for('category_detail', category_id=resource.category_id))

    return render_template('edit_resource.html', form=form, resource=resource)

@app.route('/delete/<int:resource_id>', methods=['POST'])
@login_required
def delete_resource(resource_id):
    if current_user.role != 'admin':
        flash('Only admins can delete resources.', 'danger')
        return redirect(url_for('dashboard'))

    resource = Resource.query.get_or_404(resource_id)
    category_id = resource.category_id

    file_path = os.path.join(app.config['UPLOAD_FOLDER'], resource.filename)
    if os.path.exists(file_path):
        os.remove(file_path)

    db.session.delete(resource)
    db.session.commit()

    flash('Resource deleted.', 'success')
    return redirect(url_for('category_detail', category_id=category_id))

@app.route('/download/<int:resource_id>')
@login_required
def download(resource_id):
    resource = Resource.query.get_or_404(resource_id)
    resource.download_count += 1
    db.session.add(ActivityLog(user_id=current_user.id, resource_id=resource.id, action='download'))
    db.session.commit()
    return send_from_directory(app.config['UPLOAD_FOLDER'], resource.filename, as_attachment=True)	
# ────────────────────────────────────────────────
# REPORTS & PDF GENERATION (NEW - added here)
# ────────────────────────────────────────────────

@app.route('/admin/report/users')
@login_required
def report_users():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    users = User.query.all()
    return render_template('report_users.html', users=users)

@app.route('/admin/report/users/pdf')
@login_required
def report_users_pdf():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    users = User.query.all()

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph("All Registered Users Report", styles['Heading1']))
    elements.append(Spacer(1, 12))

    data = [['Full Name', 'Username', 'Email', 'Role', 'Approved']]
    for user in users:
        data.append([
            user.full_name,
            user.username,
            user.email,
            user.role.capitalize(),
            'Yes' if user.approved else 'No'
        ])

    table = Table(data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.grey),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('BACKGROUND', (0,1), (-1,-1), colors.beige),
        ('GRID', (0,0), (-1,-1), 1, colors.black)
    ]))

    elements.append(table)
    doc.build(elements)

    buffer.seek(0)
    return Response(buffer, mimetype='application/pdf',
                    headers={'Content-Disposition': 'attachment;filename=all_users_report.pdf'})

@app.route('/admin/report/user_activity/<int:user_id>')
@login_required
def report_user_activity(user_id):
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    user = User.query.get_or_404(user_id)
    activities = ActivityLog.query.filter_by(user_id=user_id).order_by(ActivityLog.timestamp.desc()).all()
    return render_template('report_user_activity.html', user=user, activities=activities)

@app.route('/admin/report/user_activity/pdf/<int:user_id>')
@login_required
def report_user_activity_pdf(user_id):
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    user = User.query.get_or_404(user_id)
    activities = ActivityLog.query.filter_by(user_id=user_id).order_by(ActivityLog.timestamp.desc()).all()

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph(f"Activity Report for {user.full_name}", styles['Heading1']))
    elements.append(Spacer(1, 12))

    data = [['Time', 'Action', 'Resource']]
    for act in activities:
        time = act.timestamp.strftime('%Y-%m-%d %H:%M:%S')
        action = act.action.capitalize()
        resource = act.resource.title if act.resource else '-'
        data.append([time, action, resource])

    table = Table(data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.grey),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('BACKGROUND', (0,1), (-1,-1), colors.beige),
        ('GRID', (0,0), (-1,-1), 1, colors.black)
    ]))

    elements.append(table)
    doc.build(elements)

    buffer.seek(0)
    return Response(buffer, mimetype='application/pdf',
                    headers={'Content-Disposition': f'attachment;filename=user_activity_{user.username}.pdf'})

@app.route('/my/report/usage/pdf')
@login_required
def my_usage_pdf():
    activities = ActivityLog.query.filter_by(user_id=current_user.id).order_by(ActivityLog.timestamp.desc()).all()

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph(f"My Library Usage Report - {current_user.full_name}", styles['Heading1']))
    elements.append(Spacer(1, 12))

    data = [['Time', 'Action', 'Resource']]
    for act in activities:
        time = act.timestamp.strftime('%Y-%m-%d %H:%M:%S')
        action = act.action.capitalize()
        resource = act.resource.title if act.resource else '-'
        data.append([time, action, resource])

    table = Table(data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.grey),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ('BACKGROUND', (0,1), (-1,-1), colors.beige),
        ('GRID', (0,0), (-1,-1), 1, colors.black)
    ]))

    elements.append(table)
    doc.build(elements)

    buffer.seek(0)
    return Response(buffer, mimetype='application/pdf',
                    headers={'Content-Disposition': 'attachment;filename=my_library_usage.pdf'})

# ────────────────────────────────────────────────
# ADMIN: RESET USER PASSWORD
# ────────────────────────────────────────────────
@app.route('/admin/reset_user_password/<int:user_id>', methods=['GET', 'POST'])
@login_required
def admin_reset_user_password(user_id):
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))

    user = User.query.get_or_404(user_id)
    if user.role == 'admin' and user.id != current_user.id:
        flash('Cannot reset another admin password.', 'danger')
        return redirect(url_for('admin_approvals'))

    if request.method == 'POST':
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        if not new_password or len(new_password) < 6:
            flash('Password must be at least 6 characters.', 'danger')
        elif new_password != confirm_password:
            flash('Passwords do not match.', 'danger')
        else:
            user.password_hash = generate_password_hash(new_password)
            db.session.commit()
            flash(f'Password reset successfully for {user.full_name}.', 'success')
            return redirect(url_for('admin_approvals'))

    return render_template('admin_reset_password.html', user=user)

# ────────────────────────────────────────────────
# ADMIN: UPDATE OWN PASSPORT PHOTO
# ─────────────────���──────────────────────────────
@app.route('/admin/update_passport', methods=['GET', 'POST'])
@login_required
def admin_update_passport():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        if 'passport' not in request.files:
            flash('No file selected.', 'danger')
            return redirect(request.url)

        file = request.files['passport']
        if file.filename == '':
            flash('No file selected.', 'danger')
            return redirect(request.url)

        # Validate file type
        if file and allowed_file(file.filename, app.config['ALLOWED_IMAGES']):
            # Get extension safely
            extension = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
            # Secure filename with user ID and timestamp to avoid overwrites
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            filename = secure_filename(f"admin_passport_{current_user.id}_{timestamp}.{extension}")
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], 'passports', filename)

            # Optional: delete old passport if exists
            if current_user.passport_filename:
                old_path = os.path.join(app.config['UPLOAD_FOLDER'], current_user.passport_filename)
                if os.path.exists(old_path):
                    os.remove(old_path)

            # Save new file
            file.save(save_path)

            # Store relative path (without 'static/')
            current_user.passport_filename = f"uploads/passports/{filename}"
            db.session.commit()

            flash('Passport photo updated successfully.', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid file type. Only JPG, JPEG, or PNG allowed.', 'danger')

    return render_template('admin_update_passport.html')

# ────────────────────────────────────────────────
# ADMIN: MANAGE USERS (Search, Reset Password, Delete)
# ────────────────────────────────────────────────

from flask_paginate import Pagination, get_page_args  # already imported

@app.route('/admin/manage_users', methods=['GET', 'POST'])
@login_required
def admin_manage_users():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))

    search_term = request.form.get('search', request.args.get('search', '')).strip()

    query = User.query
    if search_term:
        query = query.filter(
            (User.email.ilike(f'%{search_term}%')) |
            (User.username.ilike(f'%{search_term}%')) |
            (User.full_name.ilike(f'%{search_term}%'))
        )

    page, per_page, offset = get_page_args(page_parameter='page', per_page_parameter='per_page')
    total = query.count()
    users = query.order_by(User.full_name).offset(offset).limit(per_page).all()
    pagination = Pagination(page=page, per_page=per_page, total=total, css_framework='bootstrap5')

    return render_template('admin_manage_users.html',
                           users=users,
                           search_term=search_term,
                           pagination=pagination,
                           total_users=User.query.count(),
                           csrf_token=generate_csrf())  # ← ADD THIS LINE

@app.route('/admin/reset_password/<int:user_id>', methods=['POST'])
@login_required
def admin_reset_password(user_id):
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('admin_manage_users'))

    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash('You cannot reset your own password.', 'danger')
        return redirect(url_for('admin_manage_users'))

    # Hardcoded default password
    default_password = '123456789'
    
    # Force fresh user object
    user = db.session.query(User).filter(User.id == user_id).first()
    
    print(f"[RESET] Resetting password for user ID {user.id} ({user.full_name}) to {default_password}")
    
    user.password_hash = generate_password_hash(default_password)
    db.session.commit()

    # Optional log
    db.session.add(ActivityLog(user_id=current_user.id, action='reset_password', resource_id=user.id))
    db.session.commit()

    flash(f'Password for {user.full_name} has been reset to 123456789. User can log in immediately.', 'success')
    return redirect(url_for('admin_manage_users'))

@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
@login_required
def admin_delete_user(user_id):
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))

    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('You cannot delete your own account.', 'danger')
        return redirect(url_for('admin_manage_users'))

    # Delete profile and uploads safely
    if user.role == 'student':
        profile = StudentProfile.query.filter_by(user_id=user.id).first()
        if profile:
            if profile.passport_filename:
                passport_path = os.path.join(app.config['UPLOAD_FOLDER'], profile.passport_filename)
                if os.path.exists(passport_path):
                    try:
                        os.remove(passport_path)
                    except Exception as e:
                        print(f"Error deleting passport: {e}")
            if profile.receipt_filename:
                receipt_path = os.path.join(app.config['UPLOAD_FOLDER'], profile.receipt_filename)
                if os.path.exists(receipt_path):
                    try:
                        os.remove(receipt_path)
                    except Exception as e:
                        print(f"Error deleting receipt: {e}")
            db.session.delete(profile)

    elif user.role == 'lecturer':
        profile = LecturerProfile.query.filter_by(user_id=user.id).first()
        if profile:
            if profile.passport_filename:
                passport_path = os.path.join(app.config['UPLOAD_FOLDER'], profile.passport_filename)
                if os.path.exists(passport_path):
                    try:
                        os.remove(passport_path)
                    except Exception as e:
                        print(f"Error deleting passport: {e}")
            if profile.staff_id_filename:
                staff_path = os.path.join(app.config['UPLOAD_FOLDER'], profile.staff_id_filename)
                if os.path.exists(staff_path):
                    try:
                        os.remove(staff_path)
                    except Exception as e:
                        print(f"Error deleting staff ID: {e}")
            db.session.delete(profile)

    # Delete activity logs
    ActivityLog.query.filter_by(user_id=user.id).delete()

    # Delete user
    db.session.delete(user)
    db.session.commit()

    flash(f'User {user.full_name} deleted successfully.', 'success')
    return redirect(url_for('admin_manage_users'))
	


# ------------------------------------------------
# FAVOURITES, READER, ALLOWLIST, ERROR HANDLERS
# ------------------------------------------------

@app.context_processor
def inject_favorites():
    ids = set()
    try:
        if current_user.is_authenticated:
            ids = {f.resource_id for f in Favorite.query.filter_by(user_id=current_user.id).all()}
    except Exception:
        ids = set()
    return dict(my_favorite_ids=ids)


@app.route('/favorites')
@login_required
def my_favorites():
    favs = Favorite.query.filter_by(user_id=current_user.id).order_by(Favorite.created_at.desc()).all()
    resources = [f.resource for f in favs if f.resource is not None]
    return render_template('favorites.html', resources=resources)


@app.route('/favorite/toggle/<int:resource_id>', methods=['POST'])
@login_required
def toggle_favorite(resource_id):
    resource = Resource.query.get(resource_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if not resource:
        if is_ajax:
            return jsonify(ok=False, error='Resource not found'), 404
        flash('That resource no longer exists.', 'warning')
        return redirect(request.referrer or url_for('categories'))
    fav = Favorite.query.filter_by(user_id=current_user.id, resource_id=resource_id).first()
    if fav:
        db.session.delete(fav)
        favorited = False
    else:
        db.session.add(Favorite(user_id=current_user.id, resource_id=resource_id))
        favorited = True
    db.session.commit()
    if is_ajax:
        return jsonify(ok=True, favorited=favorited)
    flash('Added to favourites.' if favorited else 'Removed from favourites.', 'success')
    return redirect(request.referrer or url_for('my_favorites'))


@app.route('/read/<int:resource_id>')
@login_required
def read_resource(resource_id):
    resource = Resource.query.get(resource_id)
    if not resource:
        flash('That resource could not be found.', 'warning')
        return redirect(url_for('categories'))
    resource.view_count = (resource.view_count or 0) + 1
    db.session.add(ActivityLog(user_id=current_user.id, resource_id=resource.id, action='read'))
    db.session.commit()
    return render_template('read_resource.html', resource=resource)


# Admin: manage the registration allow list
@app.route('/admin/allowlist')
@login_required
def admin_allowlist():
    if current_user.role != 'admin':
        flash('Access denied. Administrators only.', 'danger')
        return redirect(url_for('dashboard'))
    students = AllowedStudent.query.order_by(AllowedStudent.full_name).all()
    lecturers = AllowedLecturer.query.order_by(AllowedLecturer.full_name).all()
    return render_template('admin_allowlist.html', students=students, lecturers=lecturers)


@app.route('/admin/allowlist/add_student', methods=['POST'])
@login_required
def add_allowed_student():
    if current_user.role != 'admin':
        flash('Access denied. Administrators only.', 'danger')
        return redirect(url_for('dashboard'))
    matric = (request.form.get('matric_number') or '').strip()
    full_name = (request.form.get('full_name') or '').strip()
    level = (request.form.get('level') or '').strip()
    department = (request.form.get('department') or '').strip()
    if not matric or not full_name:
        flash('Matric number and full name are required.', 'warning')
    elif AllowedStudent.query.filter(db.func.lower(AllowedStudent.matric_number) == matric.lower()).first():
        flash('That matriculation number is already on the list.', 'info')
    else:
        db.session.add(AllowedStudent(matric_number=matric, full_name=full_name, level=(level or 'ND1'), department=department))
        db.session.commit()
        flash('Student added to the approved list.', 'success')
    return redirect(url_for('admin_allowlist'))


@app.route('/admin/allowlist/add_lecturer', methods=['POST'])
@login_required
def add_allowed_lecturer():
    if current_user.role != 'admin':
        flash('Access denied. Administrators only.', 'danger')
        return redirect(url_for('dashboard'))
    staff_id = (request.form.get('staff_id') or '').strip()
    full_name = (request.form.get('full_name') or '').strip()
    department = (request.form.get('department') or '').strip()
    if not staff_id or not full_name:
        flash('Staff ID and full name are required.', 'warning')
    elif AllowedLecturer.query.filter(db.func.lower(AllowedLecturer.staff_id) == staff_id.lower()).first():
        flash('That staff ID is already on the list.', 'info')
    else:
        db.session.add(AllowedLecturer(staff_id=staff_id, full_name=full_name, department=department))
        db.session.commit()
        flash('Lecturer added to the approved list.', 'success')
    return redirect(url_for('admin_allowlist'))


@app.route('/admin/allowlist/delete/<kind>/<int:entry_id>', methods=['POST'])
@login_required
def delete_allowed(kind, entry_id):
    if current_user.role != 'admin':
        flash('Access denied. Administrators only.', 'danger')
        return redirect(url_for('dashboard'))
    entry = None
    if kind == 'student':
        entry = AllowedStudent.query.get(entry_id)
    elif kind == 'lecturer':
        entry = AllowedLecturer.query.get(entry_id)
    if entry:
        db.session.delete(entry)
        db.session.commit()
        flash('Entry removed from the approved list.', 'success')
    else:
        flash('That entry could not be found.', 'warning')
    return redirect(url_for('admin_allowlist'))



# ------------------------------------------------
# REGISTRATION CHOOSER (clearly separate Student vs Lecturer)
# ------------------------------------------------
@app.route('/register')
def register_choice():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('register_choice.html')


# ------------------------------------------------
# ADMIN: BULK CSV IMPORT FOR THE APPROVED LIST
# ------------------------------------------------
def _read_csv_rows(file_storage):
    raw = file_storage.read().decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(StringIO(raw))
    for row in reader:
        yield { (k or '').strip().lower(): (v or '').strip() for k, v in row.items() }


@app.route('/admin/allowlist/import_students', methods=['POST'])
@login_required
def import_allowed_students():
    if current_user.role != 'admin':
        flash('Access denied. Administrators only.', 'danger')
        return redirect(url_for('dashboard'))
    file = request.files.get('csv_file')
    if not file or file.filename == '':
        flash('Please choose a CSV file to upload.', 'warning')
        return redirect(url_for('admin_allowlist'))
    if not file.filename.lower().endswith('.csv'):
        flash('The uploaded file must be a .csv file.', 'danger')
        return redirect(url_for('admin_allowlist'))
    try:
        added = skipped = bad = 0
        seen = set()
        for row in _read_csv_rows(file):
            matric = row.get('matric_number') or row.get('matric') or row.get('reg_number') or row.get('reg no') or ''
            name = row.get('full_name') or row.get('name') or ''
            level = row.get('level') or ''
            dept = row.get('department') or row.get('dept') or ''
            email = row.get('email') or ''
            if not matric or not name:
                bad += 1
                continue
            key = matric.lower()
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            if AllowedStudent.query.filter(db.func.lower(AllowedStudent.matric_number) == key).first():
                skipped += 1
                continue
            db.session.add(AllowedStudent(matric_number=matric, full_name=name,
                                          level=(level or 'ND1'), department=dept, email=email))
            added += 1
        db.session.commit()
        flash('Import complete: %d added, %d already existed, %d skipped (missing matric/name).'
              % (added, skipped, bad), 'success' if added else 'info')
    except Exception:
        db.session.rollback()
        flash('We could not read that CSV file. Please use the provided template format.', 'danger')
    return redirect(url_for('admin_allowlist'))


@app.route('/admin/allowlist/import_lecturers', methods=['POST'])
@login_required
def import_allowed_lecturers():
    if current_user.role != 'admin':
        flash('Access denied. Administrators only.', 'danger')
        return redirect(url_for('dashboard'))
    file = request.files.get('csv_file')
    if not file or file.filename == '':
        flash('Please choose a CSV file to upload.', 'warning')
        return redirect(url_for('admin_allowlist'))
    if not file.filename.lower().endswith('.csv'):
        flash('The uploaded file must be a .csv file.', 'danger')
        return redirect(url_for('admin_allowlist'))
    try:
        added = skipped = bad = 0
        seen = set()
        for row in _read_csv_rows(file):
            staff = row.get('staff_id') or row.get('staff id') or row.get('staffid') or ''
            name = row.get('full_name') or row.get('name') or ''
            dept = row.get('department') or row.get('dept') or ''
            email = row.get('email') or ''
            if not staff or not name:
                bad += 1
                continue
            key = staff.lower()
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            if AllowedLecturer.query.filter(db.func.lower(AllowedLecturer.staff_id) == key).first():
                skipped += 1
                continue
            db.session.add(AllowedLecturer(staff_id=staff, full_name=name, department=dept, email=email))
            added += 1
        db.session.commit()
        flash('Import complete: %d added, %d already existed, %d skipped (missing staff ID/name).'
              % (added, skipped, bad), 'success' if added else 'info')
    except Exception:
        db.session.rollback()
        flash('We could not read that CSV file. Please use the provided template format.', 'danger')
    return redirect(url_for('admin_allowlist'))


@app.route('/admin/allowlist/template/<kind>')
@login_required
def allowlist_template(kind):
    if current_user.role != 'admin':
        abort(403)
    out = StringIO()
    w = csv.writer(out)
    if kind == 'lecturer':
        w.writerow(['staff_id', 'full_name', 'department', 'email'])
        w.writerow(['FPN/STAFF/SE/001', 'Dr. Samuel Eze', 'Software Engineering', 'samuel.eze@example.com'])
        fname = 'allowed_lecturers_template.csv'
    else:
        w.writerow(['matric_number', 'full_name', 'level', 'department', 'email'])
        w.writerow(['FPN/S05/2023/2024/CS/SW/001', 'Student One', 'ND1', 'Computer Science', 'student001@example.com'])
        fname = 'allowed_students_template.csv'
    return Response(out.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment;filename=' + fname})


# ------------------------------------------------
# ADMIN: PRINTABLE PDF REPORTS (print any report)
# ------------------------------------------------
def _build_pdf(title, header, rows, filename):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = [Paragraph(title, styles['Heading1']),
                Paragraph('Generated: ' + datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'), styles['Normal']),
                Spacer(1, 12)]
    data = [header] + (rows if rows else [['No records found.'] + [''] * (len(header) - 1)])
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#145a2e')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#eef2ee')]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elements.append(table)
    doc.build(elements)
    buffer.seek(0)
    return Response(buffer, mimetype='application/pdf',
                    headers={'Content-Disposition': 'attachment;filename=' + filename})


@app.route('/admin/report/activity/pdf')
@login_required
def report_activity_pdf():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    acts = ActivityLog.query.order_by(ActivityLog.timestamp.desc()).limit(3000).all()
    rows = [[a.timestamp.strftime('%Y-%m-%d %H:%M') if a.timestamp else '-',
             (a.user.full_name if a.user else '-'),
             a.action.capitalize(),
             (a.resource.title if a.resource else '-')] for a in acts]
    return _build_pdf('Full Activity Log', ['Time', 'User', 'Action', 'Resource'], rows, 'activity_log.pdf')


@app.route('/admin/report/allowlist/pdf')
@login_required
def report_allowlist_pdf():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    students = AllowedStudent.query.order_by(AllowedStudent.matric_number).all()
    rows = [[s.matric_number, s.full_name, s.level or '-', s.department or '-'] for s in students]
    return _build_pdf('Approved Registration List (Students)',
                      ['Matric Number', 'Full Name', 'Level', 'Department'], rows, 'approved_students.pdf')


@app.route('/admin/report/resources/pdf')
@login_required
def report_resources_pdf():
    if current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard'))
    res = Resource.query.order_by(Resource.upload_date.desc()).all()
    rows = [[r.title, (r.category.name if r.category else '-'), r.resource_type.capitalize(),
             str(r.view_count or 0), str(r.download_count or 0)] for r in res]
    return _build_pdf('Library Resources Report',
                      ['Title', 'Category', 'Type', 'Views', 'Downloads'], rows, 'resources.pdf')


# ------------------------------------------------
# GLOBAL ERROR HANDLERS (friendly messages, never a raw crash)
# ------------------------------------------------
@app.errorhandler(400)
def err_400(e):
    return render_template('error.html', code=400, title='Bad request',
                           message="We could not process that request. Please check your input and try again."), 400


@app.errorhandler(403)
def err_403(e):
    return render_template('error.html', code=403, title='Access denied',
                           message="You do not have permission to view this page."), 403


@app.errorhandler(404)
def err_404(e):
    return render_template('error.html', code=404, title='Page not found',
                           message="The page you are looking for does not exist or may have moved."), 404


@app.errorhandler(405)
def err_405(e):
    return render_template('error.html', code=405, title='Action not allowed',
                           message="That action is not allowed here. Please go back and try again."), 405


@app.errorhandler(413)
def err_413(e):
    flash('That file is too large. The maximum upload size is 50 MB.', 'danger')
    return redirect(request.referrer or url_for('dashboard'))


@app.errorhandler(500)
def err_500(e):
    try:
        db.session.rollback()
    except Exception:
        pass
    return render_template('error.html', code=500, title='Something went wrong',
                           message="An unexpected error occurred. Please try again. If it keeps happening, contact the library administrator."), 500


@app.errorhandler(Exception)
def err_unhandled(e):
    if isinstance(e, HTTPException):
        return e
    try:
        db.session.rollback()
    except Exception:
        pass
    app.logger.exception('Unhandled application error')
    return render_template('error.html', code=500, title='Something went wrong',
                           message="An unexpected error occurred. Please try again. If it keeps happening, contact the library administrator."), 500


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    port = int(os.environ.get('PORT', '5000'))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug)
