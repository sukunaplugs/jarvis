import asyncio
import json
import os
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import Response, PlainTextResponse
from starlette.websockets import WebSocketState

app = FastAPI(title="AI Phone Assistant")
STARTED_AT = time.time()

PUBLIC_WSS_URL = os.getenv("PUBLIC_WSS_URL", "").rstrip("/")
BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "")
WORKSPACE_DIR = Path(os.getenv("WORKSPACE_DIR", ".")).resolve()
ENABLE_TERMINAL = os.getenv("ENABLE_TERMINAL", "false").lower() == "true"


@app.get("/")
async def root():
    return PlainTextResponse("AI Phone Assistant is running. Health: /health")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "uptimeSeconds": int(time.time() - STARTED_AT),
    }


@app.post("/incoming-call")
async def incoming_call(request: Request):
    if not PUBLIC_WSS_URL.startswith("wss://"):
        raise HTTPException(
            status_code=500,
            detail="PUBLIC_WSS_URL must be configured as a wss:// URL.",
        )

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say>Hello! Connecting you to your AI coding assistant. Speak your commands.</Say>
  <Connect>
    <Stream url="{PUBLIC_WSS_URL}/media-stream" />
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


def safe_workspace_path(requested_path: str) -> Path:
    if not isinstance(requested_path, str) or not requested_path.strip():
        raise ValueError("A file path is required.")

    target = (WORKSPACE_DIR / requested_path).resolve()
    try:
        target.relative_to(WORKSPACE_DIR)
    except ValueError:
        raise ValueError("Path is outside the configured workspace.")
    return target


async def send_json(websocket: WebSocket, payload: dict):
    if websocket.client_state == WebSocketState.CONNECTED:
        await websocket.send_text(json.dumps(payload))


@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    await websocket.accept()
    print("Twilio media stream connected.")

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                print("Ignored invalid JSON from media stream.")
                continue

            event = message.get("event")
            if event == "start":
                print(f"Call started. Stream SID: {message.get('streamSid', 'unknown')}")
            elif event == "media":
                # Twilio audio is received here but is not yet sent to an AI model.
                # Add your speech-to-speech integration at this point.
                pass
            elif event == "stop":
                print("Call ended.")
    except WebSocketDisconnect:
        print("Twilio media stream disconnected.")


@app.websocket("/vscode-bridge")
async def vscode_bridge(websocket: WebSocket, token: Optional[str] = None):
    # Query-string token is used because browser WebSocket clients cannot
    # reliably set custom Authorization headers.
    if not BRIDGE_TOKEN or not token or not secrets.compare_digest(token, BRIDGE_TOKEN):
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()
    print("Authorized VS Code workspace connected.")

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await send_json(websocket, {"status": "error", "message": "Invalid JSON message."})
                continue

            action = data.get("action")

            if action == "writeFile":
                try:
                    target = safe_workspace_path(data.get("path"))
                    content = data.get("content")
                    if not isinstance(content, str):
                        raise ValueError("File content must be a string.")

                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                    await send_json(websocket, {
                        "status": "success",
                        "message": "File written successfully.",
                        "path": str(target.relative_to(WORKSPACE_DIR)),
                    })
                except Exception as exc:
                    await send_json(websocket, {"status": "error", "message": str(exc)})

            elif action == "runTerminal":
                if not ENABLE_TERMINAL:
                    await send_json(websocket, {
                        "status": "error",
                        "message": "Terminal execution is disabled. Set ENABLE_TERMINAL=true only for a trusted bridge.",
                    })
                    continue

                command = data.get("command")
                if not isinstance(command, str) or not command.strip():
                    await send_json(websocket, {"status": "error", "message": "A terminal command is required."})
                    continue

                try:
                    # WARNING: shell commands can damage the server. Keep the bridge
                    # private, authenticated, and disabled unless genuinely required.
                    process = await asyncio.create_subprocess_shell(
                        command,
                        cwd=str(WORKSPACE_DIR),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    try:
                        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.communicate()
                        await send_json(websocket, {
                            "status": "error",
                            "message": "Command timed out after 15 seconds.",
                        })
                        continue

                    if process.returncode == 0:
                        await send_json(websocket, {
                            "status": "success",
                            "output": stdout.decode(errors="replace"),
                            "stderr": stderr.decode(errors="replace"),
                        })
                    else:
                        await send_json(websocket, {
                            "status": "error",
                            "output": stderr.decode(errors="replace"),
                            "stdout": stdout.decode(errors="replace"),
                            "returnCode": process.returncode,
                        })
                except Exception as exc:
                    await send_json(websocket, {"status": "error", "message": str(exc)})

            else:
                await send_json(websocket, {"status": "error", "message": "Unsupported action."})

    except WebSocketDisconnect:
        print("VS Code bridge disconnected.")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "3000"))
    uvicorn.run("Userbot:app", host="0.0.0.0", port=port)
