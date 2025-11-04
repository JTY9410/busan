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
from werkzeug.exceptions import HTTPException
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Index, CheckConstraint, event
from sqlalchemy.pool import NullPool
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError, OperationalError, IntegrityError
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from io import BytesIO
import functools
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
    """Safely detect if filesystem is read-only (serverless environment)"""
    try:
        # Use /tmp which is writable in most serverless environments
        test_dir = "/tmp/__wtest__"
        os.makedirs(test_dir, exist_ok=True)
        test_file = os.path.join(test_dir, "t")
        try:
            with open(test_file, "wb") as f:
                f.write(b"x")   # 대부분의 서버리스에서 여기서 OSError: [Errno 30]
            os.remove(test_file)
            return False
        except (OSError, IOError, PermissionError):
            return True
        finally:
            # Cleanup
            try:
                if os.path.exists(test_file):
                    os.remove(test_file)
            except Exception:
                pass
    except Exception:
        # If we can't determine, assume serverless if VERCEL env var is set
        return bool(os.environ.get('VERCEL') or os.environ.get('VERCEL_ENV'))

# Check environment variables first (fastest and most reliable)
is_serverless = bool(
    os.environ.get('VERCEL') or
    os.environ.get('VERCEL_ENV') or
    os.environ.get('AWS_LAMBDA_FUNCTION_NAME') or
    os.environ.get('LAMBDA_TASK_ROOT') or
    os.environ.get('K_SERVICE')
)

# Only check filesystem if env vars didn't indicate serverless
# This avoids potential import-time errors
if not is_serverless:
    try:
        is_serverless = _is_read_only_fs()
    except Exception:
        # If filesystem check fails, default to non-serverless (safer for local dev)
        is_serverless = False

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

# Ensure static directory exists (skip in serverless/read-only environments)
# In Vercel, /var/task is read-only, so we cannot create directories there
# Static files should be served from the project directory directly
if not is_serverless:
    # Only try to create static directory in non-serverless environments
    try:
        if not os.path.exists(STATIC_DIR):
            os.makedirs(STATIC_DIR, exist_ok=True)
    except (OSError, PermissionError):
        # If we can't create it, that's okay - static files may already exist
        pass

LOGO_SRC_FILENAME = 'logoh.png'
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
    # STATIC_DIR is already created above (if not serverless)
    # Only create if not already created
    try:
        if not os.path.exists(STATIC_DIR):
            os.makedirs(STATIC_DIR, exist_ok=True)
    except (OSError, PermissionError):
        pass



def create_app():
    # Use instance_path for Vercel compatibility
    instance_path = INSTANCE_DIR if is_serverless else None
    
    # In serverless (Vercel), static files are served directly by Vercel
    # We should not set static_folder to a read-only path
    # Flask will use the default 'static' folder relative to the app root
    if is_serverless:
        # Check if static directory exists (read-only check)
        static_folder = STATIC_DIR if os.path.exists(STATIC_DIR) else None
    else:
        static_folder = STATIC_DIR
    
    app = Flask(__name__, 
                template_folder=TEMPLATE_DIR, 
                static_folder=static_folder,
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
            # PostgreSQL-specific: enable autocommit mode to avoid transaction issues
            if 'postgresql' in database_url or 'postgres' in database_url:
                try:
                    # Register event listener to handle transaction errors
                    @event.listens_for(Engine, "connect")
                    def set_postgres_pragmas(dbapi_connection, connection_record):
                        """PostgreSQL connection setup"""
                        try:
                            if hasattr(dbapi_connection, 'cursor'):
                                cursor = dbapi_connection.cursor()
                                # Don't set autocommit here - let SQLAlchemy manage transactions
                                # But ensure connection is clean
                                cursor.close()
                        except Exception:
                            pass
                except Exception as e:
                    try:
                        import sys
                        sys.stderr.write(f"Warning: Failed to register PostgreSQL event: {e}\n")
                    except Exception:
                        pass
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
            # Log a clear warning about ephemeral storage on serverless
            try:
                import sys
                sys.stderr.write(
                    "WARNING: Using ephemeral SQLite on serverless (/tmp). "
                    "Data will be lost on cold start. Set DATABASE_URL to a persistent DB.\n"
                )
                sys.stderr.write(f"DB_FILE: {tmp_db_path}\n")
            except Exception:
                pass
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
    
    # Initialize extensions with app - CRITICAL for serverless
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'login'
    
    # Verify extensions are attached
    if not hasattr(app, 'extensions') or 'sqlalchemy' not in app.extensions:
        try:
            import sys
            sys.stderr.write("WARNING: SQLAlchemy not properly attached to app\n")
            # Force re-init
            db.init_app(app)
        except Exception as e:
            try:
                import sys
                sys.stderr.write(f"WARNING: Failed to re-init db: {e}\n")
            except Exception:
                pass
    
    if not hasattr(app, 'login_manager'):
        try:
            import sys
            sys.stderr.write("WARNING: LoginManager not properly attached to app\n")
            # Force re-init
            login_manager.init_app(app)
            login_manager.login_view = 'login'
        except Exception as e:
            try:
                import sys
                sys.stderr.write(f"WARNING: Failed to re-init login_manager: {e}\n")
            except Exception:
                pass
    
    # Ensure tzlocal is available in Jinja templates
    try:
        app.jinja_env.globals['tzlocal'] = tzlocal
    except Exception:
        pass
    # Register SQLite pragma after app is created
    register_sqlite_pragma()
    try:
        import sys
        sys.stderr.write("✓ Flask app created successfully\n")
    except Exception:
        print("✓ Flask app created successfully")
except Exception as e:
    import traceback
    error_msg = f"CRITICAL: App creation failed: {e}\n{traceback.format_exc()}"
    try:
        import sys
        sys.stderr.write(f"VERCEL_ERROR: {error_msg}\n")
    except Exception:
        print(error_msg)
    # Create minimal error app instead of crashing
    # But still try to create db and login_manager for Vercel compatibility
    try:
        app = Flask(__name__)
        db = SQLAlchemy()
        login_manager = LoginManager()
        # Try to initialize if possible
        try:
            db.init_app(app)
            login_manager.init_app(app)
            login_manager.login_view = 'login'
        except Exception:
            pass
    except Exception:
        app = Flask(__name__)
        db = None
        login_manager = None

# Add custom Jinja filter for datetime formatting (only if app exists)
if app is not None:
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
    """로고 파일이 없으면 원본에서 복사 - 안전하게 처리"""
    try:
        os.makedirs(STATIC_DIR, exist_ok=True)
    except (OSError, PermissionError) as e:
        # Cannot create static directory - skip logo setup
        try:
            import sys
            sys.stderr.write(f"Warning: Cannot create static directory: {e}\n")
        except Exception:
            pass
        return
    
    dst = os.path.join(STATIC_DIR, 'logoh.png')
    
    # 이미 존재하고 크기가 0보다 크면 완료
    try:
        if os.path.exists(dst):
            file_size = os.path.getsize(dst)
            if file_size > 0:
                return
    except (OSError, IOError, PermissionError):
        # Cannot check existing file - will try to copy
        pass
    except Exception:
        # Other errors - skip
        return
    
    # 원본 로고 파일 경로들 시도
    original_logo_paths = [
        LOGO_SOURCE_PATH_IN_CONTAINER,  # 컨테이너 내부: /app/logoh.png (repo 동봉)
        os.path.join(BASE_DIR, 'logoh.png'),  # 프로젝트 루트의 logoh.png
        '/Users/USER/dev/busan/logoh.png',  # 호스트 절대 경로 (개발 환경)
    ]
    
    for src in original_logo_paths:
        try:
            # Check if source exists and is readable
            if not os.path.exists(src):
                continue
                
            try:
                file_size = os.path.getsize(src)
                if file_size <= 0:
                    continue
            except (OSError, IOError, PermissionError):
                # Cannot read source file - skip
                continue
            
            # Try to copy
            if is_serverless:
                # Vercel 환경에서는 복사 시도 (실패해도 계속)
                try:
                    shutil.copy(src, dst)
                    try:
                        import sys
                        sys.stderr.write(f"✓ Logo copied from {src} to {dst}\n")
                    except Exception:
                        pass
                    return
                except (OSError, IOError, PermissionError) as e:
                    # Cannot write to static in serverless - this is expected
                    try:
                        import sys
                        sys.stderr.write(f"Info: Cannot copy logo in serverless (expected): {e}\n")
                    except Exception:
                        pass
                    # Continue - will serve from source path directly
                    continue
                except Exception as e:
                    # Other errors
                    try:
                        import sys
                        sys.stderr.write(f"Warning: Error copying logo: {e}\n")
                    except Exception:
                        pass
                    continue
            else:
                # 로컬 환경: 정상 복사
                try:
                    shutil.copy(src, dst)
                    return
                except (OSError, IOError, PermissionError) as e:
                    try:
                        import sys
                        sys.stderr.write(f"Warning: Could not copy logo: {e}\n")
                    except Exception:
                        pass
                    continue
        except Exception as e:
            # Any other error - log and continue to next path
            try:
                import sys
                sys.stderr.write(f"Warning: Error processing logo path {src}: {e}\n")
            except Exception:
                pass
            continue
    
    # 로고 파일이 없으면 빈 파일 생성 시도 (나중에 업로드 가능)
    if not is_serverless:
        try:
            try:
                with open(dst, 'wb') as f:
                    f.write(b'')
            except (OSError, IOError, PermissionError):
                # Cannot create empty file - skip
                pass
        except Exception:
            # Any other error - skip
            pass


# Models need db to be available - but handle gracefully for Vercel
# Always define models - they will be properly initialized when db is available
# For Vercel: models are defined conditionally but Member/InsuranceApplication classes always exist
_model_classes_defined = False

def define_models():
    """Define SQLAlchemy models - called once when db is available"""
    global PartnerGroup, AdminUser, Member, InsuranceApplication, _model_classes_defined
    
    if _model_classes_defined or db is None:
        return
    
    try:
        ModelBase = db.Model
        
        class PartnerGroup(ModelBase):
            """파트너그룹 모델"""
            id = db.Column(db.Integer, primary_key=True)
            name = db.Column(db.String(255), nullable=False)  # 파트너그룹명(상호)
            business_number = db.Column(db.String(64), unique=True, nullable=False)  # 사업자등록번호
            representative = db.Column(db.String(128), nullable=False)  # 대표자
            phone = db.Column(db.String(64), nullable=False)  # 유선번호
            mobile = db.Column(db.String(64))  # 휴대폰번호
            address = db.Column(db.String(255))  # 주소
            registration_cert_path = db.Column(db.String(512))  # 사업자등록증 첨부
            logo_path = db.Column(db.String(512))  # 사업장로고 첨부
            memo = db.Column(db.String(255))  # 비고
            created_at = db.Column(db.DateTime, default=lambda: datetime.now(KST))
            
            __table_args__ = (
                Index('idx_partner_group_created_at', 'created_at'),
                Index('idx_partner_group_business_number', 'business_number'),
            )
            
            # Relationships
            members = db.relationship('Member', backref='partner_group', lazy='dynamic', cascade='all, delete-orphan')
            insurance_applications = db.relationship('InsuranceApplication', backref='partner_group', lazy='dynamic', cascade='all, delete-orphan')
        
        class AdminUser(UserMixin, ModelBase):
            """총괄관리자 모델"""
            id = db.Column(db.Integer, primary_key=True)
            username = db.Column(db.String(120), unique=True, nullable=False)
            password_hash = db.Column(db.String(255), nullable=False)
            created_at = db.Column(db.DateTime, default=lambda: datetime.now(KST))
            
            def set_password(self, password: str) -> None:
                self.password_hash = generate_password_hash(password)

            def check_password(self, password: str) -> bool:
                return check_password_hash(self.password_hash, password)
        
        class Member(UserMixin, ModelBase):
            id = db.Column(db.Integer, primary_key=True)
            username = db.Column(db.String(120), nullable=False)  # unique 제거 (파트너그룹별로 중복 가능)
            password_hash = db.Column(db.String(255), nullable=False)
            company_name = db.Column(db.String(255))
            address = db.Column(db.String(255))
            business_number = db.Column(db.String(64))  # unique 제거 (파트너그룹별로 중복 가능)
            corporation_number = db.Column(db.String(64))
            representative = db.Column(db.String(128))
            phone = db.Column(db.String(64))
            mobile = db.Column(db.String(64))
            email = db.Column(db.String(255))
            registration_cert_path = db.Column(db.String(512))
            approval_status = db.Column(db.String(32), default='신청')  # 신청, 승인중, 승인
            role = db.Column(db.String(32), default='member')  # member, admin
            partner_group_id = db.Column(db.Integer, db.ForeignKey('partner_group.id'), nullable=True)  # 파트너그룹 관계
            created_at = db.Column(db.DateTime, default=lambda: datetime.now(KST))
            
            __table_args__ = (
                Index('idx_member_created_at', 'created_at'),
                Index('idx_member_partner_group', 'partner_group_id'),
                Index('idx_member_username_partner', 'username', 'partner_group_id'),  # 파트너그룹별 unique
                CheckConstraint("approval_status IN ('신청','승인중','승인')", name='ck_member_approval_status'),
                CheckConstraint("role IN ('member','admin')", name='ck_member_role'),
            )

            def set_password(self, password: str) -> None:
                self.password_hash = generate_password_hash(password)

            def check_password(self, password: str) -> bool:
                return check_password_hash(self.password_hash, password)

        class InsuranceApplication(ModelBase):
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
            insurance_policy_path = db.Column(db.String(512))  # 보험증권 파일 경로 (로컬 저장)
            insurance_policy_url = db.Column(db.String(512))  # 보험증권 URL (외부 링크)
            created_by_member_id = db.Column(db.Integer, db.ForeignKey('member.id'))
            partner_group_id = db.Column(db.Integer, db.ForeignKey('partner_group.id'), nullable=True)  # 파트너그룹 관계

            __table_args__ = (
                Index('idx_ins_app_created_by', 'created_by_member_id'),
                Index('idx_ins_app_partner_group', 'partner_group_id'),
                Index('idx_ins_app_desired', 'desired_start_date'),
                Index('idx_ins_app_created', 'created_at'),
                Index('idx_ins_app_approved', 'approved_at'),
                Index('idx_ins_app_status', 'status'),
                Index('idx_ins_app_start', 'start_at'),
                Index('idx_ins_app_car_plate', 'car_plate'),
                Index('idx_ins_app_vin', 'vin'),
                CheckConstraint("status IN ('신청','조합승인','가입','종료')", name='ck_ins_app_status'),
            )

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
        
        _model_classes_defined = True
        print("✓ Models defined successfully (with PartnerGroup and AdminUser)")
    except Exception as e:
        print(f"✗ Model definition failed: {e}")
        import traceback
        traceback.print_exc()

# Initialize models if db is available at module import time
if db is not None:
    define_models()
else:
    # Create minimal stub classes for import compatibility
    class Member(UserMixin):
        id = None
        username = None
        approval_status = None
        role = None
        business_number = None
        def set_password(self, password: str) -> None:
            pass
        def check_password(self, password: str) -> bool:
            return False

    class InsuranceApplication:
        id = None
        status = None
        def recompute_status(self) -> None:
            pass


# User loader - register conditionally
def load_user(user_id):
    try:
        if db is None or login_manager is None:
            return None
        # Ensure models are defined
        if not _model_classes_defined:
            try:
                define_models()
            except Exception:
                pass
        if _model_classes_defined:
            # 먼저 AdminUser에서 찾기 (총괄관리자)
            try:
                admin_user = db.session.get(AdminUser, int(user_id))
                if admin_user:
                    return admin_user
            except Exception:
                pass
            
            # 그 다음 Member에서 찾기 (일반 사용자)
            try:
                return db.session.get(Member, int(user_id))
            except Exception:
                pass
        return None
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
    
    # Ensure models are defined before creating tables
    try:
        define_models()
    except Exception as e:
        print(f"Warning: Model definition failed: {e}")
        import traceback
        traceback.print_exc()
    
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
        # Logo setup failure should not crash the app
        try:
            import sys
            sys.stderr.write(f"Warning: Logo setup failed: {e}\n")
            import traceback
            sys.stderr.write(traceback.format_exc())
        except Exception:
            print(f"Warning: Logo setup failed: {e}")
        # Continue - logo is not critical for app functionality
        pass

    # 스키마 보정: 컬럼이 없으면 추가
    try:
        from sqlalchemy import text, inspect
        db_uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
        
        if 'sqlite' in db_uri:
            # Member 테이블: role 컬럼 추가 (SQLite)
            res = db.session.execute(text("PRAGMA table_info(member)"))
            cols = [r[1] for r in res.fetchall()]
            if 'role' not in cols:
                if not is_serverless:  # Vercel 환경이 아니면 스키마 변경
                    db.session.execute(text("ALTER TABLE member ADD COLUMN role TEXT NOT NULL DEFAULT 'member'"))
                    safe_commit()  # Schema migration - don't show error if it fails
            
            # InsuranceApplication 테이블: 보험증권 필드 추가 (SQLite)
            res = db.session.execute(text("PRAGMA table_info(insurance_application)"))
            cols = [r[1] for r in res.fetchall()]
            
            if 'insurance_policy_path' not in cols:
                if not is_serverless:
                    try:
                        db.session.execute(text("ALTER TABLE insurance_application ADD COLUMN insurance_policy_path TEXT"))
                        safe_commit()
                        print("Added insurance_policy_path column to insurance_application table")
                    except Exception as e:
                        print(f"Warning: Failed to add insurance_policy_path: {e}")
            
            if 'insurance_policy_url' not in cols:
                if not is_serverless:
                    try:
                        db.session.execute(text("ALTER TABLE insurance_application ADD COLUMN insurance_policy_url TEXT"))
                        safe_commit()
                        print("Added insurance_policy_url column to insurance_application table")
                    except Exception as e:
                        print(f"Warning: Failed to add insurance_policy_url: {e}")
            
            # Member 테이블: partner_group_id 컬럼 추가 (SQLite)
            if 'partner_group_id' not in cols:
                if not is_serverless:
                    try:
                        db.session.execute(text("ALTER TABLE member ADD COLUMN partner_group_id INTEGER"))
                        safe_commit()
                        print("Added partner_group_id column to member table")
                    except Exception as e:
                        print(f"Warning: Failed to add partner_group_id to member: {e}")
            
            # InsuranceApplication 테이블: partner_group_id 컬럼 추가 (SQLite)
            res = db.session.execute(text("PRAGMA table_info(insurance_application)"))
            cols = [r[1] for r in res.fetchall()]
            if 'partner_group_id' not in cols:
                if not is_serverless:
                    try:
                        db.session.execute(text("ALTER TABLE insurance_application ADD COLUMN partner_group_id INTEGER"))
                        safe_commit()
                        print("Added partner_group_id column to insurance_application table")
                    except Exception as e:
                        print(f"Warning: Failed to add partner_group_id to insurance_application: {e}")
        elif 'postgresql' in db_uri or 'postgres' in db_uri:
            # PostgreSQL: 컬럼 존재 여부 확인 후 추가
            inspector = inspect(db.engine)
            
            # Member 테이블: role 컬럼 추가 (PostgreSQL)
            member_cols = [col['name'] for col in inspector.get_columns('member')]
            if 'role' not in member_cols:
                if not is_serverless:
                    try:
                        db.session.execute(text("ALTER TABLE member ADD COLUMN role VARCHAR(32) NOT NULL DEFAULT 'member'"))
                        safe_commit()
                        print("Added role column to member table (PostgreSQL)")
                    except Exception as e:
                        print(f"Warning: Failed to add role to member: {e}")
            
            # InsuranceApplication 테이블: 보험증권 필드 추가 (PostgreSQL)
            ins_app_cols = [col['name'] for col in inspector.get_columns('insurance_application')]
            
            if 'insurance_policy_path' not in ins_app_cols:
                if not is_serverless:
                    try:
                        db.session.execute(text("ALTER TABLE insurance_application ADD COLUMN insurance_policy_path VARCHAR(512)"))
                        safe_commit()
                        print("Added insurance_policy_path column to insurance_application table (PostgreSQL)")
                    except Exception as e:
                        print(f"Warning: Failed to add insurance_policy_path: {e}")
            
            if 'insurance_policy_url' not in ins_app_cols:
                if not is_serverless:
                    try:
                        db.session.execute(text("ALTER TABLE insurance_application ADD COLUMN insurance_policy_url VARCHAR(512)"))
                        safe_commit()
                        print("Added insurance_policy_url column to insurance_application table (PostgreSQL)")
                    except Exception as e:
                        print(f"Warning: Failed to add insurance_policy_url: {e}")
            
            # Member 테이블: partner_group_id 컬럼 추가 (PostgreSQL)
            if 'partner_group_id' not in member_cols:
                if not is_serverless:
                    try:
                        db.session.execute(text("ALTER TABLE member ADD COLUMN partner_group_id INTEGER"))
                        safe_commit()
                        print("Added partner_group_id column to member table (PostgreSQL)")
                    except Exception as e:
                        print(f"Warning: Failed to add partner_group_id to member: {e}")
            
            # InsuranceApplication 테이블: partner_group_id 컬럼 추가 (PostgreSQL)
            if 'partner_group_id' not in ins_app_cols:
                if not is_serverless:
                    try:
                        db.session.execute(text("ALTER TABLE insurance_application ADD COLUMN partner_group_id INTEGER"))
                        safe_commit()
                        print("Added partner_group_id column to insurance_application table (PostgreSQL)")
                    except Exception as e:
                        print(f"Warning: Failed to add partner_group_id to insurance_application: {e}")
    except Exception as e:
        print(f"Warning: Schema migration failed: {e}")
        import traceback
        traceback.print_exc()
        pass
    
    # 관리자 계정 생성/업데이트
    try:
        admin_username = 'admin'
        admin_password = 'admin123!@#'
        admin_business_number = '0000000000'
        
        # 1단계: username='admin'으로 먼저 찾기 (우선순위)
        admin = db.session.query(Member).filter(Member.username == admin_username).first()
        
        if not admin:
            # 2단계: business_number='0000000000'으로 찾기
            admin = db.session.query(Member).filter(Member.business_number == admin_business_number).first()
            
            if admin:
                # 기존 레코드가 있지만 username이 'admin'이 아님
                # username을 'admin'으로 변경하려고 시도
                # 만약 'admin'이 이미 다른 레코드에서 사용 중이면 처리
                existing_admin_username = db.session.query(Member).filter(
                    Member.username == admin_username,
                    Member.id != admin.id
                ).first()
                
                if existing_admin_username:
                    # 'admin' username이 다른 레코드에 있음 - 기존 레코드 사용
                    admin = existing_admin_username
                else:
                    # username을 'admin'으로 변경 가능
                    admin.username = admin_username
        
        # 3단계: role='admin'으로 찾기 (username이나 business_number로 찾지 못한 경우)
        if not admin:
            admin = db.session.query(Member).filter(Member.role == 'admin').first()
        
        if not admin:
            # 새 관리자 계정 생성
            # business_number 중복 확인
            existing_business = db.session.query(Member).filter(
                Member.business_number == admin_business_number
            ).first()
            
            if existing_business:
                # business_number가 이미 사용 중 - 기존 레코드 업데이트
                admin = existing_business
                admin.username = admin_username
            else:
                # 새로 생성 가능
                admin = Member(
                    username=admin_username,
                    company_name='부산자동차매매사업자조합',
                    business_number=admin_business_number,
                    representative='관리자',
                    approval_status='승인',
                    role='admin',
                )
                admin.set_password(admin_password)
                db.session.add(admin)
                if not safe_commit():
                    raise Exception("Failed to commit admin account creation")
                print(f'관리자 계정이 생성되었습니다. 아이디: {admin_username}, 비밀번호: {admin_password}')
                return  # 성공적으로 생성했으므로 종료
        
        # 기존 관리자 계정 업데이트
        needs_update = False
        
        if admin.username != admin_username:
            # username이 다른 경우, 중복 확인
            existing_username = db.session.query(Member).filter(
                Member.username == admin_username,
                Member.id != admin.id
            ).first()
            
            if not existing_username:
                admin.username = admin_username
                needs_update = True
        
        if admin.business_number != admin_business_number:
            # business_number가 다른 경우, 중복 확인
            existing_business = db.session.query(Member).filter(
                Member.business_number == admin_business_number,
                Member.id != admin.id
            ).first()
            
            if not existing_business:
                admin.business_number = admin_business_number
                needs_update = True
        
        if not getattr(admin, 'role', None) or admin.role != 'admin':
            admin.role = 'admin'
            needs_update = True
        
        if admin.approval_status != '승인':
            admin.approval_status = '승인'
            needs_update = True
        
        # 비밀번호는 항상 업데이트
        admin.set_password(admin_password)
        needs_update = True
        
        if needs_update:
            if not safe_commit():
                raise Exception("Failed to commit admin account update")
            print(f'관리자 계정 정보가 업데이트되었습니다. 아이디: {admin_username}, 비밀번호: {admin_password}')
        
    except IntegrityError as e:
        # UNIQUE constraint 오류 처리
        try:
            import sys
            sys.stderr.write(f"Admin account IntegrityError: {e}\n")
            db.session.rollback()
        except Exception:
            pass
        
        # 롤백 후 재시도: 기존 레코드 찾아서 업데이트
        try:
            admin = db.session.query(Member).filter(Member.username == admin_username).first()
            if not admin:
                admin = db.session.query(Member).filter(Member.business_number == admin_business_number).first()
            
            if admin:
                admin.username = admin_username
                admin.business_number = admin_business_number
                admin.role = 'admin'
                admin.approval_status = '승인'
                admin.set_password(admin_password)
                safe_commit()
                print(f'관리자 계정 정보가 업데이트되었습니다 (재시도). 아이디: {admin_username}, 비밀번호: {admin_password}')
        except Exception as retry_err:
            try:
                import sys
                sys.stderr.write(f"Admin account retry failed: {retry_err}\n")
            except Exception:
                pass
    
    except Exception as e:
        print(f"Warning: Admin account creation/update failed: {e}")
        import traceback
        traceback.print_exc()
        try:
            db.session.rollback()
        except Exception:
            pass
    
    # 총괄관리자 계정 생성/업데이트
    try:
        super_admin_username = 'insurance'
        super_admin_password = '#wecar1004'
        
        # AdminUser 테이블이 있는지 확인
        try:
            super_admin = db.session.query(AdminUser).filter(AdminUser.username == super_admin_username).first()
            
            if not super_admin:
                # 새 총괄관리자 계정 생성
                super_admin = AdminUser(
                    username=super_admin_username,
                )
                super_admin.set_password(super_admin_password)
                db.session.add(super_admin)
                if not safe_commit():
                    raise Exception("Failed to commit super admin account creation")
                print(f'총괄관리자 계정이 생성되었습니다. 아이디: {super_admin_username}, 비밀번호: {super_admin_password}')
            else:
                # 기존 총괄관리자 계정 비밀번호 업데이트
                super_admin.set_password(super_admin_password)
                if not safe_commit():
                    raise Exception("Failed to commit super admin account update")
                print(f'총괄관리자 계정 정보가 업데이트되었습니다. 아이디: {super_admin_username}, 비밀번호: {super_admin_password}')
        except Exception as e:
            # AdminUser 테이블이 아직 생성되지 않았을 수 있음 (첫 실행 시)
            print(f"Warning: Super admin account creation skipped (table may not exist yet): {e}")
            pass
    except Exception as e:
        print(f"Warning: Super admin account creation/update failed: {e}")
        import traceback
        traceback.print_exc()
        try:
            db.session.rollback()
        except Exception:
            pass

# Safe commit helper function
def safe_commit():
    """Safely commit database transaction with automatic rollback on error"""
    if db is None:
        return False
    try:
        db.session.commit()
        return True
    except Exception as e:
        error_str = str(e)
        try:
            import sys
            sys.stderr.write(f"DB commit error: {error_str}\n")
        except Exception:
            pass
        
        # Always rollback on error
        try:
            db.session.rollback()
        except Exception:
            pass
        
        # Check for PostgreSQL transaction errors
        if 'InFailedSqlTransaction' in error_str or 'current transaction is aborted' in error_str.lower():
            try:
                import sys
                sys.stderr.write("PostgreSQL transaction error detected, rolled back\n")
            except Exception:
                pass
            return False
        
        # Re-raise other exceptions
        raise

# Safe database transaction handler
def safe_db_operation(func):
    """Decorator to safely handle database operations with automatic rollback on error"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if db is None:
            flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
            return redirect(url_for('dashboard'))
        
        try:
            result = func(*args, **kwargs)
            # If function returns a response, commit before returning
            if hasattr(result, 'status_code') or isinstance(result, tuple):
                if not safe_commit():
                    flash('데이터 저장 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
            return result
        except Exception as e:
            # Always rollback on any exception
            try:
                db.session.rollback()
            except Exception:
                pass
            
            # Check if it's a PostgreSQL transaction error
            error_str = str(e)
            if 'InFailedSqlTransaction' in error_str or 'current transaction is aborted' in error_str.lower():
                try:
                    import sys
                    sys.stderr.write(f"PostgreSQL transaction error: {e}\n")
                except Exception:
                    pass
                flash('데이터베이스 트랜잭션 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
            else:
                # Re-raise other exceptions to be handled by error handler
                raise
            # Return redirect to prevent showing error page
            return redirect(request.url if request else url_for('dashboard'))
    return wrapper

# 관리자 권한 데코레이터
def admin_required(view):
    from functools import wraps
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            # Safely check authentication
            is_auth = False
            try:
                is_auth = getattr(current_user, 'is_authenticated', False)
            except Exception:
                # If current_user access fails, assume not authenticated
                pass
            
            if not is_auth:
                flash('로그인이 필요합니다.', 'warning')
                return redirect(url_for('login'))
            
            # Check admin role
            user_role = getattr(current_user, 'role', 'member')
            if user_role != 'admin':
                flash('관리자만 접근 가능합니다.', 'warning')
                return redirect(url_for('dashboard'))
            
            return view(*args, **kwargs)
        except Exception as e:
            # If any error occurs in the decorator, log and redirect
            try:
                import sys
                sys.stderr.write(f"Error in admin_required decorator: {e}\n")
            except Exception:
                pass
            flash('권한 확인 중 오류가 발생했습니다.', 'danger')
            return redirect(url_for('dashboard'))
    return wrapped

# 총괄관리자 권한 데코레이터
def super_admin_required(view):
    from functools import wraps
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            # Safely check authentication
            is_auth = False
            try:
                is_auth = getattr(current_user, 'is_authenticated', False)
            except Exception:
                pass
            
            if not is_auth:
                flash('로그인이 필요합니다.', 'warning')
                return redirect(url_for('login'))
            
            # Check if super admin (AdminUser) or session flag
            is_super = False
            try:
                # Check if current_user is AdminUser instance
                if hasattr(current_user, '__class__'):
                    from flask_login import UserMixin
                    # AdminUser는 UserMixin을 상속하지만, Member와는 다른 클래스
                    # 세션 플래그로 확인
                    is_super = session.get('is_super_admin', False)
                    # 또는 AdminUser 테이블에 존재하는지 확인
                    if not is_super and db is not None:
                        try:
                            admin_user = db.session.query(AdminUser).filter_by(id=current_user.id).first()
                            if admin_user:
                                is_super = True
                                session['is_super_admin'] = True
                        except Exception:
                            pass
            except Exception:
                pass
            
            if not is_super:
                flash('총괄관리자만 접근 가능합니다.', 'warning')
                return redirect(url_for('dashboard'))
            
            return view(*args, **kwargs)
        except Exception as e:
            try:
                import sys
                sys.stderr.write(f"Error in super_admin_required decorator: {e}\n")
            except Exception:
                pass
            flash('권한 확인 중 오류가 발생했습니다.', 'danger')
            return redirect(url_for('login'))
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
                try:
                    import sys
                    sys.stderr.write("Warning: ensure_initialized called without app context\n")
                except Exception:
                    pass
                return
            
            # CRITICAL: Ensure extensions are attached FIRST before any other operations
            try:
                if db is not None:
                    # Check if SQLAlchemy is already initialized
                    needs_db_init = True
                    if hasattr(current_app, 'extensions'):
                        if 'sqlalchemy' in current_app.extensions:
                            needs_db_init = False
                    if needs_db_init:
                        db.init_app(current_app)
                        try:
                            import sys
                            sys.stderr.write("✓ Database extension initialized\n")
                        except Exception:
                            pass
            except Exception as e:
                try:
                    import sys
                    sys.stderr.write(f"Warning: Failed to init db: {e}\n")
                except Exception:
                    pass
            
            try:
                if login_manager is not None and not hasattr(current_app, 'login_manager'):
                    login_manager.init_app(current_app)
                    login_manager.login_view = 'login'
                    try:
                        import sys
                        sys.stderr.write("✓ Login manager initialized\n")
                    except Exception:
                        pass
            except Exception as e:
                try:
                    import sys
                    sys.stderr.write(f"Warning: Failed to init login_manager: {e}\n")
                except Exception:
                    pass
            
            # Now init_db_and_assets can safely use current_app and db
            init_db_and_assets()
            _initialized = True
        except Exception as e:
            try:
                import sys
                sys.stderr.write(f"Warning: Initialization failed: {e}\n")
                import traceback
                sys.stderr.write(traceback.format_exc())
            except Exception:
                pass
            # Mark as initialized anyway to avoid infinite retry loops
            _initialized = True

# SQLite pragma registration is now done in register_sqlite_pragma() above

# Don't initialize at module level - wait for first request
# This avoids app context issues


# Register context processor only if app is available
if app is not None:
    @app.context_processor
    def inject_jinja_globals():
        # Make tzlocal available in Jinja templates
        try:
            using_ephemeral_db = False
            try:
                db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
                using_ephemeral_db = bool(is_serverless and db_uri.startswith('sqlite:///'))
            except Exception:
                using_ephemeral_db = False
            return {
                'tzlocal': tzlocal,
                'ephemeral_db': using_ephemeral_db,
            }
        except Exception:
            return {'tzlocal': tzlocal}

    @app.before_request
    def _ensure_login_manager_attached():
        """Ensure Flask-Login is attached before accessing current_user."""
        try:
            from flask import current_app
            if login_manager is not None and not hasattr(current_app, 'login_manager'):
                login_manager.init_app(current_app)
                login_manager.login_view = 'login'
        except Exception as e:
            # Log the error but don't crash - this is best-effort
            try:
                import sys
                sys.stderr.write(f"Warning: Failed to attach login_manager in before_request: {e}\n")
            except Exception:
                pass
    
    @app.after_request
    def _handle_db_transaction_errors(response):
        """Handle PostgreSQL transaction errors after each request"""
        if db is not None:
            try:
                # Check if there's an active transaction that failed
                # If session is in a bad state, rollback
                if db.session.is_active:
                    # Try to check if transaction is in error state
                    try:
                        from sqlalchemy import text
                        # Simple test query to check if transaction is healthy
                        db.session.execute(text('SELECT 1'))
                    except Exception as e:
                        error_str = str(e)
                        if 'InFailedSqlTransaction' in error_str or 'current transaction is aborted' in error_str.lower():
                            try:
                                db.session.rollback()
                                try:
                                    import sys
                                    sys.stderr.write("Rolled back failed transaction after request\n")
                                except Exception:
                                    pass
                            except Exception:
                                pass
            except Exception:
                # If we can't check, try to rollback anyway
                try:
                    db.session.rollback()
                except Exception:
                    pass
        return response


@app.route('/')
def index():
    try:
        ensure_initialized()  # Initialize on first request for Vercel
        # Safely check authentication status
        try:
            from flask_login import current_user
            is_auth = getattr(current_user, 'is_authenticated', False)
        except Exception:
            # If login_manager not ready, assume not authenticated
            is_auth = False
        
        if is_auth:
            return redirect(url_for('dashboard'))
        return redirect(url_for('login'))
    except Exception as e:
        import traceback
        error_msg = traceback.format_exc()
        try:
            import sys
            sys.stderr.write(f"ERROR in index route: {error_msg}\n")
        except Exception:
            pass
        return f"<h1>Error</h1><pre>{error_msg}</pre>", 500


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
    """Handle favicon requests - serve logoh.png as favicon or return 204"""
    try:
        # Try multiple paths for logo
        logo_paths = [
            os.path.join(STATIC_DIR, 'logoh.png'),
            os.path.join(BASE_DIR, 'logoh.png'),
            LOGO_SOURCE_PATH_IN_CONTAINER,
        ]
        
        for logo_path in logo_paths:
            try:
                if os.path.exists(logo_path):
                    file_size = os.path.getsize(logo_path)
                    if file_size > 0:
                        return send_file(logo_path, mimetype='image/png')
            except (OSError, IOError, PermissionError) as e:
                # File system errors - skip this path
                try:
                    import sys
                    sys.stderr.write(f"Warning: Could not access logo at {logo_path}: {e}\n")
                except Exception:
                    pass
                continue
            except Exception as e:
                # Other errors - log and continue
                try:
                    import sys
                    sys.stderr.write(f"Warning: Error checking logo at {logo_path}: {e}\n")
                except Exception:
                    pass
                continue
    except Exception as e:
        # If everything fails, log and return 204 (no content)
        try:
            import sys
            sys.stderr.write(f"Warning: favicon route error: {e}\n")
        except Exception:
            pass
    
    return '', 204

@app.route('/static/logoh.png')
@app.route('/static/logo.png')  # 하위 호환성을 위해 유지
def serve_logo():
    """Serve logoh.png from static directory or fallback to root"""
    try:
        # Try static directory first, then root
        logo_paths = [
            os.path.join(STATIC_DIR, 'logoh.png'),
            os.path.join(BASE_DIR, 'logoh.png'),
            LOGO_SOURCE_PATH_IN_CONTAINER,
        ]
        
        for logo_path in logo_paths:
            try:
                if os.path.exists(logo_path):
                    file_size = os.path.getsize(logo_path)
                    if file_size > 0:
                        return send_file(logo_path, mimetype='image/png')
            except (OSError, IOError, PermissionError) as e:
                # File system errors - skip this path
                try:
                    import sys
                    sys.stderr.write(f"Warning: Could not access logo at {logo_path}: {e}\n")
                except Exception:
                    pass
                continue
            except Exception as e:
                # Other errors - log and continue
                try:
                    import sys
                    sys.stderr.write(f"Warning: Error checking logo at {logo_path}: {e}\n")
                except Exception:
                    pass
                continue
    except Exception as e:
        # If everything fails, log and return 404
        try:
            import sys
            sys.stderr.write(f"Warning: serve_logo route error: {e}\n")
        except Exception:
            pass
    
    # If no logo found, return 404
    return '', 404

@app.route('/insurance/<int:insurance_id>/policy')
@login_required
def serve_insurance_policy(insurance_id):
    """Serve insurance policy PDF if available"""
    try:
        ensure_initialized()
        if db is None:
            flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
            return redirect(url_for('dashboard'))
        
        # Get insurance application
        insurance = db.session.get(InsuranceApplication, insurance_id)
        if not insurance:
            flash('보험 신청을 찾을 수 없습니다.', 'danger')
            return redirect(url_for('dashboard'))
        
        # Check permission: user can only view their own insurance policies unless admin
        user_role = getattr(current_user, 'role', 'member')
        if user_role != 'admin' and insurance.created_by_member_id != current_user.id:
            flash('권한이 없습니다.', 'danger')
            return redirect(url_for('dashboard'))
        
        # Check if insurance policy path exists
        if hasattr(insurance, 'insurance_policy_path') and insurance.insurance_policy_path:
            policy_path = insurance.insurance_policy_path
            # Handle both absolute and relative paths
            if not os.path.isabs(policy_path):
                # Try different possible locations
                possible_paths = [
                    os.path.join(UPLOAD_DIR, policy_path),
                    os.path.join(BASE_DIR, 'uploads', policy_path),
                    os.path.join(BASE_DIR, policy_path),
                ]
            else:
                possible_paths = [policy_path]
            
            for path in possible_paths:
                try:
                    if os.path.exists(path) and os.path.isfile(path):
                        return send_file(path, mimetype='application/pdf', as_attachment=False)
                except (OSError, IOError, PermissionError):
                    continue
        
        # If policy not found, return 404
        flash('보험증권 파일을 찾을 수 없습니다.', 'warning')
        return redirect(url_for('insurance'))
        
    except Exception as e:
        try:
            import sys
            sys.stderr.write(f"Error in serve_insurance_policy: {e}\n")
        except Exception:
            pass
        flash('보험증권 조회 중 오류가 발생했습니다.', 'danger')
        return redirect(url_for('dashboard'))

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
@app.route('/login/<int:partner_id>', methods=['GET', 'POST'])
def login(partner_id=None):
    try:
        ensure_initialized()  # Initialize on first request for Vercel
        
        # 파트너그룹 정보 가져오기
        partner_group = None
        partner_group_id = partner_id or request.args.get('partner_id', type=int)
        
        if db is not None and partner_group_id:
            try:
                partner_group = db.session.get(PartnerGroup, partner_group_id)
            except Exception:
                pass
        
        # GET 요청: 로그인 페이지 표시
        if request.method == 'GET':
            # 모든 파트너그룹 목록 가져오기
            partner_groups = []
            if db is not None:
                try:
                    partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                except Exception:
                    pass
            return render_template('auth/login.html', partner_group=partner_group, partner_groups=partner_groups)
        
        # POST 요청: 로그인 처리
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            form_partner_id = request.form.get('partner_id', type=int)
            
            # 폼에서 파트너그룹 ID를 받았으면 사용
            if form_partner_id:
                partner_group_id = form_partner_id
                try:
                    partner_group = db.session.get(PartnerGroup, partner_group_id)
                except Exception:
                    pass
            
            if not username or not password:
                flash('아이디와 비밀번호를 입력해주세요.', 'warning')
                # 모든 파트너그룹 목록 가져오기
                partner_groups = []
                if db is not None:
                    try:
                        partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
                    except Exception:
                        pass
                return render_template('auth/login.html', partner_group=partner_group, partner_groups=partner_groups)
            
            if db is None:
                try:
                    import sys
                    sys.stderr.write("Login error: db is None\n")
                except Exception:
                    pass
                flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
                return render_template('auth/login.html')
            
            # Ensure models are defined
            try:
                if not _model_classes_defined:
                    define_models()
            except Exception as e:
                try:
                    import sys
                    sys.stderr.write(f"Login error: Model definition failed: {e}\n")
                except Exception:
                    pass
                flash('시스템 초기화 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.', 'danger')
                return render_template('auth/login.html')
            
            try:
                # 먼저 총괄관리자 계정인지 확인
                try:
                    super_admin = db.session.query(AdminUser).filter_by(username=username).first()
                    if super_admin and super_admin.check_password(password):
                        # 총괄관리자 로그인 - 세션에 총괄관리자 플래그 설정
                        login_user(super_admin)
                        session['is_super_admin'] = True
                        return redirect(url_for('super_dashboard'))
                except Exception:
                    # AdminUser 테이블이 없거나 쿼리 실패 시 무시하고 일반 사용자 로그인 시도
                    pass
                
                # 일반 사용자 로그인 처리
                # Query user with error handling
                try:
                    # 파트너그룹이 지정된 경우 해당 파트너그룹의 사용자만 조회
                    if partner_group_id:
                        user = db.session.query(Member).filter_by(
                            username=username,
                            partner_group_id=partner_group_id
                        ).first()
                    else:
                        user = db.session.query(Member).filter_by(username=username).first()
                except Exception as query_err:
                    try:
                        import sys
                        import traceback
                        sys.stderr.write(f"Login query error: {query_err}\n")
                        sys.stderr.write(traceback.format_exc())
                    except Exception:
                        pass
                    # Try to rollback and retry
                    try:
                        db.session.rollback()
                        if partner_group_id:
                            user = db.session.query(Member).filter_by(
                                username=username,
                                partner_group_id=partner_group_id
                            ).first()
                        else:
                            user = db.session.query(Member).filter_by(username=username).first()
                    except Exception as retry_err:
                        try:
                            import sys
                            sys.stderr.write(f"Login retry query error: {retry_err}\n")
                        except Exception:
                            pass
                        flash('로그인 처리 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
                        return render_template('auth/login.html', partner_group=partner_group)
                
                if user:
                    # Check password with error handling
                    try:
                        password_valid = user.check_password(password)
                    except Exception as pwd_err:
                        try:
                            import sys
                            sys.stderr.write(f"Login password check error: {pwd_err}\n")
                        except Exception:
                            pass
                        flash('비밀번호 확인 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
                        return render_template('auth/login.html', partner_group=partner_group)
                    
                    if password_valid:
                        # 파트너그룹이 지정된 경우 사용자의 파트너그룹 확인
                        if partner_group_id and user.partner_group_id != partner_group_id:
                            flash('해당 파트너그룹의 계정이 아닙니다.', 'danger')
                            return render_template('auth/login.html', partner_group=partner_group)
                        
                        # Check approval status
                        try:
                            approval_status = getattr(user, 'approval_status', None)
                            if approval_status != '승인':
                                flash('관리자 승인 후 로그인 가능합니다.', 'warning')
                                if partner_group_id:
                                    return redirect(url_for('login', partner_id=partner_group_id))
                                return redirect(url_for('login'))
                            
                            # Login user
                            try:
                                login_user(user)
                                return redirect(url_for('dashboard'))
                            except Exception as login_err:
                                try:
                                    import sys
                                    sys.stderr.write(f"Login user error: {login_err}\n")
                                except Exception:
                                    pass
                                flash('로그인 처리 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
                                return render_template('auth/login.html', partner_group=partner_group)
                        except Exception as status_err:
                            try:
                                import sys
                                sys.stderr.write(f"Login approval check error: {status_err}\n")
                            except Exception:
                                pass
                            flash('로그인 처리 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
                            return render_template('auth/login.html', partner_group=partner_group)
                    else:
                        flash('아이디 또는 비밀번호가 올바르지 않습니다.', 'danger')
                else:
                    flash('아이디 또는 비밀번호가 올바르지 않습니다.', 'danger')
                
                return render_template('auth/login.html', partner_group=partner_group)
                    
            except Exception as e:
                try:
                    import sys
                    import traceback
                    sys.stderr.write(f"Login processing error: {e}\n")
                    sys.stderr.write(traceback.format_exc())
                except Exception:
                    pass
                flash('로그인 처리 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
                return render_template('auth/login.html', partner_group=partner_group)
        
        return render_template('auth/login.html', partner_group=partner_group)
    except Exception as e:
        try:
            import sys
            import traceback
            sys.stderr.write(f"Login route error: {e}\n")
            sys.stderr.write(traceback.format_exc())
        except Exception:
            pass
        flash('로그인 페이지 로드 중 오류가 발생했습니다.', 'danger')
        try:
            return render_template('auth/login.html')
        except Exception:
            return "로그인 페이지를 불러올 수 없습니다.", 500


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
@app.route('/register/<int:partner_id>', methods=['GET', 'POST'])
def register(partner_id=None):
    try:
        ensure_initialized()  # Initialize on first request for Vercel
        
        # 파트너그룹 정보 가져오기
        partner_group = None
        partner_group_id = partner_id or request.args.get('partner_id', type=int)
        
        if db is not None and partner_group_id:
            try:
                partner_group = db.session.get(PartnerGroup, partner_group_id)
            except Exception:
                pass
        
        # GET 요청: 회원가입 페이지 표시
        if request.method == 'GET':
            return render_template('auth/register.html', partner_group=partner_group)
        
        # POST 요청: 회원가입 처리
        if request.method == 'POST':
            form_partner_id = request.form.get('partner_id', type=int)
            if form_partner_id:
                partner_group_id = form_partner_id
                try:
                    partner_group = db.session.get(PartnerGroup, partner_group_id)
                except Exception:
                    pass
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

            if db is None:
                flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
                return render_template('auth/register.html')
            
            try:
                # Check for duplicate username (파트너그룹별로 중복 확인)
                query = db.session.query(Member).filter_by(username=username)
                if partner_group_id:
                    query = query.filter_by(partner_group_id=partner_group_id)
                if query.first():
                    flash('이미 존재하는 아이디입니다.', 'danger')
                    return render_template('auth/register.html', partner_group=partner_group)
                
                # Check for duplicate business number (파트너그룹별로 중복 확인)
                if business_number:
                    query = db.session.query(Member).filter_by(business_number=business_number)
                    if partner_group_id:
                        query = query.filter_by(partner_group_id=partner_group_id)
                    if query.first():
                        flash('이미 등록된 사업자번호입니다.', 'danger')
                        return render_template('auth/register.html', partner_group=partner_group)
            except Exception as e:
                try:
                    import sys
                    sys.stderr.write(f"Register duplicate check error: {e}\n")
                except Exception:
                    pass
                flash('회원 정보 확인 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
                return render_template('auth/register.html')

            # 파일 업로드 처리
            registration_cert_path = None # Vercel 환경에서는 파일 업로드 비활성화
            if not is_serverless and 'registration_cert' in request.files:
                try:
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
                except Exception as e:
                    try:
                        import sys
                        sys.stderr.write(f"File upload error: {e}\n")
                    except Exception:
                        pass
                    # Continue without file - not critical
            
            try:
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
                    partner_group_id=partner_group_id,
                )
                member.set_password(password)
                db.session.add(member)
                if not safe_commit():
                    flash('회원가입 처리 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
                    return render_template('auth/register.html', partner_group=partner_group)
                flash('신청이 접수되었습니다. 관리자 승인 후 로그인 가능합니다.', 'success')
                if partner_group_id:
                    return redirect(url_for('login', partner_id=partner_group_id))
                return redirect(url_for('login'))
            except Exception as e:
                try:
                    import sys
                    sys.stderr.write(f"Register member creation error: {e}\n")
                except Exception:
                    pass
                flash('회원가입 처리 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
                return render_template('auth/register.html', partner_group=partner_group)

        return render_template('auth/register.html', partner_group=partner_group)
    except Exception as e:
        try:
            import sys
            sys.stderr.write(f"Register route error: {e}\n")
        except Exception:
            pass
        flash('회원가입 페이지 로드 중 오류가 발생했습니다.', 'danger')
        try:
            return render_template('auth/register.html')
        except Exception:
            return "회원가입 페이지를 불러올 수 없습니다.", 500


@app.route('/dashboard')
@login_required
def dashboard():
    try:
        ensure_initialized()  # Ensure initialization
        return render_template('dashboard.html')
    except Exception as e:
        try:
            import sys
            sys.stderr.write(f"Dashboard route error: {e}\n")
        except Exception:
            pass
        flash('대시보드 로드 중 오류가 발생했습니다.', 'danger')
        try:
            return render_template('dashboard.html')
        except Exception:
            # If template rendering fails, redirect to login
            try:
                return redirect(url_for('login'))
            except Exception:
                return "대시보드를 불러올 수 없습니다.", 500


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """업로드된 파일 제공"""
    try:
        file_path = os.path.join(UPLOAD_DIR, filename)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            # 파일 확장자에 따라 MIME 타입 결정
            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
            mimetype_map = {
                'pdf': 'application/pdf',
                'jpg': 'image/jpeg',
                'jpeg': 'image/jpeg',
                'png': 'image/png',
                'gif': 'image/gif',
            }
            mimetype = mimetype_map.get(ext, 'application/octet-stream')
            return send_file(file_path, mimetype=mimetype)
        else:
            # 파일이 없으면 기본 로고로 폴백
            fallback_paths = [
                os.path.join(STATIC_DIR, 'logoh.png'),
                os.path.join(BASE_DIR, 'logoh.png'),
                LOGO_SOURCE_PATH_IN_CONTAINER,
            ]
            for fallback_path in fallback_paths:
                if os.path.exists(fallback_path) and os.path.isfile(fallback_path):
                    return send_file(fallback_path, mimetype='image/png')
            return 'File not found', 404
    except Exception as e:
        try:
            import sys
            sys.stderr.write(f"Error serving uploaded file {filename}: {e}\n")
        except Exception:
            pass
        # 에러 발생 시 기본 로고로 폴백
        try:
            fallback_path = os.path.join(BASE_DIR, 'logoh.png')
            if os.path.exists(fallback_path):
                return send_file(fallback_path, mimetype='image/png')
        except Exception:
            pass
        return 'Error serving file', 500


@app.route('/terms')
@login_required
def terms():
    ensure_initialized()  # Ensure initialization
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
    ensure_initialized()  # Ensure initialization
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
                            if not safe_commit():
                                flash('삭제 처리 중 오류가 발생했습니다.', 'danger')
                            else:
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
                            if not safe_commit():
                                flash('저장 처리 중 오류가 발생했습니다.', 'danger')
                            else:
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

        # 현재 사용자의 partner_group_id 가져오기
        partner_group_id = None
        if hasattr(current_user, 'partner_group_id'):
            partner_group_id = current_user.partner_group_id
        
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
            partner_group_id=partner_group_id,
        )
        db.session.add(app_row)
        if not safe_commit():
            flash('신청 처리 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
            return redirect(url_for('insurance'))
        flash('신청이 등록되었습니다.', 'success')
        return redirect(url_for('insurance'))

    # 검색
    start_date = parse_date(request.args.get('start_date', ''))
    end_date = parse_date(request.args.get('end_date', ''))
    edit_id = request.args.get('edit_id')  # 편집 모드

    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('dashboard'))
    
    # 현재 사용자의 partner_group_id 가져오기
    partner_group_id = None
    if hasattr(current_user, 'partner_group_id'):
        partner_group_id = current_user.partner_group_id
    
    # 파트너그룹별 필터링 (본인 소유 데이터만)
    q = db.session.query(InsuranceApplication).filter_by(created_by_member_id=current_user.id)
    if partner_group_id:
        q = q.filter_by(partner_group_id=partner_group_id)
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
        safe_commit()  # Don't show error if status update fails, just log it

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
        if not safe_commit():
            flash('업로드 처리 중 오류가 발생했습니다. 다시 시도해주세요.', 'danger')
        else:
            flash(f'{count}건 업로드되었습니다.', 'success')
    except Exception as e:
        flash('업로드 중 오류가 발생했습니다.', 'danger')
    return redirect(url_for('insurance'))


@app.route('/admin')
@login_required
@admin_required
def admin_home():
    ensure_initialized()  # Ensure initialization
    return render_template('admin/index.html')


@app.route('/admin/members', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_members():
    ensure_initialized()  # Ensure initialization
    if request.method == 'POST':
        action = request.form.get('action')
        member_id = request.form.get('member_id')
        if member_id:
            m = db.session.get(Member, int(member_id))
            if m:
                if action == 'update_status':
                    m.approval_status = request.form.get('approval_status', '신청')
                    if not safe_commit():
                        flash('승인 상태 변경 중 오류가 발생했습니다.', 'danger')
                    else:
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
                    if not safe_commit():
                        flash('저장 처리 중 오류가 발생했습니다.', 'danger')
                    else:
                        flash('저장되었습니다.', 'success')
                    return redirect(url_for('admin_members'))
                elif action == 'delete':
                    db.session.delete(m)
                    if not safe_commit():
                        flash('삭제 처리 중 오류가 발생했습니다.', 'danger')
                    else:
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
                elif db.session.query(Member).filter((Member.username == username) | (Member.business_number == business_number)).first():
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
                    if not safe_commit():
                        flash('회원 추가 중 오류가 발생했습니다.', 'danger')
                    else:
                        flash('회원이 추가되었습니다.', 'success')
                return redirect(url_for('admin_members'))

    edit_id = request.args.get('edit_id')
    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('dashboard'))
    members = db.session.query(Member).order_by(Member.created_at.desc()).all()
    return render_template('admin/members.html', members=members, edit_id=edit_id)


@app.route('/admin/members/upload', methods=['POST'])
@login_required
@admin_required
def admin_members_upload():
    ensure_initialized()  # Ensure initialization
    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('admin_members'))
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
            if db.session.query(Member).filter((Member.username == username) | (Member.business_number == business_number)).first():
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
        if not safe_commit():
            flash('일괄 업로드 처리 중 오류가 발생했습니다.', 'danger')
        else:
            flash(f'일괄 업로드 완료: 추가 {created}건, 건너뜀 {skipped}건', 'success')
    except Exception:
        flash('업로드 처리 중 오류가 발생했습니다.', 'danger')
    return redirect(url_for('admin_members'))


@app.route('/admin/insurance', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_insurance():
    ensure_initialized()  # Ensure initialization
    if request.method == 'POST':
        if request.form.get('bulk_approve') == '1':
            # 일괄 승인: 미승인(= status == 신청)인 데이터 모두 승인 시간 부여
            if db is None:
                flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
                return redirect(url_for('admin_insurance'))
            # 현재 관리자의 partner_group_id 가져오기
            partner_group_id = None
            if hasattr(current_user, 'partner_group_id'):
                partner_group_id = current_user.partner_group_id
            
            query = db.session.query(InsuranceApplication).filter(
                (InsuranceApplication.approved_at.is_(None))
            )
            if partner_group_id:
                query = query.filter_by(partner_group_id=partner_group_id)
            rows = query.all()
            now = datetime.now(KST)
            for r in rows:
                r.approved_at = now
                r.status = '조합승인'
                # 가입일/종료일 설정: 가입희망일자 기준으로 세팅, 종료는 30일 후
                if not r.start_at:
                    start_date_aware = datetime.combine(r.desired_start_date, datetime.min.time(), tzinfo=KST)
                    r.start_at = start_date_aware
                    r.end_at = start_date_aware + timedelta(days=30)
            if not safe_commit():
                flash('일괄 승인 처리 중 오류가 발생했습니다.', 'danger')
            else:
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
                    if not safe_commit():
                        flash('승인 처리 중 오류가 발생했습니다.', 'danger')
                    else:
                        flash('승인되었습니다.', 'success')
                        print(f"DEBUG: Approval completed for row {row.id}")  # 디버그 로그
                elif action == 'delete':
                    db.session.delete(row)
                    if not safe_commit():
                        flash('삭제 처리 중 오류가 발생했습니다.', 'danger')
                    else:
                        flash('삭제되었습니다.', 'success')
                elif action == 'save_memo':
                    row.memo = request.form.get('memo', row.memo)
                    if not safe_commit():
                        flash('비고 저장 중 오류가 발생했습니다.', 'danger')
                    else:
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
                    # 보험증권 정보 저장
                    if hasattr(row, 'insurance_policy_path'):
                        row.insurance_policy_path = request.form.get('insurance_policy_path', '').strip() or None
                    if hasattr(row, 'insurance_policy_url'):
                        row.insurance_policy_url = request.form.get('insurance_policy_url', '').strip() or None
                    if not safe_commit():
                        flash('저장 처리 중 오류가 발생했습니다.', 'danger')
                    else:
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

    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('dashboard'))
    
    # 현재 관리자의 partner_group_id 가져오기
    partner_group_id = None
    if hasattr(current_user, 'partner_group_id'):
        partner_group_id = current_user.partner_group_id
    
    q = db.session.query(InsuranceApplication)
    if partner_group_id:
        q = q.filter_by(partner_group_id=partner_group_id)
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
    safe_commit()  # Status updates - don't show error if it fails

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
    ensure_initialized()  # Ensure initialization
    # Import pandas only when needed
    import pandas as pd
    
    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('admin_insurance'))
    # Export to Excel
    # 현재 관리자의 partner_group_id 가져오기
    partner_group_id = None
    if hasattr(current_user, 'partner_group_id'):
        partner_group_id = current_user.partner_group_id
    
    query = db.session.query(InsuranceApplication)
    if partner_group_id:
        query = query.filter_by(partner_group_id=partner_group_id)
    rows = query.order_by(InsuranceApplication.created_at.desc()).all()
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
    ensure_initialized()  # Ensure initialization
    year = int(request.args.get('year', datetime.now().year))
    month = int(request.args.get('month', datetime.now().month))

    # 기준: 책임보험 승인페이지에서 해당 년/월 데이터 (시작일 기준)
    start_period = datetime(year, month, 1, tzinfo=KST)
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=KST)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=KST)

    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('admin_settlement'))
    
    # 현재 관리자의 partner_group_id 가져오기
    partner_group_id = None
    if hasattr(current_user, 'partner_group_id'):
        partner_group_id = current_user.partner_group_id
    
    query = db.session.query(InsuranceApplication).filter(
        InsuranceApplication.start_at.is_not(None),
        InsuranceApplication.start_at >= start_period,
        InsuranceApplication.start_at < next_month,
    )
    if partner_group_id:
        query = query.filter_by(partner_group_id=partner_group_id)
    rows = query.all()

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
    total_count = 0
    total_amount = 0
    for (company, rep, biz), count in by_company.items():
        amount = count * 9500
        total_count += count
        total_amount += amount
        settlements.append({
            'company': company,
            'representative': rep,
            'business_number': biz,
            'count': count,
            'amount': amount,
        })

    # Ensure total_count and total_amount are always integers (not None)
    total_count = int(total_count) if total_count else 0
    total_amount = int(total_amount) if total_amount else 0

    return render_template('admin/settlement.html', 
                          year=year, 
                          month=month, 
                          settlements=settlements, 
                          total_count=total_count, 
                          total_amount=total_amount)


@app.route('/admin/invoice')
@login_required
@admin_required
def admin_invoice():
    ensure_initialized()  # Ensure initialization
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
    ensure_initialized()  # Ensure initialization
    # Render a combined printable page for all companies for the selected month
    year = int(request.args.get('year', datetime.now().year))
    month = int(request.args.get('month', datetime.now().month))

    start_period = datetime(year, month, 1, tzinfo=KST)
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=KST)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=KST)

    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('admin_settlement'))
    rows = db.session.query(InsuranceApplication).filter(
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
    """Handle all unhandled exceptions with detailed logging"""
    # Let Flask/Flask-Login handle HTTPExceptions (e.g., 401 login required)
    if isinstance(e, HTTPException):
        return e
    
    # Log full stack trace for debugging
    import traceback
    error_trace = traceback.format_exc()
    error_msg = str(e)
    error_type = type(e).__name__
    
    # Log to stderr for Vercel (more reliable than app.logger)
    try:
        import sys
        sys.stderr.write(f"\n{'='*60}\n")
        sys.stderr.write(f"UNHANDLED EXCEPTION: {error_type}\n")
        sys.stderr.write(f"Error Message: {error_msg}\n")
        sys.stderr.write(f"Traceback:\n{error_trace}\n")
        sys.stderr.write(f"{'='*60}\n")
    except Exception:
        pass
    
    # Also log via Flask logger if available
    try:
        app.logger.exception("Unhandled exception")
    except Exception:
        pass
    
    # Try to provide user-friendly error message
    try:
        from flask import request, has_request_context
        
        if has_request_context():
            # Only flash if we're in a request context
            try:
                flash('서버 처리 중 오류가 발생했습니다.', 'danger')
            except Exception:
                pass
            
            # Try to redirect appropriately
            try:
                # Check authentication status safely
                is_auth = False
                try:
                    from flask_login import current_user
                    is_auth = getattr(current_user, 'is_authenticated', False)
                except Exception:
                    pass
                
                if not is_auth:
                    try:
                        return redirect(url_for('login'))
                    except Exception:
                        pass
                else:
                    try:
                        return redirect(url_for('dashboard'))
                    except Exception:
                        pass
            except Exception:
                pass
        
        # If redirect failed, show error page
        try:
            from flask import render_template
            return render_template('error.html', error_message=error_msg), 500
        except Exception:
            pass
        
    except Exception:
        pass
    
    # Ultimate fallback: return minimal error response
    try:
        return f"<h1>서버 오류</h1><p>오류가 발생했습니다: {error_type}</p>", 500
    except Exception:
        return ("서버 오류가 발생했습니다.", 500)


# ============================================================================
# 상위 프로그램 라우트 (총괄관리자)
# ============================================================================

@app.route('/super/dashboard')
@login_required
@super_admin_required
def super_dashboard():
    """총괄관리자 대시보드"""
    ensure_initialized()
    return render_template('super/dashboard.html')


@app.route('/super/partner-groups', methods=['GET', 'POST'])
@login_required
@super_admin_required
def super_partner_groups():
    """파트너그룹 관리"""
    ensure_initialized()
    
    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('super_dashboard'))
    
    if request.method == 'POST':
        action = request.form.get('action')
        partner_id = request.form.get('partner_id')
        
        if action == 'create':
            # 신규 파트너그룹 생성
            name = request.form.get('name', '').strip()
            business_number = request.form.get('business_number', '').strip()
            representative = request.form.get('representative', '').strip()
            phone = request.form.get('phone', '').strip()
            mobile = request.form.get('mobile', '').strip()
            address = request.form.get('address', '').strip()
            memo = request.form.get('memo', '').strip()
            
            # 필수 항목 확인
            if not name or not business_number or not representative or not phone:
                flash('필수 항목(파트너그룹명, 사업자등록번호, 대표자, 유선번호)을 모두 입력해주세요.', 'warning')
                return redirect(url_for('super_partner_groups'))
            
            # 사업자등록번호 중복 확인
            existing = db.session.query(PartnerGroup).filter_by(business_number=business_number).first()
            if existing:
                flash('이미 등록된 사업자등록번호입니다.', 'danger')
                return redirect(url_for('super_partner_groups'))
            
            # 파일 업로드 처리
            registration_cert_path = None
            logo_path = None
            
            if 'registration_cert' in request.files:
                file = request.files['registration_cert']
                if file and file.filename:
                    try:
                        filename = f"partner_{business_number}_cert_{datetime.now().strftime('%Y%m%d%H%M%S')}.{file.filename.rsplit('.', 1)[1].lower()}"
                        filepath = os.path.join(UPLOAD_DIR, filename)
                        os.makedirs(UPLOAD_DIR, exist_ok=True)
                        file.save(filepath)
                        registration_cert_path = filename
                    except Exception as e:
                        print(f"Warning: Failed to save registration cert: {e}")
            
            if 'logo' in request.files:
                file = request.files['logo']
                if file and file.filename:
                    try:
                        filename = f"partner_{business_number}_logo_{datetime.now().strftime('%Y%m%d%H%M%S')}.{file.filename.rsplit('.', 1)[1].lower()}"
                        filepath = os.path.join(UPLOAD_DIR, filename)
                        os.makedirs(UPLOAD_DIR, exist_ok=True)
                        file.save(filepath)
                        logo_path = filename
                    except Exception as e:
                        print(f"Warning: Failed to save logo: {e}")
            
            # 파트너그룹 생성
            partner_group = PartnerGroup(
                name=name,
                business_number=business_number,
                representative=representative,
                phone=phone,
                mobile=mobile,
                address=address,
                registration_cert_path=registration_cert_path,
                logo_path=logo_path,
                memo=memo
            )
            db.session.add(partner_group)
            
            if not safe_commit():
                flash('파트너그룹 생성 중 오류가 발생했습니다.', 'danger')
            else:
                # 파트너그룹 생성 시 하위 프로그램 자동 복제 및 초기화
                try:
                    # 1. 파트너그룹별 기본 관리자 계정 생성 (하위 프로그램 관리자)
                    admin_username = f"{name}_admin"
                    admin_password = f"{business_number[-4:]}"  # 사업자번호 마지막 4자리
                    
                    # 기존 관리자 계정이 있는지 확인
                    existing_admin = db.session.query(Member).filter(
                        Member.username == admin_username,
                        Member.partner_group_id == partner_group.id
                    ).first()
                    
                    if not existing_admin:
                        admin_member = Member(
                            username=admin_username,
                            company_name=name,
                            business_number=business_number,
                            representative=representative,
                            phone=phone,
                            approval_status='승인',
                            role='admin',
                            partner_group_id=partner_group.id
                        )
                        admin_member.set_password(admin_password)
                        db.session.add(admin_member)
                        if not safe_commit():
                            print(f"Warning: Failed to create admin account for partner group {name}")
                        else:
                            print(f'파트너그룹 "{name}" 하위 프로그램 생성 완료')
                            print(f'  - 관리자 계정: {admin_username} / {admin_password}')
                            print(f'  - 프로그램 제목: {name}책임보험가입')
                            print(f'  - 로고: {"적용됨" if logo_path else "기본 로고 사용"}')
                            print(f'  - 로그인 URL: /login/{partner_group.id}')
                    else:
                        print(f'파트너그룹 "{name}" 관리자 계정이 이미 존재합니다.')
                    
                    # 2. 하위 프로그램 복제 완료 - 파트너그룹별 독립적인 데이터 구조 생성
                    # (partner_group_id로 자동 분리되므로 추가 작업 불필요)
                    
                except Exception as e:
                    print(f"Warning: Failed to initialize partner group program: {e}")
                    import traceback
                    traceback.print_exc()
                    flash('파트너그룹은 생성되었으나 하위 프로그램 초기화 중 오류가 발생했습니다.', 'warning')
                else:
                    flash(f'파트너그룹 "{name}"이(가) 생성되었고 하위 프로그램이 자동으로 복제되었습니다.', 'success')
                
        elif action == 'update' and partner_id:
            # 파트너그룹 수정
            partner = db.session.get(PartnerGroup, int(partner_id))
            if partner:
                partner.name = request.form.get('name', '').strip() or partner.name
                partner.business_number = request.form.get('business_number', '').strip() or partner.business_number
                partner.representative = request.form.get('representative', '').strip() or partner.representative
                partner.phone = request.form.get('phone', '').strip() or partner.phone
                partner.mobile = request.form.get('mobile', '').strip() or partner.mobile
                partner.address = request.form.get('address', '').strip() or partner.address
                partner.memo = request.form.get('memo', '').strip() or partner.memo
                
                # 파일 업로드 처리
                if 'registration_cert' in request.files:
                    file = request.files['registration_cert']
                    if file and file.filename:
                        try:
                            filename = f"partner_{partner.business_number}_cert_{datetime.now().strftime('%Y%m%d%H%M%S')}.{file.filename.rsplit('.', 1)[1].lower()}"
                            filepath = os.path.join(UPLOAD_DIR, filename)
                            os.makedirs(UPLOAD_DIR, exist_ok=True)
                            file.save(filepath)
                            partner.registration_cert_path = filename
                        except Exception as e:
                            print(f"Warning: Failed to save registration cert: {e}")
                
                if 'logo' in request.files:
                    file = request.files['logo']
                    if file and file.filename:
                        try:
                            filename = f"partner_{partner.business_number}_logo_{datetime.now().strftime('%Y%m%d%H%M%S')}.{file.filename.rsplit('.', 1)[1].lower()}"
                            filepath = os.path.join(UPLOAD_DIR, filename)
                            os.makedirs(UPLOAD_DIR, exist_ok=True)
                            file.save(filepath)
                            partner.logo_path = filename
                        except Exception as e:
                            print(f"Warning: Failed to save logo: {e}")
                
                if not safe_commit():
                    flash('파트너그룹 수정 중 오류가 발생했습니다.', 'danger')
                else:
                    flash('파트너그룹이 수정되었습니다.', 'success')
            else:
                flash('파트너그룹을 찾을 수 없습니다.', 'danger')
                
        elif action == 'delete' and partner_id:
            # 파트너그룹 삭제
            partner = db.session.get(PartnerGroup, int(partner_id))
            if partner:
                db.session.delete(partner)
                if not safe_commit():
                    flash('파트너그룹 삭제 중 오류가 발생했습니다.', 'danger')
                else:
                    flash('파트너그룹이 삭제되었습니다.', 'success')
            else:
                flash('파트너그룹을 찾을 수 없습니다.', 'danger')
        
        return redirect(url_for('super_partner_groups'))
    
    # GET 요청: 파트너그룹 목록 조회
    partners = db.session.query(PartnerGroup).order_by(PartnerGroup.created_at.desc()).all()
    return render_template('super/partner_groups.html', partners=partners)


@app.route('/super/all-insurance', methods=['GET', 'POST'])
@login_required
@super_admin_required
def super_all_insurance():
    """전체책임보험현황 (모든 파트너그룹 데이터 취합)"""
    ensure_initialized()
    
    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('super_dashboard'))
    
    # 검색 조건
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    partner_group_id = request.args.get('partner_group_id')
    company_name = request.args.get('company_name', '').strip()
    enrollment_status = request.args.get('enrollment_status')  # 가입완료, 미가입
    
    # 기본 쿼리 (모든 파트너그룹 데이터)
    query = db.session.query(InsuranceApplication)
    
    # 검색 필터 적용
    if start_date:
        try:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
            query = query.filter(InsuranceApplication.created_at >= datetime.combine(start_dt, datetime.min.time(), tzinfo=KST))
        except Exception:
            pass
    
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
            query = query.filter(InsuranceApplication.created_at < datetime.combine(end_dt, datetime.min.time(), tzinfo=KST) + timedelta(days=1))
        except Exception:
            pass
    
    if partner_group_id:
        query = query.filter(InsuranceApplication.partner_group_id == int(partner_group_id))
    
    if company_name:
        # Member 테이블과 조인하여 상사명으로 필터링
        query = query.join(Member).filter(Member.company_name.like(f'%{company_name}%'))
    
    if enrollment_status == '가입완료':
        query = query.filter(InsuranceApplication.start_at.is_not(None))
    elif enrollment_status == '미가입':
        query = query.filter(InsuranceApplication.start_at.is_(None))
    
    rows = query.order_by(InsuranceApplication.created_at.desc()).all()
    
    # 파트너그룹 목록 (필터용)
    partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
    
    # 엑셀 다운로드
    if request.args.get('download') == 'excel':
        import pandas as pd
        data = []
        for r in rows:
            partner_name = r.partner_group.name if r.partner_group else ''
            company_name_val = r.created_by_member.company_name if r.created_by_member else ''
            data.append({
                '파트너그룹(상호)': partner_name,
                '상사명': company_name_val,
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
                '비고': r.memo or '',
            })
        df = pd.DataFrame(data)
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='전체책임보험현황')
        buffer.seek(0)
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f'all_insurance_{datetime.now().strftime("%Y%m%d")}.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
    
    # 편집 처리
    if request.method == 'POST':
        action = request.form.get('action')
        row_id = request.form.get('row_id')
        
        if action == 'update' and row_id:
            row = db.session.get(InsuranceApplication, int(row_id))
            if row:
                # 편집 가능한 필드들 업데이트
                row.memo = request.form.get('memo', '').strip() or row.memo
                if not safe_commit():
                    flash('수정 중 오류가 발생했습니다.', 'danger')
                else:
                    flash('수정되었습니다.', 'success')
        
        elif action == 'delete' and row_id:
            row = db.session.get(InsuranceApplication, int(row_id))
            if row:
                db.session.delete(row)
                if not safe_commit():
                    flash('삭제 중 오류가 발생했습니다.', 'danger')
                else:
                    flash('삭제되었습니다.', 'success')
        
        return redirect(url_for('super_all_insurance', 
                               start_date=start_date, 
                               end_date=end_date,
                               partner_group_id=partner_group_id,
                               company_name=company_name,
                               enrollment_status=enrollment_status))
    
    return render_template('super/all_insurance.html', 
                         rows=rows, 
                         partner_groups=partner_groups,
                         start_date=start_date,
                         end_date=end_date,
                         partner_group_id=partner_group_id,
                         company_name=company_name,
                         enrollment_status=enrollment_status)


@app.route('/super/settlement', methods=['GET', 'POST'])
@login_required
@super_admin_required
def super_settlement():
    """정산페이지 (파트너그룹별 정산)"""
    ensure_initialized()
    
    if db is None:
        flash('데이터베이스가 초기화되지 않았습니다.', 'danger')
        return redirect(url_for('super_dashboard'))
    
    year = int(request.args.get('year', datetime.now().year))
    month = int(request.args.get('month', datetime.now().month))
    partner_group_id = request.args.get('partner_group_id')
    
    # 기준: 해당 년/월 데이터 (시작일 기준)
    start_period = datetime(year, month, 1, tzinfo=KST)
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=KST)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=KST)
    
    # 쿼리 생성
    query = db.session.query(InsuranceApplication).filter(
        InsuranceApplication.start_at.is_not(None),
        InsuranceApplication.start_at >= start_period,
        InsuranceApplication.start_at < next_month,
    )
    
    # 파트너그룹 필터
    if partner_group_id:
        query = query.filter(InsuranceApplication.partner_group_id == int(partner_group_id))
    
    rows = query.all()
    
    # 그룹핑: 파트너그룹별, 상사별 건수/금액
    by_partner_company = {}
    for r in rows:
        partner_name = r.partner_group.name if r.partner_group else '미지정'
        company = r.created_by_member.company_name if r.created_by_member else '미상'
        rep = r.created_by_member.representative if r.created_by_member else ''
        biz = r.created_by_member.business_number if r.created_by_member else ''
        key = (partner_name, company, rep, biz)
        by_partner_company.setdefault(key, 0)
        by_partner_company[key] += 1
    
    settlements = []
    by_partner = {}  # 파트너그룹별 합계
    
    for (partner_name, company, rep, biz), count in by_partner_company.items():
        amount = count * 9500
        settlements.append({
            'partner_name': partner_name,
            'company': company,
            'representative': rep,
            'business_number': biz,
            'count': count,
            'amount': amount,
        })
        
        # 파트너그룹별 합계 계산
        by_partner.setdefault(partner_name, {'count': 0, 'amount': 0})
        by_partner[partner_name]['count'] += count
        by_partner[partner_name]['amount'] += amount
    
    # 전체 합계
    total_count = sum(s['count'] for s in settlements)
    total_amount = sum(s['amount'] for s in settlements)
    
    # 파트너그룹 목록
    partner_groups = db.session.query(PartnerGroup).order_by(PartnerGroup.name).all()
    
    # 엑셀 다운로드
    if request.args.get('download') == 'excel':
        import pandas as pd
        data = []
        current_partner = None
        for s in settlements:
            if current_partner != s['partner_name']:
                if current_partner is not None:
                    # 이전 파트너그룹 합계 추가
                    partner_total = by_partner[current_partner]
                    data.append({
                        '파트너그룹(상호)': current_partner,
                        '상사명': '합계',
                        '대표자': '',
                        '건수': partner_total['count'],
                        '금액': partner_total['amount'],
                        '비고': '',
                        '작업': '',
                        '청구서': '',
                    })
                current_partner = s['partner_name']
            
            data.append({
                '파트너그룹(상호)': s['partner_name'],
                '상사명': s['company'],
                '대표자': s['representative'],
                '건수': s['count'],
                '금액': s['amount'],
                '비고': '',
                '작업': '',
                '청구서': '',
            })
        
        # 마지막 파트너그룹 합계
        if current_partner:
            partner_total = by_partner[current_partner]
            data.append({
                '파트너그룹(상호)': current_partner,
                '상사명': '합계',
                '대표자': '',
                '건수': partner_total['count'],
                '금액': partner_total['amount'],
                '비고': '',
                '작업': '',
                '청구서': '',
            })
        
        # 전체 합계
        data.append({
            '파트너그룹(상호)': '',
            '상사명': '전체 합계',
            '대표자': '',
            '건수': total_count,
            '금액': total_amount,
            '비고': '',
            '작업': '',
            '청구서': '',
        })
        
        df = pd.DataFrame(data)
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='정산')
        buffer.seek(0)
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f'settlement_{year}{month:02d}.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
    
    return render_template('super/settlement.html', 
                         year=year, 
                         month=month,
                         partner_group_id=partner_group_id,
                         partner_groups=partner_groups,
                         settlements=settlements,
                         by_partner=by_partner,
                         total_count=total_count, 
                         total_amount=total_amount)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)


