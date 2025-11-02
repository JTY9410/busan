# Vercel Python runtime expects 'application'
import sys
import os
import traceback

# Ensure Vercel environment variables are set
os.environ.setdefault('VERCEL', '1')
os.environ.setdefault('VERCEL_ENV', os.environ.get('VERCEL_ENV', 'production'))
os.environ.setdefault('PYTHONUNBUFFERED', '1')
os.environ.setdefault('INSTANCE_PATH', '/tmp/instance')
os.environ.setdefault('DATA_DIR', '/tmp/data')
os.environ.setdefault('UPLOAD_DIR', '/tmp/uploads')

# Add better error logging for Vercel
def create_error_app(error_msg):
    """Create a minimal Flask app to show errors"""
    from flask import Flask
    error_app = Flask(__name__)
    
    @error_app.route('/', defaults={'path': ''})
    @error_app.route('/<path:path>')
    def error_handler(path):
        return f"""
        <html>
        <head><title>Application Error</title></head>
        <body style="font-family: Arial; padding: 20px;">
            <h1>Application Initialization Error</h1>
            <p>The application failed to initialize on Vercel.</p>
            <h2>Error Details:</h2>
            <pre style="background: #f5f5f5; padding: 15px; border-radius: 5px; overflow-x: auto;">{error_msg}</pre>
            <h2>Please check:</h2>
            <ul>
                <li>Vercel Function Logs for detailed error messages</li>
                <li>All dependencies are listed in requirements.txt</li>
                <li>Python version compatibility (3.11)</li>
            </ul>
        </body>
        </html>
        """, 500
    
    return error_app

# Try to import the app with detailed error reporting
try:
    from app import app as application
    
    if application is None:
        raise RuntimeError("Application is None")
    
    if not callable(application):
        raise RuntimeError("Application is not callable")
    
    print("✓ Successfully imported Flask app")
    print(f"✓ Application type: {type(application)}")
    print(f"✓ Application callable: {callable(application)}")
        
except ImportError as e:
    error_msg = f"ImportError: {str(e)}\n\n{traceback.format_exc()}"
    print(f"✗ Import failed: {error_msg}")
    sys.stderr.write(f"VERCEL_ERROR: {error_msg}\n")
    application = create_error_app(error_msg)
except Exception as e:
    error_msg = f"Unexpected error: {str(e)}\n\n{traceback.format_exc()}"
    print(f"✗ Unexpected error: {error_msg}")
    sys.stderr.write(f"VERCEL_ERROR: {error_msg}\n")
    application = create_error_app(error_msg)

# Ensure application is defined
if 'application' not in globals():
    application = create_error_app("Application object not created")
