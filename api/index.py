from flask import Flask, jsonify
import sys

app = Flask(__name__)

@app.route('/')
@app.route('/api')
def home():
    return jsonify({
        "status": "success",
        "message": "Quizera API is running!",
        "python_version": sys.version
    })

@app.route('/api/test')
def test():
    return jsonify({"test": "passed"})

# Error handler
@app.errorhandler(Exception)
def handle_error(e):
    return jsonify({
        "error": str(e),
        "type": type(e).__name__
    }), 500