"""서비스가 DB에 보내는 질의(execute) 수를 세는 시험 도구. 제품 코드에는 쓰지 않는다."""

from contextlib import contextmanager


class _CountingConn:
    def __init__(self, conn, counter):
        self._conn = conn
        self._counter = counter

    def execute(self, *args, **kwargs):
        self._counter.count += 1
        return self._conn.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class _Counter:
    count = 0


@contextmanager
def count_queries(svc):
    """with count_queries(svc) as c: ... 안에서 svc가 보낸 execute 수가 c.count에 쌓인다."""
    counter = _Counter()
    pool = svc.pool
    original = pool.connection

    @contextmanager
    def counted(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            yield _CountingConn(conn, counter)

    pool.connection = counted
    try:
        yield counter
    finally:
        del pool.connection  # 인스턴스에 덮어쓴 것을 지워 원래 메서드로 되돌린다
