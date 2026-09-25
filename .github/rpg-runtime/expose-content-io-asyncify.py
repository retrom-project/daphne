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
        raise SystemExit(f"EMULATORJS_RUNTIME_ASYNCIFY_INVALID:marker={payload.count(marker)}")
    payload = payload.replace(
        marker,
        b'Module["retromContentIOAsyncify"]=()=>Asyncify;var Asyncify={',
    )
    # callMain can suspend before the game loop is ready; EmulatorJS resumes it
    # after Asyncify finishes the startup rewind.
    resume = b'if(typeof MainLoop!="undefined"&&MainLoop.func){MainLoop.resume()}'
    if payload.count(resume) != 1:
        raise SystemExit(f"EMULATORJS_RUNTIME_ASYNCIFY_INVALID:resume={payload.count(resume)}")
    payload = payload.replace(
        resume,
        b'if(typeof MainLoop!="undefined"&&MainLoop.func&&!Module.retromContentIOStartupPending){MainLoop.resume()}',
    )
    # A single WASI fd_read may include several iovecs. Once FS.read starts
    # unwinding, leave this JS loop so Wasm can save its call stack.
    readv = next((candidate for candidate in (
        b'var curr=FS.read(stream,HEAP8,ptr,len,offset);if(curr<0)return-1;',
        b'var curr=FS.read(stream,GROWABLE_HEAP_I8(),ptr,len,offset);if(curr<0)return-1;',
    ) if payload.count(candidate) == 1), None)
    if readv is None:
        raise SystemExit("EMULATORJS_RUNTIME_ASYNCIFY_INVALID:readv")
    payload = payload.replace(
        readv,
        readv.split(b'if(curr<0)')[0] +
        b'if(Asyncify.state===Asyncify.State.Unwinding)return ret;'
        b'if(curr<0)return-1;',
    )
    # SDL requests the Emscripten special target !canvas when it starts its
    # render thread. The pthread glue only recognizes #canvas and otherwise
    # passes !canvas to querySelector, which throws before the game starts.
    target = b'if(name=="#canvas"){if(!Module["canvas"])'
    if payload.count(target) != 1:
        raise SystemExit("EMULATORJS_RUNTIME_CANVAS_TARGET_INVALID")
    payload = payload.replace(
        target,
        b'if(name=="#canvas"||name=="!canvas"){if(!Module["canvas"])',
    )
    canvas_lookup = b'return GL.offscreenCanvases[target.substr(1)]||target=="canvas"&&Object.keys(GL.offscreenCanvases)[0]||typeof document!="undefined"&&document.querySelector(target)'
    if payload.count(canvas_lookup) != 1:
        raise SystemExit("EMULATORJS_RUNTIME_CANVAS_LOOKUP_INVALID")
    payload = payload.replace(
        canvas_lookup,
        b'return target=="!canvas"&&Module.canvas||' + canvas_lookup.removeprefix(b'return '),
    )
    event_lookup = b'var domElement=specialHTMLTargets[target]||(typeof document!="undefined"?document.querySelector(target):null);'
    if payload.count(event_lookup) != 1:
        raise SystemExit("EMULATORJS_RUNTIME_EVENT_TARGET_INVALID")
    payload = payload.replace(
        event_lookup,
        b'var domElement=(target=="!canvas"&&Module.canvas)||specialHTMLTargets[target]||(typeof document!="undefined"?document.querySelector(target):null);',
    )
    crash_handler = b'}catch(ex){__emscripten_thread_crashed();throw ex}}self.onmessage=handleMessage'
    if payload.count(crash_handler) != 1:
        raise SystemExit("EMULATORJS_RUNTIME_WORKER_ERROR_INVALID")
    payload = payload.replace(
        crash_handler,
        b'}catch(ex){console.error("DAPHNE_WORKER_EXCEPTION",ex);throw ex}}self.onmessage=handleMessage',
    )
    path.write_bytes(payload)


if __name__ == "__main__":
    main()
