import os
import shutil
from datetime import datetime, timedelta
try:
    from dateutil.tz import tzlocal, gettz
except ImportError:
    # Fallback for different dateutil versions
    try:
        from dateutil import tz
        tzlocal = tz.tzlocal
        gettz = tz.gettz
    except ImportError:
        # Ultimate fallback: use UTC
        from datetime import timezone
        tzlocal = lambda: timezone.utc
        gettz = lambda name: timezone.utc if name else timezone.utc

from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Index, CheckConstraint, event
from sqlalchemy.pool import NullPool
from sqlalchemy.engine import Engine
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from io import BytesIO
# Defer pandas import to avoid heavy loading at module import time


BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# 한국 시간대 설정
try:
    KST = gettz('Asia/Seoul')
    if KST is None:
        # Fallback to UTC if timezone not available
        from datetime import timezone
        KST = timezone.utc
except Exception:
    # Fallback to UTC if timezone setup fails
    from datetime import timezone
    KST = timezone.utc

# Robust detection for serverless / read-only FS
def _is_read_only_fs() -> bool:
    try:
        test_dir = "/var/task/__wtest__"
        os.makedirs(test_dir, exist_ok=True)
        test_file = os.path.join(test_dir, "t")
        with open(test_file, "wb") as f:
            f.write(b"x")   # 대부분의 서버리스에서 여기서 OSError: [Errno 30]
        os.remove(test_file)
        return False
    except OSError:
        return True

is_serverless = bool(
    os.environ.get('VERCEL') or
    os.environ.get('VERCEL_ENV') or
    os.environ.get('AWS_LAMBDA_FUNCTION_NAME') or
    os.environ.get('LAMBDA_TASK_ROOT') or
    os.environ.get('K_SERVICE') or
    _is_read_only_fs()
)

if is_serverless:
    INSTANCE_DIR = os.environ.get('INSTANCE_PATH', '/tmp/instance')
    DATA_DIR = os.environ.get('DATA_DIR', '/tmp/data')
    UPLOAD_DIR = os.environ.get('UPLOAD_DIR', '/tmp/uploads')
else:
    INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')
    DATA_DIR = os.path.join(BASE_DIR, 'data')
    UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')


DB_PATH = os.path.join(DATA_DIR, 'busan.db')
STATIC_DIR = os.path.join(BASE_DIR, 'static')
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')

LOGO_SRC_FILENAME = 'logo.png'
# 컨테이너 내부에서 접근 가능한 경로로 변경
LOGO_SOURCE_PATH_IN_CONTAINER = os.path.join(BASE_DIR, LOGO_SRC_FILENAME)

# Ensure directories exist
if is_serverless:
    os.makedirs(INSTANCE_DIR, exist_ok=True)  # /tmp/instance
    os.makedirs(DATA_DIR, exist_ok=True)      # /tmp/data
    os.makedirs(UPLOAD_DIR, exist_ok=True)    # /tmp/uploads
else:
    os.makedirs(INSTANCE_DIR, exist_ok=True)  # 로컬에서도 만들어두는 편이 안전
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(STATIC_DIR, exist_ok=True)



def create_app():
    # Use instance_path for Vercel compatibility
    instance_path = INSTANCE_DIR if is_serverless else None
    app = Flask(__name__, 
                template_folder=TEMPLATE_DIR, 
                static_folder=STATIC_DIR,
                instance_path=instance_path)
    app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key')
    
    # Disable template caching in development
    if not is_serverless:
        app.config['TEMPLATES_AUTO_RELOAD'] = True
        app.jinja_env.auto_reload = True
        app.jinja_env.cache = {}
    
    # Database configuration with Vercel support
    if is_serverless:
        # Check for external database first
        database_url = os.environ.get('DATABASE_URL')
        if database_url:
            # External database (PostgreSQL, MySQL, etc.)
            app.config['SQLALCHEMY_DATABASE_URI'] = database_url
            app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
                'poolclass': NullPool,  # Serverless-friendly
                'pool_pre_ping': True,
                'connect_args': {
                    'connect_timeout': 10,
                }
            }
        else:
            # Fallback to /tmp SQLite for Vercel
            tmp_db_path = os.path.join(DATA_DIR, 'busan.db')
            app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{tmp_db_path}'
            
            # SQLite pragma를 위한 커스텀 커넥션 팩토리
            def get_sqlite_connect_args():
                return {
                    'check_same_thread': False,
                    'timeout': 20,
                }
            
            app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
                'poolclass': NullPool,  # Serverless-friendly
                'connect_args': get_sqlite_connect_args(),
            }
    else:
        # Local development
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
    
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    return app


# SQLite foreign keys 설정 함수 (app 생성 전에 정의)
_sqlite_pragma_registered = False

def register_sqlite_pragma():
    """Register SQLite pragma event listener (called once, in app context)"""
    global _sqlite_pragma_registered
    if not _sqlite_pragma_registered:
        try:
            # Register on Engine class level (doesn't require app context)
            @event.listens_for(Engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                """SQLite 연결 시 foreign keys 활성화"""
                try:
                    if hasattr(dbapi_connection, 'cursor'):
                        cursor = dbapi_connection.cursor()
                        cursor.execute("PRAGMA foreign_keys=ON")
                        cursor.close()
                except Exception:
                    pass
            _sqlite_pragma_registered = True
        except Exception as e:
            print(f"Warning: Failed to register SQLite pragma: {e}")
            pass

# Create app and extensions using standard Flask pattern
try:
    app = create_app()
    db = SQLAlchemy()
    login_manager = LoginManager()
    
    # Initialize extensions with app
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'login'
    
    # Ensure tzlocal is available in Jinja templates
    try:
        app.jinja_env.globals['tzlocal'] = tzlocal
    except Exception:
        pass
    # Register SQLite pragma after app is created
    register_sqlite_pragma()
    print("✓ Flask app created successfully")
except Exception as e:
    import traceback
    error_msg = f"CRITICAL: App creation failed: {e}\n{traceback.format_exc()}"
    print(error_msg)
    # Create minimal error app instead of crashing
    app = Flask(__name__)
    db = None
    login_manager = None

# Add custom Jinja filter for datetime formatting
@app.template_filter('to_local_datetime')
def to_local_datetime(dt):
    """Convert datetime to local timezone and format for datetime-local input"""
    if not dt:
        return ''
    try:
        local_dt = dt.astimezone(KST)
        return local_dt.strftime('%Y-%m-%dT%H:%M')
    except Exception:
        return ''

# Add another filter for safe datetime display
@app.template_filter('safe_datetime')
def safe_datetime(dt):
    """Safely format datetime for display"""
    if not dt:
        return ''
    try:
        if hasattr(dt, 'strftime'):
            return dt.strftime('%Y-%m-%d %H:%M')
        return str(dt)
    except Exception:
        return ''


def _ensure_aware(dt):
    if not dt:
        return None
    try:
        # tz-naive if no tzinfo or utcoffset is None
        if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
            return dt.replace(tzinfo=KST)
        return dt
    except Exception:
        return dt


def ensure_logo():
    """로고 파일이 없으면 원본에서 복사"""
    if is_serverless:
        # Vercel 환경에서는 static 디렉토리에 쓸 수 없으므로 이 작업 건너뛰기
        return
    os.makedirs(STATIC_DIR, exist_ok=True)
    dst = os.path.join(STATIC_DIR, 'logo.png')
    if os.path.exists(dst):
        try:
            if os.path.getsize(dst) > 0:
                return
        except Exception:
            pass
    
    # 원본 로고 파일 경로들 시도
    original_logo_paths = [
        LOGO_SOURCE_PATH_IN_CONTAINER,  # 컨테이너 내부: /app/logo.png (repo 동봉)
        '/Users/USER/dev/busan/logo.png',  # 호스트 절대 경로
    ]
    
    for src in original_logo_paths:
        try:
            if os.path.exists(src):
                shutil.copy(src, dst)
                return
        except Exception:
            continue
    
    # 로고 파일이 없으면 빈 파일 생성 (나중에 업로드 가능)
    try:
        with open(dst, 'wb') as f:
            f.write(b'')
    except Exception:
        pass


# Models need db to be available - ensure it exists
if db is None:
    raise RuntimeError("Database instance not initialized. Check app creation.")

class Member(UserMixin, db.Model):
    __table_args__ = (
        Index('idx_member_created_at', 'created_at'),
        CheckConstraint("approval_status IN ('신청','승인중','승인')", name='ck_member_approval_status'),
        CheckConstraint("role IN ('member','admin')", name='ck_member_role'),
    )
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    company_name = db.Column(db.String(255))
    address = db.Column(db.String(255))
    business_number = db.Column(db.String(64), unique=True)
    corporation_number = db.Column(db.String(64))
    representative = db.Column(db.String(128))
    phone = db.Column(db.String(64))
    mobile = db.Column(db.String(64))
    email = db.Column(db.String(255))
    registration_cert_path = db.Column(db.String(512))
    approval_status = db.Column(db.String(32), default='신청')  # 신청, 승인중, 승인
    role = db.Column(db.String(32), default='member')  # member, admin
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(KST))

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class InsuranceApplication(db.Model):
    __table_args__ = (
        Index('idx_ins_app_created_by', 'created_by_member_id'),
        Index('idx_ins_app_desired', 'desired_start_date'),
        Index('idx_ins_app_created', 'created_at'),
        Index('idx_ins_app_approved', 'approved_at'),
        Index('idx_ins_app_status', 'status'),
        Index('idx_ins_app_start', 'start_at'),
        Index('idx_ins_app_car_plate', 'car_plate'),
        Index('idx_ins_app_vin', 'vin'),
        CheckConstraint("status IN ('신청','조합승인','가입','종료')", name='ck_ins_app_status'),
    )
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(KST))  # 신청시간 (timezone-aware)
    desired_start_date = db.Column(db.Date, nullable=False)  # 가입희망일자
    start_at = db.Column(db.DateTime(timezone=True))  # 가입시간 (timezone-aware)
    end_at = db.Column(db.DateTime(timezone=True))  # 종료시간 (timezone-aware)
    approved_at = db.Column(db.DateTime(timezone=True))  # 조합승인시간 (timezone-aware)
    insured_code = db.Column(db.String(64))  # 피보험자코드 = 사업자번호
    contractor_code = db.Column(db.String(64), default='부산자동차매매사업자조합')  # 계약자코드
    car_plate = db.Column(db.String(64))  # 한글차량번호
    vin = db.Column(db.String(64))  # 차대번호
    car_name = db.Column(db.String(128))  # 차량명
    car_registered_at = db.Column(db.Date)  # 차량등록일자
    premium = db.Column(db.Integer, default=9500)  # 보험료 9500 고정
    status = db.Column(db.String(32), default='신청')  # 신청, 조합승인, 가입, 종료
    memo = db.Column(db.String(255))  # 비고
    created_by_member_id = db.Column(db.Integer, db.ForeignKey('member.id'))

    created_by_member = db.relationship('Member', backref='applications')

    def recompute_status(self) -> None:
        now = datetime.now(KST)
        approved_at_local = _ensure_aware(self.approved_at)
        end_at_local = _ensure_aware(self.end_at)
        # After approval + 2 hours -> 가입
        if self.status in ('신청', '조합승인'):
            if approved_at_local and now >= approved_at_local + timedelta(hours=2):
                self.status = '가입'
                if not self.start_at: # 이미 start_at이 설정되어 있지 않은 경우에만 자동 설정
                    # 가입일/종료일 설정: 가입희망일자 기준으로 세팅, 종료는 30일 후
                    start_date = datetime.combine(self.desired_start_date, datetime.min.time(), tzinfo=KST)
                    self.start_at = start_date
                    self.end_at = start_date + timedelta(days=30)
        # 종료일 경과 -> 종료
        if end_at_local and now >= end_at_local:
            self.status = '종료'


# User loader - register conditionally
def load_user(user_id):
    try:
        if db is None or login_manager is None:
            return None
        return db.session.get(Member, int(user_id))
    except Exception:
        return None

# Register user loader only if login_manager exists
if login_manager is not None:
    login_manager.user_loader(load_user)


def init_db_and_assets():
    """데이터베이스 및 리소스 초기화 (app context 내에서 호출해야 함)"""
    from flask import current_app
    
    if db is None:
        print("Warning: db is None, skipping initialization")
        return
    
    try:
        # Vercel에서도 in-memory SQLite를 사용하므로 테이블 생성은 항상 수행
        db.create_all()
    except Exception as e:
        print(f"Warning: Database creation failed: {e}")
        import traceback
        traceback.print_exc()
        # Continue anyway - tables might already exist
    
    try:
        ensure_logo()
    except Exception as e:
        print(f"Warning: Logo setup failed: {e}")
        pass

    # 스키마 보정: role 컬럼이 없으면 추가 (SQLite만)
    try:
        from sqlalchemy import text
        db_uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
        if 'sqlite' in db_uri:
            res = db.session.execute(text("PRAGMA table_info(member)"))
            cols = [r[1] for r in res.fetchall()]
            if 'role' not in cols:
                if not is_serverless:  # Vercel 환경이 아니면 스키마 변경
                    db.session.execute(text("ALTER TABLE member ADD COLUMN role TEXT NOT NULL DEFAULT 'member'"))
                    db.session.commit()
    except Exception as e:
        print(f"Warning: Schema migration failed: {e}")
        pass
    
    # 관리자 계정 생성 (없으면 생성)
    try:
        admin_username = 'busan'
        # Use db.session.query instead of Member.query to ensure app context
        admin = db.session.query(Member).filter_by(username=admin_username).first()
        if not admin:
            admin = Member(
                username=admin_username,
                company_name='부산자동차매매사업자조합',
                business_number='0000000000',
                representative='관리자',
                approval_status='승인',
                role='admin',
            )
            admin.set_password('busan123')
            db.session.add(admin)
            db.session.commit()
            print(f'관리자 계정이 생성되었습니다. 아이디: {admin_username}, 비밀번호: busan123')
        else:
            if not getattr(admin, 'role', None) or admin.role != 'admin':
                admin.role = 'admin'
                db.session.commit()
    except Exception as e:
        print(f"Warning: Admin account creation failed: {e}")
        import traceback
        traceback.print_exc()
        pass

# 관리자 권한 데코레이터
def admin_required(view):
    from functools import wraps
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or getattr(current_user, 'role', 'member') != 'admin':
            flash('관리자만 접근 가능합니다.', 'warning')
            return redirect(url_for('dashboard'))
        return view(*args, **kwargs)
    return wrapped


# Deferred initialization flag for serverless compatibility
_initialized = False

def ensure_initialized():
    """Ensure database and assets are initialized (called on first request)"""
    from flask import has_app_context, current_app
    
    global _initialized
    if not _initialized:
        try:
            # Ensure we have app context
            if not has_app_context():
                # This should not happen in a request, but handle it
                print("Warning: ensure_initialized called without app context")
                return
            # Now init_db_and_assets can safely use current_app and db
            init_db_and_assets()
            _initialized = True
        except Exception as e:
            print(f"Warning: Initialization failed: {e}")
            import traceback
            traceback.print_exc()
            # Mark as initialized anyway to avoid infinite retry loops
            _initialized = True

# SQLite pragma registration is now done in register_sqlite_pragma() above

# Don't initialize at module level - wait for first request
# This avoids app context issues


@app.context_processor
def inject_jinja_globals():
    # Make tzlocal available in Jinja templates
    return {
        'tzlocal': tzlocal,
    }


@app.route('/')
def index():
    try:
        ensure_initialized()  # Initialize on first request for Vercel
        if current_user.is_authenticated:
            return redirect(url_for('dashboard'))
        return redirect(url_for('login'))
    except Exception as e:
        import traceback
        return f"<h1>Error</h1><pre>{traceback.format_exc()}</pre>", 500


@app.route('/healthz')
def healthz():
    try:
        if db is None:
            return 'db not initialized', 500
        ensure_initialized()
        # Simple DB check
        from sqlalchemy import text
        db.session.execute(text('SELECT 1'))
        return 'ok', 200
    except Exception as e:
        return f'error: {str(e)}', 500

@app.route('/favicon.ico')
def favicon():
    """Handle favicon requests - serve logo.png as favicon or return 204"""
    logo_path = os.path.join(STATIC_DIR, 'logo.png')
    if os.path.exists(logo_path):
        return send_file(logo_path, mimetype='image/png')
    return '', 204

@app.route('/debug/template-check')
def debug_template_check():
    """Debug route to check template loading"""
    if not app.debug and is_serverless:
        return "Debug disabled", 404
    
    import os
    template_path = os.path.join(TEMPLATE_DIR, 'admin', 'insurance.html')
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Check if the template has been updated
        has_filter = 'to_local_datetime' in content
        has_old_syntax = 'tzlocal()' in content
        
        return f"""
        <h1>Template Debug Info</h1>
        <p><strong>Template Path:</strong> {template_path}</p>
        <p><strong>File exists:</strong> {os.path.exists(template_path)}</p>
        <p><strong>Has new filter:</strong> {has_filter}</p>
        <p><strong>Has old syntax:</strong> {has_old_syntax}</p>
        <p><strong>Template cache disabled:</strong> {app.config.get('TEMPLATES_AUTO_RELOAD', False)}</p>
        <hr>
        <h2>Line 86 area:</h2>
        <pre>{chr(10).join(content.split(chr(10))[83:89])}</pre>
        """
    except Exception as e:
        return f"Error reading template: {e}"


@app.route('/login', methods=['GET', 'POST'])
def login():
    ensure_initialized()  # Initialize on first request for Vercel
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = Member.query.filter_by(username=username).first()
        if user and user.check_password(password):
            if user.approval_status != '승인':
                flash('관리자 승인 후 로그인 가능합니다.', 'warning')
                return redirect(url_for('login'))
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('아이디 또는 비밀번호가 올바르지 않습니다.', 'danger')
    return render_template('auth/login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        company_name = request.form.get('company_name', '').strip()
        address = request.form.get('address', '').strip()
        business_number = request.form.get('business_number', '').strip()
        corporation_number = request.form.get('corporation_number', '').strip()
        representative = request.form.get('representative', '').strip()
        phone = request.form.get('phone', '').strip()
        mobile = request.form.get('mobile', '').strip()
        email = request.form.get('email', '').strip()

        if Member.query.filter_by(username=username).first():
            flash('이미 존재하는 아이디입니다.', 'danger')
            return render_template('auth/register.html')
        if business_number and Member.query.filter_by(business_number=business_number).first():
            flash('이미 등록된 사업자번호입니다.', 'danger')
            return render_template('auth/register.html')

        # 파일 업로드 처리
        registration_cert_path = None # Vercel 환경에서는 파일 업로드 비활성화
        if not is_serverless and 'registration_cert' in request.files:
            file = request.files['registration_cert']
            if file and file.filename:
                allowed_extensions = {'.pdf', '.jpg', '.jpeg', '.png'}
                file_ext = os.path.splitext(file.filename)[1].lower()
                if file_ext in allowed_extensions:
                    timestamp = datetime.now(KST).strftime('%Y%m%d_%H%M%S')
                    filename = f"{business_number}_{timestamp}{file_ext}"
                    filepath = os.path.join(UPLOAD_DIR, filename)
                    file.save(filepath)
                    registration_cert_path = os.path.join('uploads', filename)
        member = Member(
            username=username,
            company_name=company_name,
            address=address,
            business_number=business_number,
            corporation_number=corporation_number,
            representative=representative,
            phone=phone,
            mobile=mobile,
            email=email,
            registration_cert_path=registration_cert_path,
            approval_status='신청',
        )
        member.set_password(password)
        db.session.add(member)
        db.session.commit()
        flash('신청이 접수되었습니다. 관리자 승인 후 로그인 가능합니다.', 'success')
        return redirect(url_for('login'))

    return render_template('auth/register.html')


@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')


@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    """업로드된 파일 제공"""
    if is_serverless:
        flash('Vercel 환경에서는 파일 제공이 제한됩니다.', 'warning')
        return redirect(url_for('dashboard'))
    return send_file(os.path.join(UPLOAD_DIR, filename))


@app.route('/terms')
@login_required
def terms():
    return render_template('terms.html')


@app.route('/terms/guide.pdf')
@login_required
def terms_guide_pdf():
    """상품안내 PDF를 브라우저에 표시 (inline)"""
    try:
        pdf_path = os.path.join(BASE_DIR, '@중고차매매업자자동차보험_상품안내_부산.pdf')
        return send_file(pdf_path, mimetype='application/pdf')
    except Exception:
        flash('안내 문서를 불러올 수 없습니다.', 'danger')
        return redirect(url_for('terms'))


@app.route('/terms/policy/download')
@login_required
def terms_policy_download():
    """약관 PDF 다운로드"""
    try:
        pdf_path = os.path.join(BASE_DIR, '중고차 매매업자 자동차보험 약관.pdf')
        return send_file(pdf_path, as_attachment=True, download_name='중고차_매매업자_자동차보험_약관.pdf', mimetype='application/pdf')
    except Exception:
        flash('약관 파일을 다운로드할 수 없습니다.', 'danger')
        return redirect(url_for('terms'))


def parse_date(value: str):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except Exception:
        return None


def parse_datetime(value: str):
    if not value:
        return None
    try:
        dt = datetime.strptime(value, '%Y-%m-%d %H:%M')
        return _ensure_aware(dt)
    except ValueError:
        try:
            dt = datetime.strptime(value, '%Y-%m-%d')
            return _ensure_aware(dt)
        except Exception:
            return None
    except Exception:
        return None


@app.route('/insurance', methods=['GET', 'POST'])
@login_required
def insurance():
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'save' or action == 'delete':
            # 저장/삭제 작업
            row_id = request.form.get('row_id')
            if row_id:
                row = db.session.get(InsuranceApplication, int(row_id))
                if row and row.created_by_member_id == current_user.id:
                    if action == 'delete':
                        if not row.approved_at:  # 조합승인 전까지만 삭제 가능
                            db.session.delete(row)
                            db.session.commit()
                            flash('삭제되었습니다.', 'success')
                        else:
                            flash('조합승인 후에는 삭제할 수 없습니다.', 'warning')
                    elif action == 'save':
                        if not row.approved_at:  # 조합승인 전까지만 수정 가능
                            # 편집 모드에서 온 경우에만 모든 필드 업데이트
                            if request.form.get('desired_start_date'):
                                row.desired_start_date = parse_date(request.form.get('desired_start_date'))
                                row.car_plate = request.form.get('car_plate', '').strip()
                                row.vin = request.form.get('vin', '').strip()
                                row.car_name = request.form.get('car_name', '').strip()
                                row.car_registered_at = parse_date(request.form.get('car_registered_at'))
                            # 비고는 항상 업데이트 가능
                            row.memo = request.form.get('memo', '').strip()
                            db.session.commit()
                            flash('저장되었습니다.', 'success')
                        else:
                            flash('조합승인 후에는 수정할 수 없습니다.', 'warning')
            return redirect(url_for('insurance'))
        
        # 신규 가입
        desired_start_date = parse_date(request.form.get('desired_start_date'))
        car_plate = request.form.get('car_plate', '').strip()
        vin = request.form.get('vin', '').strip()
        car_name = request.form.get('car_name', '').strip()
        car_registered_at = parse_date(request.form.get('car_registered_at'))
        memo = request.form.get('memo', '').strip()

        app_row = InsuranceApplication(
            desired_start_date=desired_start_date,
            insured_code=current_user.business_number or '',
            contractor_code='부산자동차매매사업자조합',
            car_plate=car_plate,
            vin=vin,
            car_name=car_name,
            car_registered_at=car_registered_at,
            premium=9500,
            status='신청',
            memo=memo,
            created_by_member_id=current_user.id,
        )
        db.session.add(app_row)
        db.session.commit()
        flash('신청이 등록되었습니다.', 'success')
        return redirect(url_for('insurance'))

    # 검색
    start_date = parse_date(request.args.get('start_date', ''))
    end_date = parse_date(request.args.get('end_date', ''))
    edit_id = request.args.get('edit_id')  # 편집 모드

    q = InsuranceApplication.query.filter_by(created_by_member_id=current_user.id)
    if start_date:
        q = q.filter(InsuranceApplication.desired_start_date >= start_date)
    if end_date:
        q = q.filter(InsuranceApplication.desired_start_date <= end_date)

    rows = q.order_by(InsuranceApplication.created_at.desc()).all()
    # 상태 재계산
    changed = False
    for r in rows:
        old_status = r.status
        r.recompute_status()
        if r.status != old_status:
            changed = True
    if changed:
        db.session.commit()

    # Build view models with proper timezone formatting
    def fmt_display_safe(dt):
        if not dt:
            return ''
        try:
            # Ensure timezone-aware datetime and convert to KST
            if dt.tzinfo is None:
                # If naive, assume it's already in KST
                local_dt = dt.replace(tzinfo=KST)
            else:
                # Convert to KST
                local_dt = dt.astimezone(KST)
            return local_dt.strftime('%Y-%m-%d %H:%M')
        except Exception:
            return ''

    items = []
    for r in rows:
        items.append({
            'id': r.id,
            'created_at_str': fmt_display_safe(r.created_at),
            'start_at_str': fmt_display_safe(r.start_at),
            'end_at_str': fmt_display_safe(r.end_at),
            'approved_at_str': fmt_display_safe(r.approved_at),
        })

    return render_template('insurance.html', rows=rows, items=items, edit_id=edit_id)


@app.route('/insurance/template')
@login_required
def insurance_template_download():
    # Import pandas only when needed
    import pandas as pd
    
    # Create Excel template in-memory
    df = pd.DataFrame([
        {
            '가입희망일자(YYYY-MM-DD)': '',
            '한글차량번호': '',
            '차대번호': '',
            '차량명': '',
            '차량등록일자(YYYY-MM-DD)': '',
            '비고': '',
        }
    ])
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='template')
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name='insurance_upload_template.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@app.route('/insurance/upload', methods=['POST'])
@login_required
def insurance_upload():
    if is_serverless:
        flash('Vercel 환경에서는 엑셀 업로드가 제한됩니다.', 'warning')
        return redirect(url_for('insurance'))
    file = request.files.get('file')
    if not file:
        flash('파일을 선택하세요.', 'warning')
        return redirect(url_for('insurance'))
    try:
        # Import pandas only when needed
        import pandas as pd
        df = pd.read_excel(file)
        required_cols = {
            '가입희망일자(YYYY-MM-DD)',
            '한글차량번호',
            '차대번호',
            '차량명',
            '차량등록일자(YYYY-MM-DD)'
        }
        if not required_cols.issubset(set(df.columns)):
            flash('엑셀 양식이 올바르지 않습니다.', 'danger')
            return redirect(url_for('insurance'))
        count = 0
        for _, row in df.iterrows():
            desired_start_date = parse_date(str(row.get('가입희망일자(YYYY-MM-DD)', '')).strip())
            car_plate = str(row.get('한글차량번호', '')).strip()
            vin = str(row.get('차대번호', '')).strip()
            car_name = str(row.get('차량명', '')).strip()
            car_registered_at = parse_date(str(row.get('차량등록일자(YYYY-MM-DD)', '')).strip())
            memo = str(row.get('비고', '')).strip() if '비고' in df.columns else None
            if not desired_start_date or not car_plate:
                continue
            app_row = InsuranceApplication(
                desired_start_date=desired_start_date,
                insured_code=current_user.business_number or '',
                contractor_code='부산자동차매매사업자조합',
                car_plate=car_plate,
                vin=vin,
                car_name=car_name,
                car_registered_at=car_registered_at,
                premium=9500,
                status='신청',
                memo=memo,
                created_by_member_id=current_user.id,
            )
            db.session.add(app_row)
            count += 1
        db.session.commit()
        flash(f'{count}건 업로드되었습니다.', 'success')
    except Exception as e:
        flash('업로드 중 오류가 발생했습니다.', 'danger')
    return redirect(url_for('insurance'))


@app.route('/admin')
@login_required
@admin_required
def admin_home():
    return render_template('admin/index.html')


@app.route('/admin/members', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_members():
    if request.method == 'POST':
        action = request.form.get('action')
        member_id = request.form.get('member_id')
        if member_id:
            m = db.session.get(Member, int(member_id))
            if m:
                if action == 'update_status':
                    m.approval_status = request.form.get('approval_status', '신청')
                    db.session.commit()
                    flash('승인 상태가 변경되었습니다.', 'success')
                elif action == 'save':
                    m.company_name = request.form.get('company_name', '').strip()
                    m.address = request.form.get('address', '').strip()
                    m.corporation_number = request.form.get('corporation_number', '').strip()
                    m.representative = request.form.get('representative', '').strip()
                    m.phone = request.form.get('phone', '').strip()
                    m.mobile = request.form.get('mobile', '').strip()
                    m.email = request.form.get('email', '').strip()
                    m.approval_status = request.form.get('approval_status', '신청')
                    m.role = request.form.get('role', m.role or 'member')
                    db.session.commit()
                    flash('저장되었습니다.', 'success')
                    return redirect(url_for('admin_members'))
                elif action == 'delete':
                    db.session.delete(m)
                    db.session.commit()
                    flash('삭제되었습니다.', 'success')
        else:
            if action == 'create':
                username = request.form.get('username', '').strip()
                password = request.form.get('password', 'temp1234')
                company_name = request.form.get('company_name', '').strip()
                address = request.form.get('address', '').strip()
                business_number = request.form.get('business_number', '').strip()
                corporation_number = request.form.get('corporation_number', '').strip()
                representative = request.form.get('representative', '').strip()
                phone = request.form.get('phone', '').strip()
                mobile = request.form.get('mobile', '').strip()
                email = request.form.get('email', '').strip()
                approval_status = request.form.get('approval_status', '승인')
                role = request.form.get('role', 'member')
                if not username or not company_name or not business_number:
                    flash('아이디/상사명/사업자번호는 필수입니다.', 'warning')
                elif Member.query.filter((Member.username == username) | (Member.business_number == business_number)).first():
                    flash('이미 존재하는 아이디 또는 사업자번호입니다.', 'danger')
                else:
                    nm = Member(
                        username=username,
                        company_name=company_name,
                        address=address,
                        business_number=business_number,
                        corporation_number=corporation_number,
                        representative=representative,
                        phone=phone,
                        mobile=mobile,
                        email=email,
                        approval_status=approval_status,
                        role=role,
                    )
                    nm.set_password(password)
                    db.session.add(nm)
                    db.session.commit()
                    flash('회원이 추가되었습니다.', 'success')
                return redirect(url_for('admin_members'))

    edit_id = request.args.get('edit_id')
    members = Member.query.order_by(Member.created_at.desc()).all()
    return render_template('admin/members.html', members=members, edit_id=edit_id)


@app.route('/admin/members/upload', methods=['POST'])
@login_required
@admin_required
def admin_members_upload():
    if is_serverless:
        flash('Vercel 환경에서는 엑셀 업로드가 제한됩니다.', 'warning')
        return redirect(url_for('admin_members'))
    file = request.files.get('file')
    if not file:
        flash('엑셀 파일을 선택하세요.', 'warning')
        return redirect(url_for('admin_members'))
    try:
        # Import pandas only when needed
        import pandas as pd
        df = pd.read_excel(file)
        created = 0
        skipped = 0
        required_cols = {'username', 'company_name', 'business_number'}
        if not required_cols.issubset(set(df.columns)):
            flash('엑셀 컬럼이 올바르지 않습니다. (필수: username, company_name, business_number)', 'danger')
            return redirect(url_for('admin_members'))
        for _, row in df.iterrows():
            username = str(row.get('username', '')).strip()
            company_name = str(row.get('company_name', '')).strip()
            business_number = str(row.get('business_number', '')).strip()
            if not username or not company_name or not business_number:
                skipped += 1
                continue
            # Dup checks
            if Member.query.filter((Member.username == username) | (Member.business_number == business_number)).first():
                skipped += 1
                continue
            m = Member(
                username=username,
                company_name=company_name,
                address=str(row.get('address', '') or '').strip(),
                business_number=business_number,
                corporation_number=str(row.get('corporation_number', '') or '').strip(),
                representative=str(row.get('representative', '') or '').strip(),
                phone=str(row.get('phone', '') or '').strip(),
                mobile=str(row.get('mobile', '') or '').strip(),
                email=str(row.get('email', '') or '').strip(),
                approval_status=str(row.get('approval_status', '승인') or '승인').strip() or '승인',
                role=str(row.get('role', 'member') or 'member').strip() or 'member',
            )
            password = str(row.get('password', 'temp1234'))
            m.set_password(password)
            db.session.add(m)
            created += 1
        db.session.commit()
        flash(f'일괄 업로드 완료: 추가 {created}건, 건너뜀 {skipped}건', 'success')
    except Exception:
        flash('업로드 처리 중 오류가 발생했습니다.', 'danger')
    return redirect(url_for('admin_members'))


@app.route('/admin/insurance', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_insurance():
    if request.method == 'POST':
        if request.form.get('bulk_approve') == '1':
            # 일괄 승인: 미승인(= status == 신청)인 데이터 모두 승인 시간 부여
            rows = InsuranceApplication.query.filter(
                (InsuranceApplication.approved_at.is_(None))
            ).all()
            now = datetime.now(KST)
            for r in rows:
                r.approved_at = now
                r.status = '조합승인'
                # 가입일/종료일 설정: 가입희망일자 기준으로 세팅, 종료는 30일 후
                if not r.start_at:
                    start_date_aware = datetime.combine(r.desired_start_date, datetime.min.time(), tzinfo=KST)
                    r.start_at = start_date_aware
                    r.end_at = start_date_aware + timedelta(days=30)
            db.session.commit()
            flash('일괄 승인되었습니다.', 'success')
        else:
            # 단건 수정/삭제
            action = request.form.get('action')
            row_id = request.form.get('row_id')
            print(f"DEBUG: action={action}, row_id={row_id}")  # 디버그 로그
            
            if row_id:
                try:
                    row = db.session.get(InsuranceApplication, int(row_id))
                    print(f"DEBUG: Found row={row}")  # 디버그 로그
                except Exception as e:
                    print(f"DEBUG: Error finding row: {e}")
                    row = None
            else:
                row = None
                
            if row and action:
                if action == 'approve':
                    print(f"DEBUG: Approving row {row.id}")  # 디버그 로그
                    row.approved_at = datetime.now(KST)
                    row.status = '조합승인'
                    # 가입일/종료일 설정: 가입희망일자 기준으로 세팅, 종료는 30일 후
                    if not row.start_at:
                        start_date_aware = datetime.combine(row.desired_start_date, datetime.min.time(), tzinfo=KST)
                        row.start_at = start_date_aware
                        row.end_at = start_date_aware + timedelta(days=30)
                    db.session.commit()
                    flash('승인되었습니다.', 'success')
                    print(f"DEBUG: Approval completed for row {row.id}")  # 디버그 로그
                elif action == 'delete':
                    db.session.delete(row)
                    db.session.commit()
                    flash('삭제되었습니다.', 'success')
                elif action == 'save_memo':
                    row.memo = request.form.get('memo', row.memo)
                    db.session.commit()
                    flash('비고가 저장되었습니다.', 'success')
                elif action == 'save':
                    # 편집 모드에서 저장
                    row.desired_start_date = parse_date(request.form.get('desired_start_date'))
                    row.car_plate = request.form.get('car_plate', '').strip()
                    row.vin = request.form.get('vin', '').strip()
                    row.car_name = request.form.get('car_name', '').strip()
                    row.car_registered_at = parse_date(request.form.get('car_registered_at'))
                    row.start_at = parse_datetime(request.form.get('start_at')) # 가입시간 수정 추가
                    row.end_at = parse_datetime(request.form.get('end_at'))   # 종료시간 수정 추가
                    row.memo = request.form.get('memo', '').strip()
                    db.session.commit()
                    flash('저장되었습니다.', 'success')
                    return redirect(url_for('admin_insurance', 
                                           req_start=request.args.get('req_start'),
                                           req_end=request.args.get('req_end'),
                                           approved=request.args.get('approved'),
                                           appr_start=request.args.get('appr_start'),
                                           appr_end=request.args.get('appr_end')))

    # Filters
    req_start = parse_date(request.args.get('req_start', ''))
    req_end = parse_date(request.args.get('req_end', ''))
    approved_filter = request.args.get('approved')  # 승인/미승인/전체
    appr_start = parse_date(request.args.get('appr_start', ''))
    appr_end = parse_date(request.args.get('appr_end', ''))
    edit_id = request.args.get('edit_id')

    q = InsuranceApplication.query
    if req_start:
        q = q.filter(InsuranceApplication.created_at >= datetime.combine(req_start, datetime.min.time(), tzinfo=KST))
    if req_end:
        q = q.filter(InsuranceApplication.created_at <= datetime.combine(req_end, datetime.max.time(), tzinfo=KST))
    if approved_filter == '승인':
        q = q.filter(InsuranceApplication.approved_at.is_not(None))
    elif approved_filter == '미승인':
        q = q.filter(InsuranceApplication.approved_at.is_(None))
    if appr_start:
        q = q.filter(InsuranceApplication.approved_at >= datetime.combine(appr_start, datetime.min.time(), tzinfo=KST))
    if appr_end:
        q = q.filter(InsuranceApplication.approved_at <= datetime.combine(appr_end, datetime.max.time(), tzinfo=KST))

    rows = q.order_by(InsuranceApplication.created_at.desc()).all()
    for r in rows:
        r.recompute_status()
    db.session.commit()

    # Build view models with pre-formatted strings to avoid tzlocal usage in templates
    def fmt_display(dt):
        if not dt:
            return ''
        try:
            # Ensure timezone-aware datetime and convert to KST
            if dt.tzinfo is None:
                # If naive, assume it's already in KST
                local_dt = dt.replace(tzinfo=KST)
            else:
                # Convert to KST
                local_dt = dt.astimezone(KST)
            return local_dt.strftime('%Y-%m-%d %H:%M')
        except Exception:
            return ''

    def fmt_input(dt):
        if not dt:
            return ''
        try:
            # Ensure timezone-aware datetime and convert to KST
            if dt.tzinfo is None:
                # If naive, assume it's already in KST
                local_dt = dt.replace(tzinfo=KST)
            else:
                # Convert to KST
                local_dt = dt.astimezone(KST)
            return local_dt.strftime('%Y-%m-%dT%H:%M')
        except Exception:
            return ''

    items = []
    for r in rows:
        items.append({
            'id': r.id,
            'created_by_company': (r.created_by_member.company_name if r.created_by_member else ''),
            'created_at_str': fmt_display(r.created_at),
            'desired_start_date': r.desired_start_date,
            'start_at_str': fmt_display(r.start_at),
            'end_at_str': fmt_display(r.end_at),
            'approved_at_str': fmt_display(r.approved_at),
            'start_at_input': fmt_input(r.start_at),
            'end_at_input': fmt_input(r.end_at),
            'insured_code': r.insured_code,
            'contractor_code': r.contractor_code,
            'car_plate': r.car_plate,
            'vin': r.vin,
            'car_name': r.car_name,
            'car_registered_at': r.car_registered_at,
            'approved_at': r.approved_at,
            'memo': r.memo or '',
        })

    return render_template('admin/insurance.html', rows=rows, items=items, edit_id=edit_id)


@app.route('/admin/insurance/download')
@login_required
@admin_required
def admin_insurance_download():
    # Import pandas only when needed
    import pandas as pd
    
    # Export to Excel
    rows = InsuranceApplication.query.order_by(InsuranceApplication.created_at.desc()).all()
    data = []
    for r in rows:
        data.append({
            '상사명': r.created_by_member.company_name if r.created_by_member else '',
            '신청시간': r.created_at,
            '가입희망일자': r.desired_start_date,
            '가입시간': r.start_at,
            '종료시간': r.end_at,
            '조합승인시간': r.approved_at,
            '피보험자코드': r.insured_code,
            '계약자코드': r.contractor_code,
            '한글차량번호': r.car_plate,
            '차대번호': r.vin,
            '차량명': r.car_name,
            '차량등록일자': r.car_registered_at,
            '보험료': r.premium,
            '조합승인': '승인' if r.approved_at else '미승인',
            '비고': r.memo or '',
        })
    df = pd.DataFrame(data)
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='data')
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name='insurance_data.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@app.route('/admin/settlement')
@login_required
@admin_required
def admin_settlement():
    year = int(request.args.get('year', datetime.now().year))
    month = int(request.args.get('month', datetime.now().month))

    # 기준: 책임보험 승인페이지에서 해당 년/월 데이터 (시작일 기준)
    start_period = datetime(year, month, 1, tzinfo=KST)
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=KST)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=KST)

    rows = InsuranceApplication.query.filter(
        InsuranceApplication.start_at.is_not(None),
        InsuranceApplication.start_at >= start_period,
        InsuranceApplication.start_at < next_month,
    ).all()

    # 그룹핑: 상사별 건수/금액
    by_company = {}
    for r in rows:
        company = r.created_by_member.company_name if r.created_by_member else '미상'
        rep = r.created_by_member.representative if r.created_by_member else ''
        biz = r.created_by_member.business_number if r.created_by_member else ''
        key = (company, rep, biz)
        by_company.setdefault(key, 0)
        by_company[key] += 1

    settlements = []
    for (company, rep, biz), count in by_company.items():
        amount = count * 9500
        settlements.append({
            'company': company,
            'representative': rep,
            'business_number': biz,
            'count': count,
            'amount': amount,
        })

    return render_template('admin/settlement.html', year=year, month=month, settlements=settlements)


@app.route('/admin/invoice')
@login_required
@admin_required
def admin_invoice():
    company = request.args.get('company', '')
    representative = request.args.get('representative', '')
    business_number = request.args.get('business_number', '')
    try:
        year = int(request.args.get('year'))
        month = int(request.args.get('month'))
        count = int(request.args.get('count'))
        amount = int(request.args.get('amount'))
    except Exception:
        flash('요청 파라미터가 올바르지 않습니다.', 'danger')
        return redirect(url_for('admin_settlement'))
    return render_template('invoice.html',
                           company=company,
                           representative=representative,
                           business_number=business_number,
                           year=year,
                           month=month,
                           count=count,
                           amount=amount)


@app.route('/admin/invoice/batch')
@login_required
@admin_required
def admin_invoice_batch():
    # Render a combined printable page for all companies for the selected month
    year = int(request.args.get('year', datetime.now().year))
    month = int(request.args.get('month', datetime.now().month))

    start_period = datetime(year, month, 1, tzinfo=KST)
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=KST)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=KST)

    rows = InsuranceApplication.query.filter(
        InsuranceApplication.start_at.is_not(None),
        InsuranceApplication.start_at >= start_period,
        InsuranceApplication.start_at < next_month,
    ).all()

    by_company = {}
    for r in rows:
        company = r.created_by_member.company_name if r.created_by_member else '미상'
        rep = r.created_by_member.representative if r.created_by_member else ''
        biz = r.created_by_member.business_number if r.created_by_member else ''
        key = (company, rep, biz)
        by_company.setdefault(key, 0)
        by_company[key] += 1

    invoices = []
    for (company, rep, biz), count in by_company.items():
        amount = count * 9500
        invoices.append({
            'company': company,
            'representative': rep,
            'business_number': biz,
            'year': year,
            'month': month,
            'count': count,
            'amount': amount,
        })
    return render_template('invoice_batch.html', invoices=invoices)


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    # Log full stack trace for debugging while showing a friendly message to users
    app.logger.exception("Unhandled exception")
    try:
        flash('서버 처리 중 오류가 발생했습니다.', 'danger')
        return redirect(url_for('dashboard'))
    except Exception:
        # Fallback minimal response if flashing/redirecting fails (e.g., outside request context)
        return ("", 500)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)


