import asyncio
import logging
import os
import traceback
import base64
import json
from typing import Optional, Dict, Any

# --- Audio Playback ---
import pyaudio # Added for server-side playback

import google.genai as genai
from google.genai import types
import uvicorn
from dotenv import load_dotenv
import fastapi # Ensure fastapi is imported if using its types directly
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from google.api_core import exceptions as google_exceptions
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from concurrent.futures import ThreadPoolExecutor # Re-import for clarity

# ==============================================================================
# Configuration & Constants
# ==============================================================================
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

LIVE_API_MODEL = "models/gemini-2.0-flash-live-001"
LIVE_API_VERSION = "v1beta"
AUDIO_SAMPLE_RATE = 16000 # Input audio rate from client

# --- PyAudio Config ---
# Gemini Live API typically outputs at 24kHz based on examples
PYAUDIO_OUTPUT_RATE = 24000
PYAUDIO_FORMAT = pyaudio.paInt16 # Assuming 16-bit PCM output from Gemini
PYAUDIO_CHANNELS = 1 # Assuming mono output

# --- LiveConnect Config ---
LIVE_CONFIG = None
try:
    LIVE_CONFIG = types.LiveConnectConfig(
        response_modalities=["audio"], # MUST include "audio" to get audio data back
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck")
            )
        ),
        # Note: No explicit output format config here, assuming defaults (e.g., 24kHz PCM)
    )
    logger.info(f"LiveConnectConfig created successfully: {LIVE_CONFIG}")
except AttributeError as e:
     logger.error(f"Failed LiveConnectConfig (AttributeError). Update google-genai? {e}", exc_info=True)
     LIVE_CONFIG = None
except Exception as e:
     logger.error(f"Error creating LiveConnectConfig: {e}", exc_info=True)
     LIVE_CONFIG = None

ALLOWED_ORIGINS = ["*"] # Simplified for local testing, restrict in production

# ==============================================================================
# Initialization
# ==============================================================================
assert GOOGLE_API_KEY, "GOOGLE_API_KEY environment variable not set."

# --- Initialize PyAudio ---
try:
    pya = pyaudio.PyAudio()
    logger.info(f"PyAudio initialized successfully (Version: {pyaudio.get_portaudio_version_text()}).")
except Exception as e:
    logger.error(f"Failed to initialize PyAudio. Playback disabled. Error: {e}", exc_info=True)
    pya = None

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

# --- Initialize Executor ---
executor = ThreadPoolExecutor(max_workers=5) # For running blocking PyAudio writes

# --- FastAPI App ---
app = FastAPI(title="Live Audio + Snapshot Processor with Playback")
app.add_middleware(
    CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ==============================================================================
# Live API Interaction Logic
# ==============================================================================

# forward_input_to_gemini remains the same
async def forward_input_to_gemini(
    websocket: WebSocket,
    session,
    connection_id: str
):
    """Receives audio (bytes) and image (JSON) from client and forwards to Gemini."""
    # (Code from previous version - no changes needed here)
    input_count = 0
    try:
        while True:
            message = await websocket.receive()
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
                        else: logger.warning(f"{log_msg_prefix} Invalid image JSON: {json_data}")
                    else: logger.warning(f"{log_msg_prefix} Unexpected TEXT: {message['text']}")
                except json.JSONDecodeError: logger.warning(f"{log_msg_prefix} Non-JSON TEXT: {message['text']}")
                except Exception as json_err: logger.error(f"{log_msg_prefix} Error processing TEXT: {json_err}", exc_info=True)

            if gemini_input:
                try:
                    await session.send(input=gemini_input)
                    logger.debug(f"{log_msg_prefix} Sent {gemini_input['mime_type']} to Gemini.")
                except (ConnectionClosedOK, ConnectionClosedError) as close_err:
                     logger.info(f"[{connection_id}] Gemini session closed during send ({type(close_err).__name__}). Stopping forward.")
                     break
                except Exception as send_err:
                    logger.error(f"{log_msg_prefix} Failed send ({gemini_input['mime_type']}): {send_err}", exc_info=True)
                    break
    except WebSocketDisconnect: logger.info(f"[{connection_id}] Client WebSocket disconnected during forward_input.")
    except RuntimeError as e:
        if "Cannot call \"receive\"" in str(e): logger.info(f"[{connection_id}] Forward task tried to receive from already disconnected client.")
        else: logger.error(f"[{connection_id}] Runtime error in forward_input_to_gemini task: {e}", exc_info=True)
    except Exception as e: logger.error(f"[{connection_id}] Error in forward_input_to_gemini task: {e}", exc_info=True)
    finally: logger.info(f"[{connection_id}] Forwarding input task finished.")


# Modified receive_from_gemini to handle playback
async def receive_from_gemini(
    session,
    pya_output_stream: Optional[pyaudio.Stream], # Pass the PyAudio stream
    connection_id: str,
    stop_event: asyncio.Event
):
    """Receives streaming responses from Gemini and plays audio on the server."""
    receive_count = 0
    loop = asyncio.get_running_loop() # Get event loop for executor

    try:
        while not stop_event.is_set():
            try:
                turn_generator = session.receive()
                async for response in turn_generator:
                    if stop_event.is_set(): break
                    receive_count += 1

                    if data := response.data:
                        logger.info(f"[{connection_id}] Received AUDIO data chunk {receive_count} ({len(data)} bytes) from Gemini.")
                        if pya_output_stream:
                            try:
                                # Write audio data in executor thread to avoid blocking asyncio loop
                                await loop.run_in_executor(executor, lambda: pya_output_stream.write(data))
                                logger.debug(f"[{connection_id}] Wrote audio chunk {receive_count} to PyAudio stream.")
                            except OSError as audio_err:
                                 logger.error(f"[{connection_id}] PyAudio write error: {audio_err}. Playback may stop.", exc_info=True)
                                 # Optionally set stop_event or close stream here on error
                                 # stop_event.set() # Example: stop everything on playback error
                            except Exception as write_err:
                                 logger.error(f"[{connection_id}] Unexpected error writing audio chunk {receive_count}: {write_err}", exc_info=True)
                        else:
                            logger.warning(f"[{connection_id}] Received audio data but PyAudio stream is not available. Skipping playback.")

                    elif text := response.text:
                        logger.info(f"[{connection_id}] Received TEXT response {receive_count} from Gemini: '{text}'")
                        # Can still handle text if needed, even if only audio requested
                        # TODO: Optional: forward text to client?

                    else:
                        logger.debug(f"[{connection_id}] Received other response part {receive_count} from Gemini: {response}")

                if stop_event.is_set(): break
                logger.info(f"[{connection_id}] Gemini turn completed.")

            except StopAsyncIteration:
                 logger.info(f"[{connection_id}] StopAsyncIteration received, Gemini session ending.")
                 stop_event.set()
                 break
            except asyncio.TimeoutError:
                 logger.debug(f"[{connection_id}] Receive timeout (should not happen).")
                 continue

    except (asyncio.CancelledError):
         logger.info(f"[{connection_id}] Receiving task cancelled.")
    except ConnectionClosedOK:
         logger.info(f"[{connection_id}] Gemini session closed gracefully during receive.")
    except ConnectionClosedError as close_err:
         logger.error(f"[{connection_id}] Gemini connection error during receive (Code: {close_err.code}).")
    except Exception as e:
        logger.error(f"[{connection_id}] Error in receive_from_gemini task: {e}", exc_info=True)
    finally:
        logger.info(f"[{connection_id}] Receiving task finished.")
        stop_event.set() # Ensure stop is signalled on any exit

# ==============================================================================
# WebSocket Endpoint (Manages Live Connect Session & Audio Playback)
# ==============================================================================
@app.websocket("/ws/stream")
async def websocket_endpoint(websocket: WebSocket):
    """Handles client WS, connects to Gemini Live API, manages playback."""
    await websocket.accept()
    client_host = websocket.client.host or "unknown"
    client_port = websocket.client.port or "unknown"
    connection_id = f"{client_host}:{client_port}-{os.urandom(4).hex()}"
    logger.info(f"WebSocket connection accepted from: {connection_id}")

    if not client or not LIVE_CONFIG:
        err_msg = "Gemini client or LiveConnectConfig not initialized."
        await websocket.close(code=1011, reason=f"Server config error: {err_msg}")
        return
    if not pya: # Check if PyAudio initialized
        err_msg = "PyAudio (for playback) not initialized."
        await websocket.close(code=1011, reason=f"Server config error: {err_msg}")
        return

    session: Optional[genai.LiveConnectSession] = None
    forward_task: Optional[asyncio.Task] = None
    receive_task: Optional[asyncio.Task] = None
    stop_event = asyncio.Event()
    pya_output_stream: Optional[pyaudio.Stream] = None # Define stream variable

    try:
        # --- Open PyAudio Output Stream ---
        try:
            pya_output_stream = pya.open(
                format=PYAUDIO_FORMAT,
                channels=PYAUDIO_CHANNELS,
                rate=PYAUDIO_OUTPUT_RATE,
                output=True
            )
            logger.info(f"[{connection_id}] PyAudio output stream opened (Rate: {PYAUDIO_OUTPUT_RATE}, Format: {PYAUDIO_FORMAT}, Channels: {PYAUDIO_CHANNELS}).")
        except Exception as stream_err:
             logger.error(f"[{connection_id}] Failed to open PyAudio output stream: {stream_err}", exc_info=True)
             await websocket.close(code=1011, reason="Server audio output error.")
             return # Cannot proceed without audio output

        # --- Establish Gemini Live Connect Session ---
        logger.info(f"[{connection_id}] Establishing Gemini Live Connect session (Model: {LIVE_API_MODEL})...")
        async with client.aio.live.connect(model=LIVE_API_MODEL, config=LIVE_CONFIG) as session:
            logger.info(f"[{connection_id}] Gemini Live Connect session established.")

            # --- Start Background Tasks ---
            forward_task = asyncio.create_task(
                forward_input_to_gemini(websocket, session, connection_id),
                name=f"forward_{connection_id}"
            )
            # Pass the output stream to the receive task
            receive_task = asyncio.create_task(
                receive_from_gemini(session, pya_output_stream, connection_id, stop_event),
                name=f"receive_{connection_id}"
            )

            # --- Wait for tasks ---
            done, pending = await asyncio.wait(
                [forward_task, receive_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            logger.info(f"[{connection_id}] First task completed. Done: {[t.get_name() for t in done]}. Pending: {[t.get_name() for t in pending]}.")

            # --- Signal stop and cancel pending ---
            stop_event.set()
            for task in pending:
                 logger.info(f"[{connection_id}] Cancelling pending task: {task.get_name()}")
                 task.cancel()
                 try: await task
                 except asyncio.CancelledError: logger.info(f"[{connection_id}] Task {task.get_name()} cancelled successfully.")
                 except Exception as e_cancel: logger.error(f"[{connection_id}] Error cancelling task {task.get_name()}: {e_cancel}")

    except ConnectionClosedError as close_err:
         logger.error(f"[{connection_id}] WebSocket connection to Gemini failed (Code: {close_err.code}).", exc_info=True)
         if websocket.client_state == fastapi.websockets.WebSocketState.CONNECTED:
             await websocket.close(code=1011, reason=f"Backend connection failed: {close_err.reason}")
    except google_exceptions.GoogleAPIError as api_err:
         logger.error(f"[{connection_id}] Gemini API Error during setup: {api_err}", exc_info=True)
         if websocket.client_state == fastapi.websockets.WebSocketState.CONNECTED:
            await websocket.close(code=1011, reason=f"Gemini API Error: {api_err.message}")
    except Exception as e:
        logger.error(f"[{connection_id}] Error during WebSocket session: {e}", exc_info=True)
        if websocket.client_state == fastapi.websockets.WebSocketState.CONNECTED:
             await websocket.close(code=1011, reason="Internal Server Error")
    finally:
        stop_event.set() # Ensure stop is signalled
        logger.info(f"[{connection_id}] Cleaning up session resources...")
        # --- Close PyAudio Stream ---
        if pya_output_stream:
            logger.info(f"[{connection_id}] Closing PyAudio output stream...")
            try:
                # Run stop/close in executor as they might block briefly
                await asyncio.get_running_loop().run_in_executor(executor, pya_output_stream.stop_stream)
                await asyncio.get_running_loop().run_in_executor(executor, pya_output_stream.close)
                logger.info(f"[{connection_id}] PyAudio stream closed.")
            except Exception as close_audio_err:
                 logger.error(f"[{connection_id}] Error closing PyAudio stream: {close_audio_err}")
        logger.info(f"[{connection_id}] WebSocket session cleanup complete.")


# ==============================================================================
# Root Endpoint & Server Runner
# ==============================================================================
@app.get("/")
async def read_root():
    return {"message": "Live Audio + Snapshot Server with Playback (Gemini Live API) is running"}

if __name__ == "__main__":
    print("Starting Live Audio + Snapshot Server with Playback...")
    if not pya:
         print("WARNING: PyAudio initialization failed. Server-side audio playback will be disabled.")
    if client and LIVE_CONFIG:
        print(f"Gemini Client Initialized. Target Model: {LIVE_API_MODEL} via API Version: {LIVE_API_VERSION}")
    else:
        print("Warning: Gemini Client or LiveConnectConfig failed to initialize.")

    # Note: PyAudio termination (pya.terminate()) should ideally happen on graceful server shutdown (e.g., via FastAPI lifespan events)
    # For simplicity here, we don't implement global termination. It might leave resources hanging slightly on abrupt server exit.


    # Note: PyAudio termination (pya.terminate()) should ideally happen on graceful server shutdown (e.g., via FastAPI lifespan events)
    # For simplicity here, we don't implement global termination. It might leave resources hanging slightly on abrupt server exit.
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True, log_level="info")



