import socket
import threading
import time
import struct
import zlib
import functools
import itertools
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from opentdx.utils.log import log
from opentdx.utils.heartbeat import HeartBeatThread

CONNECT_TIMEOUT = 5.0
RSP_HEADER_LEN = 0x10


class ResponseDispatcher:
    """响应分发器：维护 customize -> Future 的映射，reader 线程收到响应后唤醒对应 Future"""

    def __init__(self):
        self._pending: dict[int, Future] = {}
        self._lock = threading.Lock()

    def register(self, customize: int) -> Future:
        future = Future()
        with self._lock:
            self._pending[customize] = future
        return future

    def resolve(self, customize: int, data: bytes):
        with self._lock:
            future = self._pending.pop(customize, None)
        if future and not future.done():
            future.set_result(data)

    def reject(self, customize: int, exception: Exception):
        with self._lock:
            future = self._pending.pop(customize, None)
        if future and not future.done():
            future.set_exception(exception)

    def reject_all(self, exception: Exception):
        with self._lock:
            pending = self._pending
            self._pending = {}
        for future in pending.values():
            if not future.done():
                future.set_exception(exception)


def update_last_ack_time(func):
    @functools.wraps(func)
    def wrapper(self, *args, **kw):
        ht = getattr(self, 'heartbeat_thread', None)
        if ht is None:
            t = getattr(self, '_t', None)
            if t is not None:
                ht = getattr(t, 'heartbeat_thread', None)
        if ht and ht.is_alive():
            ht.update_last_ack_time()

        try:
            return func(self, *args, **kw)
        except Exception:
            if not self.auto_retry:
                if self.raise_exception:
                    raise
                return None

            log.debug("request failed, starting auto retry")
            last_exception = None
            for time_interval in self.retry_strategy.gen():
                try:
                    time.sleep(time_interval)
                    self.disconnect()
                    self.connect(self.ip, self.port)
                    ret = func(self, *args, **kw)
                    if ret is not None:
                        return ret
                except Exception as e:
                    last_exception = e
                    log.debug("retry failed: %s", e)

            if self.raise_exception:
                raise last_exception if last_exception else RuntimeError("auto retry exhausted")
            return None
    return wrapper


def _paginate(fetch_fn, page_size, count, start=0):
    results = []
    remaining = count if count != 0 else float('inf')
    while remaining > 0:
        req_count = min(remaining, page_size)
        part = fetch_fn(start, req_count)
        results.extend(part)
        if len(part) < req_count:
            break
        remaining -= len(part)
        start += len(part)
    return results


def _normalize_code_list(code_list, code=None):
    if code is not None:
        return [(code_list, code)]
    if (isinstance(code_list, list) or isinstance(code_list, tuple)) \
            and len(code_list) == 2 and isinstance(code_list[0], (int, Enum)):
        return [code_list]
    return code_list


class DefaultRetryStrategy:
    def gen(self):
        for time_interval in [0.1, 0.5, 1, 2]:
            yield time_interval


class Transport:
    hosts = []

    def __init__(self, multithread=False, heartbeat=False, auto_retry=False,
                 raise_exception=False, nonblocking=False):
        self.client = None
        self.ip = None
        self.port = None

        if multithread or heartbeat:
            self.lock = threading.Lock()
        else:
            self.lock = None
        self.heartbeat = heartbeat
        self.heartbeat_thread = None
        self._stop_event = None
        self.connected = False

        self.auto_retry = auto_retry
        self.retry_strategy = DefaultRetryStrategy()
        self.raise_exception = raise_exception

        self._heartbeat_callback = None

        self.nonblocking = nonblocking
        self._reader_thread = None
        self._dispatcher = None
        self._customize_counter = itertools.count(1)
        self._customize_lock = threading.Lock()

    def set_heartbeat_callback(self, callback):
        self._heartbeat_callback = callback

    def _next_customize(self) -> int:
        with self._customize_lock:
            return next(self._customize_counter)

    def _reader_loop(self):
        while not self._stop_event.is_set():
            try:
                self.client.settimeout(0.5)
                head_buf = self.client.recv(RSP_HEADER_LEN)
                if not head_buf:
                    break
                prefix, zipped, customize, unknown, msg_id, zipsize, unzip_size = \
                    struct.unpack('<IBIBHHH', head_buf)
                body = bytearray()
                remaining = zipsize
                while remaining > 0:
                    chunk = self.client.recv(remaining)
                    if not chunk:
                        raise ConnectionError("connection closed while receiving body")
                    body.extend(chunk)
                    remaining -= len(chunk)
                if zipsize != unzip_size:
                    body = zlib.decompress(body)
                self._dispatcher.resolve(customize, bytes(body))
            except socket.timeout:
                continue
            except (OSError, ConnectionError, struct.error) as e:
                if self._stop_event.is_set():
                    break
                log.warning("reader loop error: %s", e)
                self._dispatcher.reject_all(e)
                if self.client:
                    try:
                        self.client.close()
                    except OSError:
                        pass
                self.client = None
                self.connected = False
                break
        log.debug("reader loop exited")

    def connect(self, ip=None, port=7709, time_out=5, bind_port=None, bind_ip='0.0.0.0'):
        if ip is None:
            infos = []
            def get_latency(target_ip, target_port, timeout):
                client = None
                try:
                    start_time = time.time()
                    family = socket.AF_INET6 if ":" in target_ip else socket.AF_INET
                    client = socket.socket(family, socket.SOCK_STREAM)
                    client.settimeout(timeout)
                    client.connect((target_ip, target_port))
                    infos.append({
                        'ip': target_ip,
                        'port': target_port,
                        'time': time.time() - start_time,
                    })
                except Exception:
                    pass
                finally:
                    if client is not None:
                        try:
                            client.close()
                        except OSError:
                            pass
            max_workers = min(10, len(self.hosts))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(get_latency, host[1], host[2], 1): host
                    for host in self.hosts
                }
                for f in as_completed(futures):
                    pass

            infos.sort(key=lambda x: x['time'])
            if len(infos) == 0:
                raise Exception("no available server")

            return self._connect(infos[0]['ip'], infos[0]['port'], time_out, bind_port, bind_ip)
        else:
            return self._connect(ip, port, time_out, bind_port, bind_ip)

    def _connect(self, ip, port=7709, time_out=CONNECT_TIMEOUT, bind_port=None, bind_ip='0.0.0.0'):
        if ip.count(":") > 0:
            self.client = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        else:
            self.client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        self.ip = ip
        self.port = port
        self.client.settimeout(time_out)
        log.debug("connecting to server : %s on port :%d" % (ip, port))

        try:
            if bind_port is not None:
                self.client.bind((bind_ip, bind_port))
            self.client.connect((ip, port))
        except socket.timeout as e:
            self.client = None
            self.connected = False
            log.debug("connection expired")
            if self.raise_exception:
                raise Exception("connection timeout error", e)
            return None
        except Exception as e:
            self.client = None
            self.connected = False
            if self.raise_exception:
                raise Exception("other errors", e)
            return None

        log.debug("connected!")
        self.connected = True

        if self.nonblocking:
            self._dispatcher = ResponseDispatcher()
            self._stop_event = threading.Event()
            self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
            self._reader_thread.start()

        if self.heartbeat and self._heartbeat_callback and \
                not (self.heartbeat_thread and self.heartbeat_thread.is_alive()):
            if self._stop_event is None:
                self._stop_event = threading.Event()
            self.heartbeat_thread = HeartBeatThread(self.client, self._stop_event, self._heartbeat_callback)
            self.heartbeat_thread.daemon = True
            self.heartbeat_thread.start()
        return self

    def disconnect(self):
        if self._stop_event:
            self._stop_event.set()

        if self.heartbeat_thread and self.heartbeat_thread.is_alive():
            self.heartbeat_thread.join(timeout=2)
        self.heartbeat_thread = None

        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2)
        self._reader_thread = None
        self._dispatcher = None
        self._stop_event = None

        if self.client:
            log.debug("disconnecting")
            try:
                self.client.shutdown(socket.SHUT_RDWR)
                self.client.close()
                self.client = None
            except Exception as e:
                log.debug(str(e))
                if self.raise_exception:
                    raise Exception("disconnect err")
            log.debug("disconnected")
            self.connected = False

    def send(self, data):
        if self.lock:
            with self.lock:
                return self._send(data)
        else:
            return self._send(data)

    def _send(self, data):
        if not self.client:
            log.debug("not connected")
            if self.raise_exception:
                raise Exception("not connected")
            return None

        if self.nonblocking:
            return self._send_async(data)
        else:
            return self._send_sync(data)

    def _send_async(self, data):
        cid = self._next_customize()
        data = bytearray(data)
        struct.pack_into('<I', data, 1, cid)

        future = self._dispatcher.register(cid)
        try:
            sent = self.client.send(data)
            if sent != len(data):
                self._dispatcher.reject(cid, Exception("send data error"))
                return None
        except Exception as e:
            self._dispatcher.reject(cid, e)
            self.connected = False
            if self.raise_exception:
                raise
            return None
        return future

    def _send_sync(self, data):
        try:
            send_data = self.client.send(data)
            if send_data != len(data):
                log.debug("send data error")
                if self.raise_exception:
                    raise Exception("send data error")
                return None
            else:
                head_buf = self.client.recv(RSP_HEADER_LEN)
                prefix, zipped, customize, unknown, msg_id, zipsize, unzip_size = struct.unpack('<IBIBHHH', head_buf)
                need_unzip_size = zipsize != unzip_size
                body_buf = bytearray()
                while zipsize > 0:
                    data_buf = self.client.recv(zipsize)
                    if not data_buf:
                        raise Exception("connection closed while receiving data")
                    body_buf.extend(data_buf)
                    zipsize -= len(data_buf)
                if need_unzip_size:
                    body_buf = zlib.decompress(body_buf)
                return body_buf
        except Exception as e:
            log.warning("send error: %s", e)
            self.connected = False
            if self.client:
                try:
                    self.client.close()
                except OSError:
                    pass
                self.client = None
            if self.raise_exception:
                raise Exception("send error") from e

    def download_file(self, fetch_fn, filename: str, filesize=0, report_hook=None):
        file_content = bytearray()
        current_downloaded_size = 0
        get_zero_length_package_times = 0
        while current_downloaded_size < filesize or filesize == 0:
            response = fetch_fn(filename, current_downloaded_size)
            if not response:
                break
            if response["size"] > 0:
                current_downloaded_size += response["size"]
                file_content.extend(response["data"])
                if report_hook is not None:
                    report_hook(current_downloaded_size, filesize)
            else:
                get_zero_length_package_times += 1
                if filesize == 0:
                    break
                elif get_zero_length_package_times > 2:
                    break
        return file_content
