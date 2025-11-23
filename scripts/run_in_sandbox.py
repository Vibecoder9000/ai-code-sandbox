#!/usr/bin/env python3
"""
Run Python code inside AICodeSandbox and return a JSON result on stdout.

Input: JSON on stdin with keys:
  - code: string (required)
  - packages: list of package names to install (optional)
  - timeout: seconds (optional, not enforced here — caller should enforce)

Output: JSON to stdout with keys:
  - success: bool
  - result: string (sandbox.run_code return or captured stdout)
  - error: string (error message if any)
  - timings: dict with ms timing for each stage

Note: This script expects the ai_code_sandbox package to be importable from the
current working directory. Run it from the project root so Python can find
the local `ai-code-sandbox` package included in this repository.
"""
import sys
import json
import traceback
import time

t_start = time.perf_counter()

def log_time(label, start_time):
    """Log a timing checkpoint to stderr."""
    elapsed = (time.perf_counter() - start_time) * 1000
    sys.stderr.write(f"[SANDBOX_TIMING] {label}: {elapsed:.2f}ms\n")
    sys.stderr.flush()
    return elapsed

def main():
    timings = {}
    t_main_start = time.perf_counter()
    
    try:
        t_read_start = time.perf_counter()
        raw = sys.stdin.read()
        timings['stdin_read_ms'] = log_time('stdin_read', t_read_start)
        
        if not raw:
            print(json.dumps({"success": False, "error": "no input"}))
            return

        t_parse_start = time.perf_counter()
        data = json.loads(raw)
        timings['json_parse_ms'] = log_time('json_parse', t_parse_start)
        
        code = data.get('code')
        packages = data.get('packages') or None

        if not isinstance(code, str) or not code.strip():
            print(json.dumps({"success": False, "error": "missing or empty 'code'"}))
            return

        # Import here so failures surface as JSON
        t_import_start = time.perf_counter()
        try:
            from ai_code_sandbox import AICodeSandbox
            timings['import_ms'] = log_time('import', t_import_start)
        except Exception as e:
            print(json.dumps({"success": False, "error": f"failed to import ai_code_sandbox: {e}"}))
            return

        sandbox = None
        try:
            t_sandbox_init = time.perf_counter()
            sandbox = AICodeSandbox(packages=packages)
            timings['sandbox_init_ms'] = log_time('sandbox_init', t_sandbox_init)
            
            # run_code may return output or raise; capture return
            t_code_exec = time.perf_counter()
            result = sandbox.run_code(code)
            timings['code_exec_ms'] = log_time('code_exec', t_code_exec)
            
            # Normalize result to string for JSON transport
            t_serialize_start = time.perf_counter()
            try:
                out = result if result is not None else ''
                if not isinstance(out, str):
                    out = str(out)
            except Exception:
                out = repr(result)
            timings['serialize_ms'] = log_time('serialize', t_serialize_start)

            timings['total_ms'] = log_time('TOTAL', t_main_start)
            
            response = {"success": True, "result": out, "timings": timings}
            print(json.dumps(response))
        except Exception as e:
            tb = traceback.format_exc()
            timings['error'] = str(e)
            timings['total_ms'] = log_time('TOTAL_ERROR', t_main_start)
            print(json.dumps({"success": False, "error": str(e), "traceback": tb, "timings": timings}))
        finally:
            try:
                if sandbox is not None:
                    t_close = time.perf_counter()
                    sandbox.close()
                    timings['sandbox_close_ms'] = log_time('sandbox_close', t_close)
            except Exception:
                pass

    except Exception as e:
        tb = traceback.format_exc()
        timings['error'] = str(e)
        timings['total_ms'] = log_time('TOTAL_ERROR', t_main_start)
        print(json.dumps({"success": False, "error": str(e), "traceback": tb, "timings": timings}))

if __name__ == '__main__':
    main()
