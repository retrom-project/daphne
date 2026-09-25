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
    # after Asyncify finishes the startup rewind. This build runs RetroArch's
    # game loop on a pthread; the browser main thread must never resume its
    # stale MainLoop function after an asynchronous Content I/O read.
    resume = b'if(typeof MainLoop!="undefined"&&MainLoop.func){MainLoop.resume()}'
    if payload.count(resume) != 1:
        raise SystemExit(f"EMULATORJS_RUNTIME_ASYNCIFY_INVALID:resume={payload.count(resume)}")
    payload = payload.replace(
        resume,
        b'if(typeof MainLoop!="undefined"&&MainLoop.func&&ENVIRONMENT_IS_PTHREAD&&!Module.retromContentIOStartupPending){MainLoop.resume()}',
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
    # EmulatorJS leaves the DOM canvas ID empty. The transferred OffscreenCanvas
    # is then keyed under an empty name, so receiveObjectTransfer cannot assign
    # Module.canvas in the worker even though the transfer succeeded.
    module_canvas_id = b'var moduleCanvasId=Module["canvas"]?.id||"";'
    if payload.count(module_canvas_id) != 1:
        raise SystemExit("EMULATORJS_RUNTIME_CANVAS_ID_INVALID")
    payload = payload.replace(
        module_canvas_id,
        b'if(Module["canvas"]&&!Module["canvas"].id)Module["canvas"].id="retrom-daphne-canvas";var moduleCanvasId=Module["canvas"]?.id||"";',
    )
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
    # Emscripten proxies pthread fd_read to the browser main thread. Suspending
    # that proxied callback with Asyncify returns a short read to the worker
    # before the network result arrives. Keep the pthread asleep until the
    # runtime Content I/O reader has filled the shared Wasm buffer.
    worker_read = b'if(ENVIRONMENT_IS_PTHREAD)return proxyToMainThread(73,0,1,fd,iov,iovcnt,pnum);'
    if payload.count(worker_read) != 1:
        raise SystemExit("EMULATORJS_RUNTIME_WORKER_READ_INVALID")
    payload = payload.replace(
        worker_read,
        b'if(ENVIRONMENT_IS_PTHREAD){var control=new Int32Array(new SharedArrayBuffer(8));'
        b'postMessage({cmd:"retromContentRead",fd,iov,iovcnt,pnum,control:control.buffer});'
        b'while(Atomics.load(control,0)===0)Atomics.wait(control,0,0);'
        b'return Atomics.load(control,1)}',
    )
    main_message = b'var cmd=d.cmd;if(d.targetThread&&d.targetThread!=_pthread_self())'
    if payload.count(main_message) != 1:
        raise SystemExit("EMULATORJS_RUNTIME_MAIN_READ_INVALID")
    payload = payload.replace(
        main_message,
        b'var cmd=d.cmd;if(cmd==="retromContentRead"){'
        b'var control=new Int32Array(d.control);'
        b'Promise.resolve().then(()=>Module.retromDaphneFdRead?.(d.fd,d.iov>>>0,d.iovcnt,d.pnum>>>0))'
        b'.then(result=>result==null?_fd_read(d.fd,d.iov,d.iovcnt,d.pnum):result)'
        b'.then(result=>{Atomics.store(control,1,result);Atomics.store(control,0,1);Atomics.notify(control,0)},'
        b'error=>{err(error);Atomics.store(control,1,29);Atomics.store(control,0,1);Atomics.notify(control,0)});'
        b'return}if(d.targetThread&&d.targetThread!=_pthread_self())',
    )
    path.write_bytes(payload)


if __name__ == "__main__":
    main()
