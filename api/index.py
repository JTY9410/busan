# Vercel Python runtime expects 'application'
import sys
import traceback

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
    print("✓ Successfully imported Flask app")
except ImportError as e:
    error_msg = f"ImportError: {str(e)}\n\n{traceback.format_exc()}"
    print(f"✗ Import failed: {error_msg}")
    application = create_error_app(error_msg)
except Exception as e:
    error_msg = f"Unexpected error: {str(e)}\n\n{traceback.format_exc()}"
    print(f"✗ Unexpected error: {error_msg}")
    application = create_error_app(error_msg)
