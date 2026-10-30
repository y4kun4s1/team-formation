"""PostgreSQL 앞에 두는 시험용 TCP 중계기. 오가는 조각마다 delay_s초씩 늦춰 느린 DB를 흉내 낸다.

제품 코드에는 쓰지 않는다. 시험에서 DB 왕복이 100~200ms인 환경을 만들려는 도구다.
"""

import socket
import threading
import time


def start_delay_proxy(target_host: str, target_port: int, delay_s: float, listen_port: int = 0):
    """(listen_port, stop)을 돌려준다. stop()을 부르면 새 연결도, 이미 열린 연결도 모두 닫힌다.
    listen_port를 정해 주면 stop() 뒤에 같은 포트로 다시 열 수 있다."""
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", listen_port))
    server.listen(32)
    port = server.getsockname()[1]
    sockets: list[socket.socket] = []
    stopped = threading.Event()

    def pump(src: socket.socket, dst: socket.socket) -> None:
        try:
            while not stopped.is_set():
                data = src.recv(65536)
                if not data:
                    break
                time.sleep(delay_s)
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                s.close()

    def accept_loop() -> None:
        while not stopped.is_set():
            try:
                client, _ = server.accept()
            except OSError:
                return
            try:
                upstream = socket.create_connection((target_host, target_port))
            except OSError:
                client.close()
                continue
            sockets.extend([client, upstream])
            threading.Thread(target=pump, args=(client, upstream), daemon=True).start()
            threading.Thread(target=pump, args=(upstream, client), daemon=True).start()

    threading.Thread(target=accept_loop, daemon=True).start()

    def stop() -> None:
        stopped.set()
        server.close()
        for s in sockets:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()

    return port, stop
