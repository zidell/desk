"""One isolated AT-SPI client per desk session, with bounded failure recovery."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time


class AccessibilityWorker:
    def __init__(self, command=None):
        self.environment=None
        if command is None:self.environment=runtime_environment()
        self.command=command or [sys.executable,str(Path(__file__).with_name("accessibility.py")),"--serve"]
        self.process=None
        self.buffer=b""
        self.sequence=0
        self.retry_at=0
        self.failures=0

    def close(self):
        process,self.process=self.process,None
        self.buffer=b""
        if process is None:return
        try:
            process.stdin.close()
            process.wait(timeout=.2)
        except (OSError,subprocess.TimeoutExpired):
            if process.poll() is None:process.kill()
            process.wait()
        finally:
            process.stdout.close()

    def request(self, window, pump=None, timeout=5):
        if time.monotonic()<self.retry_at:
            raise TimeoutError("접근성 도구 복구 대기 중")
        try:
            if self.process is None:
                self.process=subprocess.Popen(self.command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=self.environment)
            self.sequence+=1
            payload={"request_id":self.sequence,"window":window}
            self.process.stdin.write(json.dumps(payload).encode()+b"\n")
            self.process.stdin.flush()
            deadline=time.monotonic()+timeout
            while b"\n" not in self.buffer:
                if pump:pump(.005)
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TimeoutError("접근성 도구 응답 시간 초과")
                if not select.select([self.process.stdout],[],[],min(.02,remaining))[0]:continue
                chunk=os.read(self.process.stdout.fileno(),65536)
                if not chunk:raise EOFError("접근성 도구 연결 종료")
                self.buffer+=chunk
                if len(self.buffer)>8*1024*1024:raise ValueError("접근성 응답 크기 초과")
            line,self.buffer=self.buffer.split(b"\n",1)
            result=json.loads(line)
            if result.get("request_id")!=self.sequence:raise ValueError("접근성 응답 ID 불일치")
            if not isinstance(result.get("bindings"),list) or not isinstance(result.get("warnings"),list):
                raise ValueError("접근성 응답 형식 오류")
            self.failures=0
            self.retry_at=0
            return result
        except BaseException:
            self.close()
            self.failures=min(self.failures+1,5)
            self.retry_at=time.monotonic()+min(60,5*2**(self.failures-1))
            raise


def runtime_environment():
    from atspi_runtime.build import build
    runtime=build(Path.home()/'.cache'/'desk'/'linux'/'atspi')
    old=os.environ.get('LD_LIBRARY_PATH','')
    return {**os.environ,'DESK_PRIVATE_ATSPI':str(runtime),
            'LD_LIBRARY_PATH':str(runtime)+(os.pathsep+old if old else '')}
