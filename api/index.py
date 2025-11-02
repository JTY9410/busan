# Vercel Python runtime expects 'application'
try:
    from app import app as application
except Exception as e:
    # Create a minimal error handler app if import fails
    from flask import Flask
    error_app = Flask(__name__)
    
    @error_app.route('/', defaults={'path': ''})
    @error_app.route('/<path:path>')
    def error_handler(path):
        return f"""
        <h1>Application Error</h1>
        <p>The application failed to initialize.</p>
        <p>Error: {str(e)}</p>
        <p>Please check the logs for more details.</p>
        """, 500
    
    application = error_app

