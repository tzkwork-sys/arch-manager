"""Own application executors until they have stopped, before Qt is destroyed."""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

_pools: set[ThreadPoolExecutor] = set()
_lock = Lock()


def create_background_executor(**kwargs) -> ThreadPoolExecutor:
    pool = ThreadPoolExecutor(**kwargs)
    with _lock:
        _pools.add(pool)
    return pool


def shutdown_background_executors() -> None:
    with _lock:
        pools = tuple(_pools)
        _pools.clear()
    for pool in pools:
        # Do not interrupt or cancel package operations already submitted.
        pool.shutdown(wait=True, cancel_futures=False)
