"""Current LightCraft MCP transport and strict evidence decoder. No legacy workbench."""
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path


class LightCraft:
    def __init__(self, binary, source, run):
        self.run = Path(run)
        self._close_error = None
        self._exit_known = False
        self.stderr = (self.run / "lightcraft.stderr.log").open("a", encoding="utf-8")
        self.process = subprocess.Popen([str(binary), "mcp", "--compact", "--library", str(run / "library"), str(source)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
                                        text=True, encoding="utf-8", bufsize=1)
        self.responses = queue.Queue()
        self.sequence = 0
        threading.Thread(target=self._reader, daemon=True).start()
        try:
            self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "photo-judgment", "version": "0.1"}})
            self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception as exc:
            try:
                self.close()
            except Exception as cleanup:
                exc.add_note('LightCraft initialization cleanup failed: '+repr(cleanup))
            raise

    def _reader(self):
        for line in self.process.stdout:
            try:
                self.responses.put(json.loads(line))
            except json.JSONDecodeError:
                self.responses.put({"error": {"message": "non-JSON LightCraft stdout"}})
        self.responses.put({"error": {"message": "LightCraft exited"}})

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method, params):
        self.sequence += 1
        expected = self.sequence
        self.send({"jsonrpc": "2.0", "id": expected, "method": method, "params": params})
        deadline = time.monotonic() + 90
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("LightCraft response deadline reached; native outcome requires reconciliation")
            response = self.responses.get(timeout=max(0.01, deadline - time.monotonic()))
            if response.get("error"):
                raise RuntimeError(str(response["error"]))
            if response.get("id") == expected:
                return response["result"]
            if "id" in response:
                raise RuntimeError("Unexpected LightCraft response ID; do not discard or replay native operations")

    def call(self, name, args):
        result = self.request("tools/call", {"name": name, "arguments": args})
        if result.get("isError"):
            raise RuntimeError(json.dumps(result, ensure_ascii=False))
        return result

    def close(self):
        # A second finally/close must not turn an unknown cleanup into success.
        if self._close_error is not None:
            raise self._close_error
        if self._exit_known:
            return
        original = sys.exception()
        errors = []
        exited = False
        first_timeout = None
        try:
            self.process.stdin.close()
        except Exception as exc:
            errors.append(('stdin.close', exc))
        try:
            self.process.wait(timeout=8)
            exited = True
        except subprocess.TimeoutExpired as exc:
            first_timeout = exc
            errors.append(('wait', exc))
        except Exception as exc:
            errors.append(('wait', exc))
        if not exited:
            try:
                self.process.terminate()
            except Exception as exc:
                errors.append(('terminate', exc))
            try:
                self.process.wait(timeout=8)
                exited = True
            except Exception as exc:
                errors.append(('wait_after_terminate', exc))
            if not exited:
                try:
                    exited = self.process.poll() is not None
                except Exception as exc:
                    errors.append(('poll', exc))
        if exited and first_timeout is not None:
            errors = [(step, exc) for step, exc in errors if exc is not first_timeout]
        self._exit_known = exited
        try:
            self.stderr.close()
        except Exception as exc:
            errors.append(('stderr.close', exc))
        if not exited:
            marker = self.run / 'native-cleanup-unknown.json'
            evidence = {'schema':'native-cleanup-unknown/v1', 'time':time.time(),
                        'pid':getattr(self.process,'pid',None), 'exit_known':False,
                        'original_error':None if original is None else repr(original),
                        'cleanup_errors':[{'step':step,'error':repr(exc)} for step,exc in errors],
                        'resume':'Offline process/library reconciliation required; do not remove the worker lease automatically'}
            try:
                # Preserve the first unknown record, including across another close.
                with marker.open('x', encoding='utf-8') as stream:
                    json.dump(evidence, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                pass
            except Exception as exc:
                errors.append(('cleanup_marker', exc))
        if errors or not exited:
            failure = errors[0][1] if errors else RuntimeError('LightCraft exit is unknown')
            for step, exc in errors:
                failure.add_note('LightCraft cleanup '+step+': '+repr(exc))
            if original is not None:
                failure.add_note('Original operation error: '+repr(original))
            if not exited:
                failure.add_note('Native exit unknown; retain worker lease and reconcile '+str(marker))
            self._close_error = failure
            raise failure


def strict_json(text):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate JSON key')
            value[key] = item
        return value
    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('Nonfinite JSON value')
        return number
    def invalid(_value):
        raise ValueError('Nonfinite JSON value')
    return json.loads(text, object_pairs_hook=unique, parse_float=finite, parse_constant=invalid)


def structured(result):
    if result.get("isError"):
        raise ValueError("backend returned an error, not evidence")
    def decode(text):
        text=text.strip();parts=text.splitlines()
        if len(parts)>=3 and parts[0].strip().lower() in ["```json","```"] and parts[-1].strip()=="```":
            text="\n".join(parts[1:-1])
        return strict_json(text)
    value=result.get("structuredContent")
    if value is not None:
        return decode(value) if isinstance(value,str) else value
    texts=[x["text"] for x in result.get("content",[]) if x.get("type")=="text" and isinstance(x.get("text"),str)]
    def equivalent(a,b):
        if isinstance(a,bool) or isinstance(b,bool):return type(a) is type(b) and a==b
        if isinstance(a,dict) and isinstance(b,dict):return a.keys()==b.keys() and all(equivalent(a[k],b[k]) for k in a)
        if isinstance(a,list) and isinstance(b,list):return len(a)==len(b) and all(equivalent(x,y) for x,y in zip(a,b))
        return a==b
    candidates=[]
    for text in [*texts,"".join(texts)]:
        try:
            value=decode(text)
            if value is not None and not any(equivalent(value,x) for x in candidates):candidates.append(value)
        except (json.JSONDecodeError,ValueError):
            pass
    if len(candidates)==1:return candidates[0]
    raise ValueError("tool returned ambiguous structured evidence" if candidates else "tool returned no structured settings")
