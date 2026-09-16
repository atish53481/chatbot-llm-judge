"""
Flask backend for DeepEval test execution
Runs pytest and streams results as JSON
"""
from flask import Flask, request, jsonify
from flask_cors import CORS
import subprocess
import os
import re

app = Flask(__name__)
CORS(app)

@app.route('/run-tests', methods=['POST'])
def run_tests():
    data = request.json
    api_key = data.get('apiKey')
    test_file = data.get('testFile', 'all')

    if not api_key:
        return jsonify({'error': 'API key required'}), 400

    # Set env var
    env = os.environ.copy()
    env['OPENAI_API_KEY'] = api_key

    # Build pytest command
    if test_file == 'all':
        cmd = ['python', '-m', 'pytest', 'evals/', '-v', '--tb=short']
    else:
        cmd = ['python', '-m', 'pytest', f'evals/{test_file}', '-v', '--tb=short']

    try:
        result = subprocess.run(
            cmd,
            cwd=r'C:\Users\atish\Documents\LLMJudge\ChatbotLLM Judge',
            env=env,
            capture_output=True,
            text=True,
            timeout=300
        )

        # Parse pytest output
        tests = parse_pytest_output(result.stdout, result.stderr)

        return jsonify({
            'success': result.returncode == 0,
            'total': len(tests),
            'passed': sum(1 for t in tests if t['status'] == 'pass'),
            'failed': sum(1 for t in tests if t['status'] == 'fail'),
            'duration': extract_duration(result.stdout),
            'tests': tests,
            'raw_output': result.stdout + '\n' + result.stderr
        })

    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Tests timed out after 5 minutes'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def parse_pytest_output(stdout, stderr):
    """Parse pytest verbose output into structured results"""
    tests = []
    lines = stdout.split('\n')

    for line in lines:
        # Match test result lines
        if 'PASSED' in line or 'FAILED' in line:
            match = re.search(r'(test_\w+)', line)
            if match:
                test_name = match.group(1)
                status = 'pass' if 'PASSED' in line else 'fail'

                tests.append({
                    'name': test_name,
                    'input': 'Check raw output for details',
                    'actual_output': 'See full test output below',
                    'status': status,
                    'metrics': []
                })

    return tests

def extract_duration(output):
    """Extract test duration from pytest output"""
    match = re.search(r'in ([\d.]+)s', output)
    return float(match.group(1)) if match else 0.0

if __name__ == '__main__':
    print("Starting DeepEval backend server...")
    print("Install dependencies first: pip install flask flask-cors")
    app.run(host='127.0.0.1', port=5000, debug=True)
