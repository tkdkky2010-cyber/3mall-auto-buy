#!/usr/bin/env python3
"""127.0.0.1:<lport> → <phone_ip>:<rport> 단순 TCP 포워더.

adb 프로세스가 LAN 주소로 직접 연결하면 EHOSTUNREACH 로 막히는데(맥 로컬네트워크 권한),
루프백은 막히지 않는다. adb 는 127.0.0.1 로 붙이고 실제 전송만 이 프로세스가 대신한다.
(adb-tls 는 기기 키 기반이라 호스트명 검증이 없어 그대로 통과한다.)
"""
import socket
import sys
import threading

def pipe(a: socket.socket, b: socket.socket) -> None:
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()

def serve(lport: int, rhost: str, rport: int) -> None:
    ls = socket.socket()
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind(("127.0.0.1", lport))
    ls.listen(8)
    print(f"[fwd] 127.0.0.1:{lport} -> {rhost}:{rport}", flush=True)
    while True:
        c, _ = ls.accept()
        try:
            r = socket.create_connection((rhost, rport), timeout=5)
        except OSError as e:
            print(f"[fwd] upstream 실패: {e}", flush=True)
            c.close()
            continue
        # ★연결 타임아웃은 연결에만 쓰고 반드시 푼다. 남겨두면 5초 idle 마다 recv 가
        #   socket.timeout(OSError) 을 던져 파이프가 끊기고 adb 가 'device offline' 이 된다.
        r.settimeout(None)
        c.settimeout(None)
        for s in (c, r):
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        threading.Thread(target=pipe, args=(c, r), daemon=True).start()
        threading.Thread(target=pipe, args=(r, c), daemon=True).start()

if __name__ == "__main__":
    host = sys.argv[1]
    pairs = [tuple(int(x) for x in a.split(":")) for a in sys.argv[2:]]   # lport:rport
    for lp, rp in pairs[:-1]:
        threading.Thread(target=serve, args=(lp, host, rp), daemon=True).start()
    lp, rp = pairs[-1]
    serve(lp, host, rp)
