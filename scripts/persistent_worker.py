#!/usr/bin/env python3
"""
Persistent sandbox worker that stays alive and processes multiple requests.
Reads JSON requests from stdin, executes them, outputs JSON results.
Much faster than spawning a new Python process for each request.
Supports both Python code execution and bash shell commands.
"""
import sys
import json
import traceback
import time
import subprocess

def execute_bash(code):
    """Execute bash code and return stdout/stderr."""
    try:
        result = subprocess.run(
            code,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            preexec_fn=None  # Windows compatibility
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired as e:
        # Ensure process is killed on timeout
        if e.stdout:
            stdout = e.stdout if isinstance(e.stdout, str) else e.stdout.decode('utf-8', errors='ignore')
        else:
            stdout = ""
        return stdout, "Bash execution timed out (30s)", 124
    except Exception as e:
        return "", str(e), 1

def process_request(request_data):
    """Process a single sandbox execution request."""
    timings = {}
    t_start = time.perf_counter()
    
    try:
        code = request_data.get('code')
        language = request_data.get('language', 'python')  # Default to python
        packages = request_data.get('packages') or None
        request_id = request_data.get('id', 'unknown')
        
        if not isinstance(code, str) or not code.strip():
            return {"id": request_id, "success": False, "error": "missing or empty code", "timings": timings}

        # Handle bash execution
        if language == 'bash':
            t_exec = time.perf_counter()
            stdout, stderr, returncode = execute_bash(code)
            timings['code_exec_ms'] = (time.perf_counter() - t_exec) * 1000
            timings['total_ms'] = (time.perf_counter() - t_start) * 1000
            
            if returncode != 0:
                print(
                    f"[WORKER] request={request_id} bash_exit_code={returncode} stderr_len={len(stderr)}",
                    file=sys.stderr,
                    flush=True,
                )
                return {
                    "id": request_id,
                    "success": False,
                    "error": stderr or f"Bash exited with code {returncode}",
                    "stdout": stdout,
                    "stderr": stderr,
                    "timings": timings
                }
            else:
                print(
                    f"[WORKER] request={request_id} bash_success=True total_ms={timings['total_ms']:.2f} stdout_len={len(stdout)}",
                    file=sys.stderr,
                    flush=True,
                )
                return {"id": request_id, "success": True, "result": stdout, "timings": timings}
        
        # Handle Python execution (original behavior)
        # Import on first request, then cached
        if 'AICodeSandbox' not in dir():
            t_import = time.perf_counter()
            try:
                from ai_code_sandbox import AICodeSandbox
            except Exception as e:
                return {"id": request_id, "success": False, "error": f"failed to import: {e}", "timings": timings}
            timings['import_ms'] = (time.perf_counter() - t_import) * 1000
        else:
            timings['import_ms'] = 0  # Already imported
        
        sandbox = None
        try:
            t_sandbox = time.perf_counter()
            from ai_code_sandbox import AICodeSandbox
            sandbox = AICodeSandbox(packages=packages)
            timings['sandbox_init_ms'] = (time.perf_counter() - t_sandbox) * 1000
            
            t_exec = time.perf_counter()
            result = sandbox.run_code(code)
            timings['code_exec_ms'] = (time.perf_counter() - t_exec) * 1000
            
            # Normalize result
            out = result if result is not None else ''
            if not isinstance(out, str):
                out = str(out)
            
            timings['total_ms'] = (time.perf_counter() - t_start) * 1000
            print(
                f"[WORKER] request={request_id} success=True total_ms={timings['total_ms']:.2f} stdout_len={len(out)}",
                file=sys.stderr,
                flush=True,
            )
            return {"id": request_id, "success": True, "result": out, "timings": timings}
            
        except Exception as e:
            tb = traceback.format_exc()
            timings['total_ms'] = (time.perf_counter() - t_start) * 1000
            print(
                f"[WORKER] request={request_id} raised {e}" ,
                file=sys.stderr,
                flush=True,
            )
            return {"id": request_id, "success": False, "error": str(e), "traceback": tb, "timings": timings}
        finally:
            try:
                if sandbox:
                    sandbox.close()
            except:
                pass
    
    except Exception as e:
        timings['total_ms'] = (time.perf_counter() - t_start) * 1000
        print(
            f"[WORKER] request={request_id} failed before execution: {e}",
            file=sys.stderr,
            flush=True,
        )
        return {"id": request_id, "success": False, "error": str(e), "timings": timings}

def main():
    """Main loop: read requests, process, output responses."""
    print("[WORKER] Persistent sandbox worker started", file=sys.stderr, flush=True)
    
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            
            t_recv = time.perf_counter()
            try:
                request = json.loads(line)
                t_parse = time.perf_counter()
                
                response = process_request(request)
                t_process = time.perf_counter()
                
                json_str = json.dumps(response)
                t_serialize = time.perf_counter()
                
                print(json_str, flush=True)
                sys.stdout.flush()
                t_output = time.perf_counter()
                
                # Log timing breakdown
                print(f"[WORKER_TIMING] recv->parse={((t_parse-t_recv)*1000):.2f}ms process={((t_process-t_parse)*1000):.2f}ms serialize={((t_serialize-t_process)*1000):.2f}ms output={((t_output-t_serialize)*1000):.2f}ms", file=sys.stderr, flush=True)
                
            except json.JSONDecodeError as e:
                print(json.dumps({"success": False, "error": f"invalid json: {e}"}), flush=True)
                sys.stdout.flush()
            except Exception as e:
                print(json.dumps({"success": False, "error": str(e)}), flush=True)
                sys.stdout.flush()
    except KeyboardInterrupt:
        print("[WORKER] Shutting down", file=sys.stderr, flush=True)
        sys.exit(0)

if __name__ == '__main__':
    main()
