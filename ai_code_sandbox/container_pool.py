import docker
import uuid
import time
import sys

class ContainerPool:
    """Simple pool of pre-created Docker containers for code execution."""
    
    def __init__(self, pool_size=2, image_name="python:3.9-slim"):
        self.pool_size = pool_size
        self.image_name = image_name
        self.client = docker.from_env()
        self.containers = []
        self.in_use = set()
        self._init_pool()
    
    def _init_pool(self):
        """Create warm containers once."""
        print(f"[ContainerPool] Creating {self.pool_size} warm containers...", file=sys.stderr, flush=True)
        for i in range(self.pool_size):
            try:
                c = self.client.containers.run(
                    self.image_name,
                    name=f"sandbox_pool_{uuid.uuid4().hex[:8]}",
                    command="tail -f /dev/null",
                    detach=True,
                    network_mode="none",
                    mem_limit="100m",
                    cpu_period=100000,
                    cpu_quota=50000,
                    remove=True
                )
                self.containers.append(c)
                print(f"[ContainerPool] Container {i+1}/{self.pool_size} ready", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"[ContainerPool] Failed to create container: {e}", file=sys.stderr, flush=True)
    
    def acquire(self, timeout=30):
        """Get an available container from the pool."""
        start = time.time()
        while time.time() - start < timeout:
            for c in self.containers:
                cid = c.id
                if cid not in self.in_use:
                    self.in_use.add(cid)
                    return c
            time.sleep(0.1)
        raise RuntimeError("No containers available in pool")
    
    def release(self, container):
        """Return a container to the pool."""
        self.in_use.discard(container.id)
    
    def cleanup(self):
        """Stop all containers."""
        for c in self.containers:
            try:
                c.stop()
                c.remove()
            except:
                pass

# Global pool instance
_pool = None

def get_pool(pool_size=2):
    global _pool
    if _pool is None:
        _pool = ContainerPool(pool_size)
    return _pool
