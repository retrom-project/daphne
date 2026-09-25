#!/usr/bin/env python3
"""Expose the pinned EmulatorJS Asyncify runtime to its shared virtual FS mount."""

import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("EMULATORJS_RUNTIME_ASYNCIFY_INVALID")
    path = Path(sys.argv[1])
    payload = path.read_bytes()
    marker = b"var Asyncify={"
    if payload.count(marker) != 1 or b"retromContentIOAsyncify" in payload:
        raise SystemExit("EMULATORJS_RUNTIME_ASYNCIFY_INVALID")
    payload = payload.replace(
        marker,
        b'Module["retromContentIOAsyncify"]=()=>Asyncify;var Asyncify={',
    )
    # callMain can suspend before the game loop is ready; EmulatorJS resumes it
    # after Asyncify finishes the startup rewind.
    resume = b'if(typeof MainLoop!="undefined"&&MainLoop.func){MainLoop.resume()}'
    if payload.count(resume) != 1:
        raise SystemExit("EMULATORJS_RUNTIME_ASYNCIFY_INVALID")
    payload = payload.replace(
        resume,
        b'if(typeof MainLoop!="undefined"&&MainLoop.func&&!Module.retromContentIOStartupPending){MainLoop.resume()}',
    )
    # A single WASI fd_read may include several iovecs. Once FS.read starts
    # unwinding, leave this JS loop so Wasm can save its call stack.
    readv = b'var curr=FS.read(stream,HEAP8,ptr,len,offset);if(curr<0)return-1;'
    if payload.count(readv) != 1:
        raise SystemExit("EMULATORJS_RUNTIME_ASYNCIFY_INVALID")
    payload = payload.replace(
        readv,
        b'var curr=FS.read(stream,HEAP8,ptr,len,offset);'
        b'if(Asyncify.state===Asyncify.State.Unwinding)return ret;'
        b'if(curr<0)return-1;',
    )
    path.write_bytes(payload)


if __name__ == "__main__":
    main()
