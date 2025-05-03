import asyncio
import logging
import os
import traceback
import base64
import json
from typing import Optional, Dict, Any

import google.genai as genai
from google.genai import types  # Use types directly
import uvicorn
from dotenv import load_dotenv
import fastapi
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from google.api_core import exceptions as google_exceptions
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from concurrent.futures import ThreadPoolExecutor

# ==============================================================================
# Configuration & Constants
# ==============================================================================
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

LIVE_API_MODEL = "models/gemini-2.0-flash-live-001"
LIVE_API_VERSION = "v1beta"
AUDIO_SAMPLE_RATE = 16000  # Input sample rate from client

# --- LiveConnect Config ---
# Request both audio and text responses from Gemini
LIVE_CONFIG = None
try:
    LIVE_CONFIG = types.LiveConnectConfig(
        response_modalities=["audio"],  # Request both
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck")
            )
        ),
    )
    logger.info(f"LiveConnectConfig created successfully: {LIVE_CONFIG}")
except Exception as e:
    logger.error(f"Failed to create LiveConnectConfig: {e}. Disabling LiveConnect.", exc_info=True)
    LIVE_CONFIG = None

ALLOWED_ORIGINS = ["*"]
MAX_CONCURRENT_SESSIONS = 10

# ==============================================================================
# Initialization
# ==============================================================================
assert GOOGLE_API_KEY, "GOOGLE_API_KEY environment variable not set."

# --- Initialize Gemini Client ---
client = None
try:
    client = genai.Client(
        http_options={"api_version": LIVE_API_VERSION},
        api_key=GOOGLE_API_KEY,
    )
    logger.info(f"Gemini Client configured for Live API (version: {LIVE_API_VERSION}).")
except Exception as e:
    logger.error(f"Failed to initialize Gemini Client: {e}", exc_info=True)

# --- Initialize Executor (might not be needed anymore without PyAudio) ---
# executor = ThreadPoolExecutor(max_workers=5) # Keep if other blocking IO added later

# --- FastAPI App ---
app = FastAPI(title="Live Audio/Snapshot Forwarder")
app.add_middleware(
    CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

session_semaphore = asyncio.Semaphore(MAX_CONCURRENT_SESSIONS)


# ==============================================================================
# Live API Interaction Logic
# ==============================================================================

async def forward_input_to_gemini(
        websocket: WebSocket,
        session
        ,
        connection_id: str,
        stop_event: asyncio.Event  # Added stop event
):
    """Receives audio (bytes) and image (JSON) from client and forwards to Gemini."""
    input_count = 0
    try:
        while not stop_event.is_set():
            try:
                # Use wait_for to periodically check stop_event while waiting for message
                message = await asyncio.wait_for(websocket.receive(), timeout=0.5)
            except asyncio.TimeoutError:
                continue  # No message, check stop_event and loop

            input_count += 1
            gemini_input = None
            log_msg_prefix = f"[{connection_id}] Input {input_count}:"

            if "bytes" in message:
                audio_bytes = message["bytes"]
                if not audio_bytes: continue
                logger.debug(f"{log_msg_prefix} Received {len(audio_bytes)} audio bytes.")
                gemini_input = {"data": audio_bytes, "mime_type": "audio/pcm"}
            elif "text" in message:
                try:
                    json_data = json.loads(message["text"])
                    if isinstance(json_data, dict) and json_data.get("type") == "image":
                        mime_type = json_data.get("mime_type")
                        base64_data = json_data.get("data")
                        if mime_type == "image/jpeg" and base64_data:
                            logger.info(f"{log_msg_prefix} Received image ({mime_type}).")
                            gemini_input = {"data": base64_data, "mime_type": mime_type}
                        else:
                            logger.warning(f"{log_msg_prefix} Invalid image JSON: {json_data}")
                    else:
                        logger.warning(f"{log_msg_prefix} Unexpected TEXT: {message['text']}")
                except json.JSONDecodeError:
                    logger.warning(f"{log_msg_prefix} Non-JSON TEXT: {message['text']}")
                except Exception as json_err:
                    logger.error(f"{log_msg_prefix} Error processing TEXT: {json_err}", exc_info=True)

            if gemini_input:
                try:
                    # Check stop event *before* sending to Gemini
                    if stop_event.is_set(): break
                    await session.send(input=gemini_input)
                    logger.debug(f"{log_msg_prefix} Sent {gemini_input['mime_type']} to Gemini.")
                except (ConnectionClosedOK, ConnectionClosedError) as close_err:
                    logger.info(
                        f"[{connection_id}] Gemini session closed during send ({type(close_err).__name__}). Stopping forward.")
                    break
                except Exception as send_err:
                    logger.error(f"{log_msg_prefix} Failed send ({gemini_input['mime_type']}): {send_err}",
                                 exc_info=True)
                    break  # Exit forwarding loop on send error

    except WebSocketDisconnect:
        logger.info(f"[{connection_id}] Client WebSocket disconnected during forward_input.")
    except RuntimeError as e:
        if "Cannot call \"receive\"" in str(e):
            logger.info(f"[{connection_id}] Forward task tried to receive from already disconnected client.")
        else:
            logger.error(f"[{connection_id}] Runtime error in forward_input_to_gemini task: {e}", exc_info=True)
    except asyncio.CancelledError:
        logger.info(f"[{connection_id}] Forwarding task cancelled.")
    except Exception as e:
        logger.error(f"[{connection_id}] Error in forward_input_to_gemini task: {e}", exc_info=True)
    finally:
        logger.info(f"[{connection_id}] Forwarding input task finished.")
        stop_event.set()  # Signal other tasks to stop


async def receive_and_forward_response(
        websocket: WebSocket,  # Client websocket to send responses back to
        session
        ,
        connection_id: str,
        stop_event: asyncio.Event
):
    """Receives responses from Gemini and forwards them back to the client."""
    receive_count = 0
    try:
        while not stop_event.is_set():
            try:
                turn_generator = session.receive()
                async for response in turn_generator:
                    if stop_event.is_set(): break
                    receive_count += 1

                    # Forward received data back to the client
                    try:
                        if data := response.data:
                            # Forward audio data as bytes
                            logger.info(
                                f"[{connection_id}] Received AUDIO ({len(data)} bytes) from Gemini, forwarding to client.")
                            await websocket.send_bytes(data)
                        elif text := response.text:
                            # Forward text data as JSON (or plain text)
                            logger.info(f"[{connection_id}] Received TEXT from Gemini: '{text}', forwarding to client.")
                            # Send as JSON to distinguish from potential future control messages
                            await websocket.send_json({"type": "text", "data": text})
                        else:
                            logger.debug(
                                f"[{connection_id}] Received other response part {receive_count} from Gemini: {response}")
                    except WebSocketDisconnect:
                        logger.info(
                            f"[{connection_id}] Client disconnected while forwarding response. Stopping receive.")
                        stop_event.set()
                        break
                    except Exception as forward_err:
                        logger.error(f"[{connection_id}] Error forwarding response to client: {forward_err}",
                                     exc_info=True)
                        # Continue receiving from Gemini? Or stop? Let's continue for now.

                if stop_event.is_set(): break
                logger.info(f"[{connection_id}] Gemini turn completed.")

            except StopAsyncIteration:
                logger.info(f"[{connection_id}] StopAsyncIteration received, Gemini session ending.")
                stop_event.set()
                break
            except asyncio.TimeoutError:  # Should not happen unless session.receive adds timeout
                logger.debug(f"[{connection_id}] Receive timeout (unexpected).")
                continue

    except (asyncio.CancelledError):
        logger.info(f"[{connection_id}] Receiving task cancelled.")
    except ConnectionClosedOK:
        logger.info(f"[{connection_id}] Gemini session closed gracefully during receive.")
    except ConnectionClosedError as close_err:
        logger.error(f"[{connection_id}] Gemini connection error during receive (Code: {close_err.code}).")
    except Exception as e:
        logger.error(f"[{connection_id}] Error in receive_and_forward_response task: {e}", exc_info=True)
    finally:
        logger.info(f"[{connection_id}] Receiving/Forwarding response task finished.")
        stop_event.set()  # Ensure stop is signalled on any exit


# ==============================================================================
# WebSocket Endpoint
# ==============================================================================
@app.websocket("/ws/stream")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_host = websocket.client.host or "unknown"
    client_port = websocket.client.port or "unknown"
    connection_id = f"{client_host}:{client_port}-{os.urandom(4).hex()}"
    logger.info(f"WebSocket connection accepted from: {connection_id}")

    # --- Pre-checks ---
    if not client or not LIVE_CONFIG: await websocket.close(code=1011, reason="Server config error (Gemini)."); return
    # No PyAudio check needed

    session: Optional[genai.LiveConnectSession] = None
    all_tasks = []
    stop_event = asyncio.Event()

    async with session_semaphore:  # Limit concurrent sessions
        try:
            # --- Establish Gemini Live Connect Session ---
            logger.info(f"[{connection_id}] Establishing Gemini Live Connect session...")
            async with client.aio.live.connect(model=LIVE_API_MODEL, config=LIVE_CONFIG) as session:
                logger.info(f"[{connection_id}] Gemini Live Connect session established.")

                # --- Start Background Tasks ---
                # Task to forward client input (audio/images) to Gemini
                forward_task = asyncio.create_task(
                    forward_input_to_gemini(websocket, session, connection_id, stop_event),
                    name=f"forward_{connection_id}"
                )
                # Task to receive Gemini responses (audio/text) and forward them back to client
                receive_forward_task = asyncio.create_task(
                    receive_and_forward_response(websocket, session, connection_id, stop_event),
                    name=f"receive_{connection_id}"
                )
                all_tasks = [forward_task, receive_forward_task]

                # --- Wait for tasks ---
                done, pending = await asyncio.wait(all_tasks, return_when=asyncio.FIRST_COMPLETED)
                logger.info(
                    f"[{connection_id}] First task completed. Done: {[t.get_name() for t in done]}. Pending: {[t.get_name() for t in pending]}.")

        except ConnectionClosedError as close_err:
            logger.error(f"[{connection_id}] Connection to Gemini failed: {close_err.code}",
                         exc_info=True);  # ... close websocket ...
        except google_exceptions.GoogleAPIError as api_err:
            logger.error(f"[{connection_id}] Gemini API Error: {api_err}", exc_info=True);  # ... close websocket ...
        except Exception as e:
            logger.error(f"[{connection_id}] Error in session: {e}", exc_info=True);  # ... close websocket ...
        finally:
            logger.info(f"[{connection_id}] Cleaning up session resources...")
            stop_event.set()  # Signal tasks to stop

            # --- Cancel remaining tasks ---
            await asyncio.sleep(0.1)
            tasks_to_cancel = [t for t in all_tasks if t and not t.done()]
            if tasks_to_cancel:
                logger.info(f"[{connection_id}] Cancelling {len(tasks_to_cancel)} pending task(s)...")
                for task in tasks_to_cancel: task.cancel()
                await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
                logger.info(f"[{connection_id}] Pending tasks cancelled.")

            # --- Close Client WebSocket if needed ---
            if websocket.client_state == fastapi.websockets.WebSocketState.CONNECTED:
                await websocket.close(code=1000)  # Normal closure

            logger.info(f"[{connection_id}] WebSocket session cleanup complete for {connection_id}.")


# ==============================================================================
# Root Endpoint & Server Runner
# ==============================================================================
@app.get("/")
async def read_root(): return {"message": "Live Audio/Snapshot Forwarder (Client Playback)"}


if __name__ == "__main__":
    print("Starting Live Audio/Snapshot Forwarder (Client Playback)...")
    if client and LIVE_CONFIG:
        print(f"Gemini OK. Model: {LIVE_API_MODEL}, API: {LIVE_API_VERSION}")
    else:
        print("Warning: Gemini Client or Config failed init.")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True, log_level="info")
