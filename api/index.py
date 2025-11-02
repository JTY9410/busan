# Vercel Python runtime expects 'application' or 'handler'
import sys
import os
import traceback

# Ensure Vercel environment variables are set BEFORE any other imports
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
        # Escape HTML in error message for safety
        escaped_msg = error_msg.replace('<', '&lt;').replace('>', '&gt;').replace('&', '&amp;')
        return f"""
        <html>
        <head><title>Application Error</title></head>
        <body style="font-family: Arial; padding: 20px;">
            <h1>Application Initialization Error</h1>
            <p>The application failed to initialize on Vercel.</p>
            <h2>Error Details:</h2>
            <pre style="background: #f5f5f5; padding: 15px; border-radius: 5px; overflow-x: auto; white-space: pre-wrap;">{escaped_msg}</pre>
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
application = None
import_error = None

try:
    # Import with detailed error handling
    import io
    
    # Capture import errors
    old_stderr = sys.stderr
    stderr_capture = io.StringIO()
    sys.stderr = stderr_capture
    
    try:
        from app import app as application
        import_error = None
    except Exception as import_err:
        import_error = import_err
        application = None
    finally:
        sys.stderr = old_stderr
        stderr_output = stderr_capture.getvalue()
        if stderr_output:
            print(f"Import stderr: {stderr_output}", file=sys.stderr)
    
    if import_error:
        raise import_error
    
    if application is None:
        raise RuntimeError("Application is None after import")
    
    if not callable(application):
        raise RuntimeError(f"Application is not callable (type: {type(application)})")
    
    print("✓ Successfully imported Flask app", file=sys.stderr)
    print(f"✓ Application type: {type(application)}", file=sys.stderr)
    print(f"✓ Application callable: {callable(application)}", file=sys.stderr)
        
except ImportError as e:
    error_msg = f"ImportError: {str(e)}\n\n{traceback.format_exc()}"
    print(f"✗ Import failed: {error_msg}", file=sys.stderr)
    sys.stderr.write(f"VERCEL_ERROR: {error_msg}\n")
    application = create_error_app(error_msg)
except Exception as e:
    error_msg = f"Unexpected error: {str(e)}\n\n{traceback.format_exc()}"
    print(f"✗ Unexpected error: {error_msg}", file=sys.stderr)
    sys.stderr.write(f"VERCEL_ERROR: {error_msg}\n")
    application = create_error_app(error_msg)

# Ensure application is defined
if application is None:
    application = create_error_app("Application object not created")

# Verify the app is WSGI-compatible before exporting
if not callable(application):
    error_msg = f"Application is not callable: {type(application)}"
    print(f"ERROR: {error_msg}", file=sys.stderr)
    application = create_error_app(error_msg)

# Vercel Python runtime expects a WSGI application exported as 'app'.
# Flask apps are WSGI-compatible callables that accept (environ, start_response).
#
# Vercel's runtime has a bug where it tries to inspect class hierarchies
# and checks issubclass() on values that might not be classes, causing:
# TypeError: issubclass() arg 1 must be a class
#
# We export the Flask app directly - it's a proper WSGI application.
# If Vercel's handler code has issues, it's a bug on their side, but we
# ensure our export is clean and correct.

# Export as 'app' - this is what Vercel's Python runtime looks for
app = application

# The Flask app object is already a WSGI callable, so we can export it directly
# No wrapper needed - Flask apps implement the WSGI interface natively
