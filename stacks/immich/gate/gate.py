#!/usr/bin/env python3
"""immich-gate —— Immich 按需启停网关（透明 TCP 转发 + 空闲休眠）

工作方式
--------
1. 监听 :2283。任何客户端连上来时：
   - 后端 immich_server:2283 可连  -> 直接转发
   - 不可连 -> 经 Docker socket 依次启动 infra 容器与 immich_server，
     等后端可连（实测约 5 秒）后再转发
2. 连续 IDLE_SECONDS 秒没有任何连接、且当前无活动连接 -> 停止全部容器
3. 转发是纯 TCP 字节流，对 HTTP/1.1、WebSocket、视频 Range 请求完全透明。
   网关不解析、不理解也不执行任何来自客户端的指令，因此没有可注入的入口。

环境变量
--------
  BACKEND_HOST / BACKEND_PORT   后端地址（默认 immich_server:2283）
  LISTEN_HOST / LISTEN_PORT     监听地址（默认 0.0.0.0:2283）
  IDLE_SECONDS                  空闲多久后休眠（默认 1800 = 30 分钟）
  READY_TIMEOUT                 唤醒等待上限（默认 90 秒）
  INFRA_CONTAINERS              先启动的基础容器，逗号分隔
  APP_CONTAINERS                随后启动的应用容器，逗号分隔
  DB_WAIT_HOST / DB_WAIT_PORT   启动应用前需等待的依赖（默认 immich_postgres:5432）
  DRY_RUN=1                     只记录不真启停，用于调试
"""

import http.client
import json
import os
import socket
import socketserver
import sys
import threading
import time

DOCKER_SOCK = os.environ.get("DOCKER_SOCK", "/var/run/docker.sock")
BACKEND_HOST = os.environ.get("BACKEND_HOST", "immich_server")
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "2283"))
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "2283"))
IDLE_SECONDS = int(os.environ.get("IDLE_SECONDS", "1800"))
READY_TIMEOUT = int(os.environ.get("READY_TIMEOUT", "90"))
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

DB_WAIT_HOST = os.environ.get("DB_WAIT_HOST", "immich_postgres")
DB_WAIT_PORT = int(os.environ.get("DB_WAIT_PORT", "5432"))

INFRA = [c.strip() for c in os.environ.get(
    "INFRA_CONTAINERS",
    "immich_postgres,immich_redis,immich_machine_learning",
).split(",") if c.strip()]
APP = [c.strip() for c in os.environ.get(
    "APP_CONTAINERS", "immich_server",
).split(",") if c.strip()]

STATE_LOCK = threading.RLock()
START_LOCK = threading.Lock()
STATE = {"running": False, "starting": False, "active": 0, "last": time.monotonic()}


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- Docker API

class _UnixHTTPConnection(http.client.HTTPConnection):
    """走 unix socket 的最小 HTTP 客户端，只用于访问 Docker API。"""

    def __init__(self, path, timeout=30):
        super().__init__("localhost", timeout=timeout)
        self._unix_path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._unix_path)


def docker_api(method, path, timeout=60):
    conn = _UnixHTTPConnection(DOCKER_SOCK, timeout=timeout)
    try:
        conn.request(method, path)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def container_running(name):
    try:
        status, body = docker_api("GET", f"/containers/{name}/json", timeout=10)
    except OSError as exc:
        log(f"无法访问 Docker socket: {exc}")
        return False
    if status != 200:
        return False
    try:
        return bool(json.loads(body).get("State", {}).get("Running"))
    except ValueError:
        return False


def docker_start(name):
    if DRY_RUN:
        log(f"[dry-run] start {name}")
        return True
    try:
        status, _ = docker_api("POST", f"/containers/{name}/start", timeout=90)
    except OSError as exc:
        log(f"启动 {name} 失败: {exc}")
        return False
    # 204 = 已启动, 304 = 本来就在运行
    if status in (204, 304, 200):
        return True
    if status == 404:
        log(f"容器 {name} 不存在：请先在 Immich 目录执行 docker compose up -d")
    else:
        log(f"启动 {name} 返回 HTTP {status}")
    return False


def docker_stop(name, timeout=45):
    if DRY_RUN:
        log(f"[dry-run] stop {name}")
        return True
    try:
        status, _ = docker_api("POST", f"/containers/{name}/stop?t={timeout}", timeout=timeout + 30)
    except OSError as exc:
        log(f"停止 {name} 失败: {exc}")
        return False
    return status in (204, 304, 200)


# ------------------------------------------------------------------ 后端探测

def port_open(host, port, timeout=1.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_port(host, port, limit, label=None):
    """轮询到端口可连或超时。"""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if port_open(host, port):
            return True
        time.sleep(0.2)
    if label:
        log(f"等待 {label} 超时（{limit}s）")
    return False


def connect_backend():
    return socket.create_connection((BACKEND_HOST, BACKEND_PORT), timeout=10)


def ensure_running():
    """保证后端可连。并发调用会排队，只有第一个真正执行启动。"""
    started = time.monotonic()
    with START_LOCK:
        if port_open(BACKEND_HOST, BACKEND_PORT):
            with STATE_LOCK:
                STATE["running"] = True
            return True

        with STATE_LOCK:
            STATE["starting"] = True
        try:
            log("收到访问但 Immich 未运行，开始唤醒")
            for name in INFRA:
                docker_start(name)
            # immich_server 需要 DB 就绪，否则可能启动即退出
            if not wait_port(DB_WAIT_HOST, DB_WAIT_PORT, 60, "immich_postgres"):
                log("警告：数据库端口未就绪，仍尝试启动 immich_server")
            for name in APP:
                docker_start(name)

            if wait_port(BACKEND_HOST, BACKEND_PORT, READY_TIMEOUT, "immich_server"):
                elapsed = time.monotonic() - started
                log(f"Immich 已就绪，用时 {elapsed:.1f}s")
                with STATE_LOCK:
                    STATE["running"] = True
                    STATE["last"] = time.monotonic()
                return True

            log("唤醒失败：后端在超时内未就绪")
            with STATE_LOCK:
                STATE["running"] = False
            return False
        finally:
            with STATE_LOCK:
                STATE["starting"] = False


def stop_stack():
    log("空闲超时，停止 Immich 释放内存")
    ok = True
    for name in APP + INFRA:          # 先停应用，再停基础设施
        ok = docker_stop(name) and ok
    with STATE_LOCK:
        STATE["running"] = False
    if ok:
        log("已停止，内存已释放")
    return ok


# ---------------------------------------------------------------- TCP 转发

def pipe(src, dst):
    try:
        while True:
            chunk = src.recv(65536)
            if not chunk:
                break
            dst.sendall(chunk)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        client = self.request
        client.settimeout(None)
        with STATE_LOCK:
            STATE["active"] += 1
            STATE["last"] = time.monotonic()
        upstream = None
        try:
            try:
                upstream = connect_backend()
            except OSError:
                if not ensure_running():
                    self.reject(client)
                    return
                try:
                    upstream = connect_backend()
                except OSError as exc:
                    log(f"唤醒后仍无法连接后端: {exc}")
                    self.reject(client)
                    return

            upstream.settimeout(None)
            helper = threading.Thread(target=pipe, args=(upstream, client), daemon=True)
            helper.start()
            pipe(client, upstream)
            helper.join(timeout=5)
        finally:
            for sock in (client, upstream):
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
            with STATE_LOCK:
                STATE["active"] -= 1
                STATE["last"] = time.monotonic()

    @staticmethod
    def reject(client):
        try:
            body = b"Immich is waking up, please retry in a few seconds.\n"
            client.sendall(
                b"HTTP/1.1 503 Service Unavailable\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n"
                b"Retry-After: 5\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n" + body
            )
        except OSError:
            pass


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------- 空闲监控

def idle_monitor():
    while True:
        time.sleep(10)
        with STATE_LOCK:
            idle = time.monotonic() - STATE["last"]
            active = STATE["active"]
            starting = STATE["starting"]
        if starting or active > 0 or idle < IDLE_SECONDS:
            continue
        # 真正停下之前确认容器确实在跑，避免空转日志
        if not any(container_running(name) for name in APP + INFRA):
            continue
        with START_LOCK:
            with STATE_LOCK:
                if STATE["active"] > 0:      # 双重检查，防止刚有请求进来
                    continue
            stop_stack()


def main():
    log(f"immich-gate 启动：监听 {LISTEN_HOST}:{LISTEN_PORT} -> {BACKEND_HOST}:{BACKEND_PORT}")
    log(f"空闲休眠 {IDLE_SECONDS}s；基础设施容器 {INFRA}；应用容器 {APP}")
    threading.Thread(target=idle_monitor, daemon=True).start()
    with Server((LISTEN_HOST, LISTEN_PORT), Handler) as srv:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            log("收到中断，退出")


if __name__ == "__main__":
    sys.exit(main())
