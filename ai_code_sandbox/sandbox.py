import docker
import shlex
import uuid
import textwrap
import os
import tarfile
import io
from io import BytesIO
import time
import sys

class AICodeSandbox:
    """
    A sandbox environment for executing Python code safely.

    This class creates a Docker container with a Python environment,
    optionally installs additional packages, and provides methods to
    execute code, read and write files within the sandbox.

    Attributes:
        client (docker.DockerClient): Docker client for managing containers and images.
        container (docker.models.containers.Container): The Docker container used as a sandbox.
        temp_image (docker.models.images.Image): Temporary Docker image created for the sandbox.
    """

    def __init__(self, custom_image=None, packages=None, network_mode="bridge", mem_limit="512m", cpu_period=100000, cpu_quota=50000):
        """
        Initialize the PythonSandbox.

        Args:
            custom_image (str, optional): Name of a custom Docker image to use. Defaults to None.
            packages (list, optional): List of Python packages to install in the sandbox. Defaults to None.
            network_mode (str, optional): Network mode to use for the sandbox. Defaults to "bridge".
            mem_limit (str, optional): Memory limit for the sandbox. Defaults to "512m".
            cpu_period (int, optional): CPU period for the sandbox. Defaults to 100000.
            cpu_quota (int, optional): CPU quota for the sandbox. Defaults to 50000.
        """
        self.client = docker.from_env()
        self.container = None
        self.temp_image = None
        self.pool = None
        self._setup_sandbox(custom_image, packages, network_mode, mem_limit, cpu_period, cpu_quota)

    def _setup_sandbox(self, custom_image, packages, network_mode, mem_limit, cpu_period, cpu_quota):
        """Set up the sandbox environment."""
        # Default packages for persistent container
        default_packages = [
            "requests", "numpy", "pandas", "matplotlib", "scipy", 
            "beautifulsoup4", "fastapi", "pillow", "opencv-python", "regex"
        ]
        
        # If using default setup (no custom image, no packages specified), try persistent container
        if custom_image is None and packages is None:
            try:
                self.container = self.client.containers.get("sandbox_persistent")
                # Check if container is running, start it if not
                self.container.reload()
                if self.container.status != 'running':
                    print(f"[SANDBOX] Container sandbox_persistent is {self.container.status}, starting it...", file=sys.stderr, flush=True)
                    self.container.start()
                    # Wait for container to be fully running
                    for _ in range(10):
                        self.container.reload()
                        if self.container.status == 'running':
                            break
                        time.sleep(0.5)
                    if self.container.status != 'running':
                        raise Exception(f"Failed to start container, status: {self.container.status}")
                return  # Reuse existing persistent container
            except docker.errors.NotFound:
                pass  # Container doesn't exist, will create new one below
            except Exception as e:
                print(f"[SANDBOX] Error with persistent container: {e}, recreating...", file=sys.stderr, flush=True)
                # Try to remove broken container
                try:
                    old = self.client.containers.get("sandbox_persistent")
                    old.remove(force=True)
                except:
                    pass
            
            # First time setup: create persistent container with default packages
            packages = default_packages
        
        image_name = custom_image or "python:3.9-slim"
        
        # Build custom image if packages are provided
        if packages:
            dockerfile = f"FROM {image_name}\nRUN pip install {' '.join(packages)}"
            dockerfile_obj = BytesIO(dockerfile.encode('utf-8'))
            self.temp_image = self.client.images.build(fileobj=dockerfile_obj, rm=True)[0]
            image_name = self.temp_image.id

        # Create new container
        is_default_setup = custom_image is None and packages is not None and set(packages) == set(default_packages)
        container_name = "sandbox_persistent" if is_default_setup else f"python_sandbox_{uuid.uuid4().hex[:8]}"
        self.container = self.client.containers.run(
            image_name,
            name=container_name,
            command="tail -f /dev/null",
            detach=True,
            network_mode=network_mode,
            dns=["8.8.8.8", "8.8.4.4"],  # Google DNS for internet access
            mem_limit=mem_limit,
            cpu_period=cpu_period,
            cpu_quota=cpu_quota
        )


    def write_file(self, filename, content):
        """
        Write content to a file in the sandbox, creating directories if they don't exist.

        Args:
            filename (str): Name of the file to create or overwrite.
            content (str or bytes): Content to write to the file.

        Raises:
            Exception: If writing to the file fails.
        """
        directory = os.path.dirname(filename)
        if directory:
            mkdir_command = f'mkdir -p {shlex.quote(directory)}'
            mkdir_result = self.container.exec_run(["sh", "-c", mkdir_command])
            if mkdir_result.exit_code != 0:
                raise Exception(f"Failed to create directory: {mkdir_result.output.decode('utf-8')}")

        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode='w') as tar:
            if isinstance(content, str):
                file_data = content.encode('utf-8')
            else:
                file_data = content
            tarinfo = tarfile.TarInfo(name=filename)
            tarinfo.size = len(file_data)
            tar.addfile(tarinfo, io.BytesIO(file_data))

        tar_stream.seek(0)

        try:
            self.container.put_archive('/', tar_stream)
        except Exception as e:
            raise Exception(f"Failed to write file: {str(e)}")

        check_command = f'test -f {shlex.quote(filename)}'
        check_result = self.container.exec_run(["sh", "-c", check_command])
        if check_result.exit_code != 0:
            raise Exception(f"Failed to write file: {filename}")

    def read_file(self, filename):
        """
        Read content from a file in the sandbox.

        Args:
            filename (str): Name of the file to read.

        Returns:
            str: Content of the file.

        Raises:
            Exception: If reading the file fails.
        """
        result = self.container.exec_run(["cat", filename])
        if result.exit_code != 0:
            raise Exception(f"Failed to read file: {result.output.decode('utf-8')}")
        return result.output.decode('utf-8')

    def run_code(self, code, env_vars=None):
        """
        Execute Python code in the sandbox.

        Args:
            code (str): Python code to execute.
            env_vars (dict, optional): Environment variables to set for the execution. Defaults to None.

        Returns:
            str: Output of the executed code or error message.
        """
        if env_vars is None:
            env_vars = {}
        
        code = textwrap.dedent(code)

        code_preview = code.strip()
        if len(code_preview) > 200:
            code_preview = code_preview[:197] + '...'
        print(
            f"[SANDBOX] Executing code (len={len(code)}): {code_preview}",
            file=sys.stderr,
            flush=True,
        )

        env_str = " ".join(f"{k}={shlex.quote(v)}" for k, v in env_vars.items())
        escaped_code = code.replace("'", "'\"'\"'")
        exec_command = f"env {env_str} python -c '{escaped_code}'"
        
        t_exec_start = time.time()
        exec_result = self.container.exec_run(
            ["sh", "-c", exec_command],
            demux=True
        )
        t_exec_end = time.time()
        print(
            f"[SANDBOX] container.exec_run() took {(t_exec_end - t_exec_start)*1000:.2f}ms",
            file=sys.stderr,
            flush=True,
        )
        
        stdout, stderr = exec_result.output

        stdout_text = stdout.decode('utf-8') if stdout else ''
        stderr_text = stderr.decode('utf-8') if stderr else ''

        print(
            f"[SANDBOX] exit_code={exec_result.exit_code} stdout_len={len(stdout_text)} stderr_len={len(stderr_text)}",
            file=sys.stderr,
            flush=True,
        )

        if exec_result.exit_code != 0:
            error_preview = stderr_text.strip() or stdout_text.strip()
            if len(error_preview) > 200:
                error_preview = error_preview[:197] + '...'
            print(
                f"[SANDBOX] execution failed: {error_preview}",
                file=sys.stderr,
                flush=True,
            )
            return f"Error (exit code {exec_result.exit_code}): {stderr_text}"

        if stdout_text:
            preview = stdout_text.strip()
            if len(preview) > 200:
                preview = preview[:197] + '...'
            print(
                f"[SANDBOX] stdout preview: {preview}",
                file=sys.stderr,
                flush=True,
            )

        if stderr_text:
            preview = stderr_text.strip()
            if len(preview) > 200:
                preview = preview[:197] + '...'
            print(
                f"[SANDBOX] stderr preview: {preview}",
                file=sys.stderr,
                flush=True,
            )

        if stdout_text:
            return stdout_text
        if stderr_text:
            return f"Error: {stderr_text}"
        return "No output"

    def close(self):
        """
        Remove all resources created by this sandbox.

        This method should be called when the sandbox is no longer needed to clean up Docker resources.
        """
        if self.container:
            # Never stop or remove the persistent container
            if self.container.name != "sandbox_persistent":
                try:
                    self.container.stop(timeout=10)
                    self.container.remove(force=True)
                except Exception as e:
                    print(f"Error stopping/removing container: {str(e)}")
            self.container = None

        if self.temp_image:
            try:
                for _ in range(3):
                    try:
                        self.client.images.remove(self.temp_image.id, force=True)
                        break
                    except Exception as e:
                        print(f"Attempt to remove image failed: {str(e)}")
                        time.sleep(2)
                else:
                    print("Failed to remove temporary image after multiple attempts")
            except Exception as e:
                print(f"Error removing temporary image: {str(e)}")
            finally:
                self.temp_image = None

    def __del__(self):
        """Ensure resources are cleaned up when the object is garbage collected."""
        self.close()