import sys
from pprint import pprint

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
import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from google.api_core import exceptions as google_exceptions
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup

# ==============================================================================
# Configuration & Constants
# ==============================================================================
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY",
                           "AIzaSyB7JSR-1Y5ycRDrBqzVTLQmQC6g-PKZjBw")  # Replace with your actual API key
# Add Google Search API constants
# Replace with your actual API key or use environment variables
GOOGLE_SEARCH_API_KEY = os.environ.get("GOOGLE_SEARCH_API_KEY", "AIzaSyB7JSR-1Y5ycRDrBqzVTLQmQC6g-PKZjBw")
GOOGLE_SEARCH_ENGINE_ID = os.environ.get("GOOGLE_SEARCH_ENGINE_ID",
                                         "0018ac0bdf4c44bd0")  # You'll get this from the steps below
GOOGLE_SEARCH_BASE_URL = "https://www.googleapis.com/customsearch/v1"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

LIVE_API_MODEL = "models/gemini-2.0-flash-live-001"
LIVE_API_VERSION = "v1beta"
AUDIO_SAMPLE_RATE = 16000  # Input sample rate from client

# --- Google Search API Configuration ---    
tools = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="get_bunq_how_to_steps",
                description="Get Bunq account how to steps from bunq.com, that give you step by step instructions.",
                parameters=genai.types.Schema(
                    type=genai.types.Type.OBJECT,
                    properties={
                        "query": genai.types.Schema(
                            type=genai.types.Type.STRING,
                            description="The query to search for on bunq.com."
                        ),
                    },
                ),
            ),
        ]
    ),
]

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
        temperature=0,
        system_instruction="""
You are Bunq Assist AI, a professional and precise Customer Support Engineer focused solely on helping users of Bunq — the mobile-first European neobank.

== CORE OPERATING PRINCIPLE ==

You have two intelligent sources of truth:

1. ✅ The **user’s current screen** inside the Bunq app — always visible to you  
2. 🤖 The **BunqMate Knowledge System** — an AI-powered support brain that gives verified information about Bunq’s features, policies, and steps, accessed via:  
   ➡️ `get_bunq_how_to_steps(query: string)`

You must balance both sources wisely.

→ If the screen gives a clear answer, rely on it.  
→ If more detail, precision, or backend guidance is needed, query BunqMate.

The most effective responses often combine what you see on the screen with insights from BunqMate.

== WORKFLOW (SMART ASSISTANT LOGIC) ==

1. **Understand the user's intent and screen view**
   - Focus on the meaningful content of the query.
   - Remove irrelevant characters: ignore emojis, special symbols, HTML/XML tags.

2. **Screen-first logic**
   - If the user’s screen provides a direct answer (e.g., button, label, message, menu path):
     ✅ Respond based on screen context with precise and visually-guided steps.

3. **When screen lacks full clarity**
   - 🔄 Query BunqMate: `get_bunq_how_to_steps(query="...")`
   - 📘 Review the result intelligently.
     - If helpful, synthesize it with what you see on screen.
     - If not relevant, fall back to general interface knowledge if possible.

4. **If both the screen and BunqMate return no helpful guidance:**
   ❌ Say:  
   **"I’m sorry, I couldn’t find the requested information in the BunqMate Knowledge System or on your current screen."**

== SCREEN + BUNQMATE BALANCE ==

Use the screen for:
- Interface walkthroughs
- Real-time problem solving
- Detecting misnavigation or incorrect context

Use BunqMate for:
- Complex procedures (e.g., setting limits, recovering access)
- Definitions, limits, feature explanations
- Background rules not always visible in the app

Blend both when possible.

== ABOUT BUNQ ==

Bunq is a fully licensed European neobank offering:
- Personal and Business accounts
- Multi-currency sub-accounts (Bank Accounts & Joint Accounts)
- Smart budgeting, saving, and environmental tools
- Real-time app-based notifications and international payment options

All interactions are designed for mobile-first use.

== OFF-TOPIC HANDLING ==

If a user asks a non-Bunq question (e.g., sports, weather, general trivia):

1. Say:  
   **"My purpose is to assist with Bunq banking questions."**

2. Follow with a gentle, cheeky nudge:  
   **"I'm afraid that's a bit outside my designated operational parameters. Are we perhaps procrastinating on sorting out our finances today? 😉 Let's focus back on Bunq."**

Then immediately redirect to Bunq support.

== NON-NEGOTIABLE GUIDELINES ==

- ✅ Prioritize screen context when clear
- ✅ Query BunqMate for backend or policy-rich queries
- 🧹 Clean queries of emojis, tags, and symbols before processing
- ❌ Never make up answers
- ❌ Never give financial advice
- ❌ Never assist with non-Bunq topics

You are Bunq Assist AI — a visually smart, BunqMate-powered, precision support specialist.


        """,
        tools=tools,
    )
except Exception as e:
    logger.error(f"Failed to create LiveConnectConfig: {e}. Disabling LiveConnect.", exc_info=True)
    LIVE_CONFIG = None

ALLOWED_ORIGINS = ["*"]
MAX_CONCURRENT_SESSIONS = 10

# ==============================================================================
# Initialization
# ==============================================================================
assert GOOGLE_API_KEY, "GOOGLE_API_KEY environment variable not set."
assert GOOGLE_SEARCH_API_KEY, "GOOGLE_SEARCH_API_KEY environment variable not set."
assert GOOGLE_SEARCH_ENGINE_ID, "GOOGLE_SEARCH_ENGINE_ID environment variable not set."
assert GOOGLE_SEARCH_BASE_URL, "GOOGLE_SEARCH_BASE_URL environment variable not set."

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
# Google Search API Configuration
# ==============================================================================


# Function to perform Google search and retrieve content
def google_search(query, site_restriction=None):
    """
    Performs a Google search with the specified query and retrieves content from the first result.
    If site_restriction is provided, the search is limited to that site.
    
    Returns:
        A dictionary containing the search result and the page content
    """
    search_query = query
    if site_restriction:
        search_query = f"site:{site_restriction} {query}"

    params = {
        'key': GOOGLE_SEARCH_API_KEY,
        'cx': GOOGLE_SEARCH_ENGINE_ID,
        'q': search_query
    }

    try:
        # Search for results
        search_response = requests.get(GOOGLE_SEARCH_BASE_URL, params=params)
        search_response.raise_for_status()
        search_data = search_response.json()

        # Check if we have search results
        if "items" not in search_data or not search_data["items"]:
            return {"error": "No search results found"}

        # Get the first result URL
        first_result = search_data["items"][0]
        url = first_result.get("link")

        # Get metadata from the search result
        metadata = {
            "title": first_result.get("title", "No title"),
            "snippet": first_result.get("snippet", "No description"),
            "url": url
        }

        # Retrieve the content from the URL
        if url:
            try:
                content_response = requests.get(url)
                content_response.raise_for_status()

                # Parse HTML content
                soup = BeautifulSoup(content_response.text, 'html.parser')

                # Extract content under the div with class 'Post-body'
                post_body_div = soup.find('div', class_='Post-body')
                text = post_body_div.get_text(separator=' ', strip=True) if post_body_div else "No content found"

                # Clean text (remove excessive whitespace)
                text = ' '.join(text.split())

                logging.info(f"Retrieved content from {url}")
                logging.info(f"Title: {metadata['title']}")
                logging.info(f"Snippet: {metadata['snippet']}")
                logging.info(f"Content: {text}")  # Print first 500 characters of content

                return {
                    "metadata": "",
                    "content": text[:5000],  # Limit content length to prevent very large responses
                    "all_results": []  # Include top 5 results for reference
                }
            except requests.RequestException as e:
                logging.error("here we are getting an error")
                return {
                    "metadata": "",
                    "error": f"something went wrong, please try again later",
                    "all_results": []
                }
        else:
            return {"error": "No data found in search results"}

    except requests.RequestException as e:
        print(f"Google Search API error: {e}")
        return {"error": str(e)}




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
        session,
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
                    logging.info('processing response')
                    if stop_event.is_set(): break
                    logging.info('stop event is not set')

                    receive_count += 1
                    # logging.info(response)
                    if hasattr(response, 'tool_call') and response.tool_call:
                        logger.info('Function has been called')
                        function_call = response.tool_call.function_calls[0]
                        logger.info(f"Function call: {function_call}")

                        function_name = response.tool_call.function_calls[0].name
                        args = response.tool_call.function_calls[0].args
                        if function_name == "get_bunq_how_to_steps":
                            # Execute the function with the provided arguments
                            logger.info(f"USED FUNCTION CALL {function_name}, result ")
                            search_result = google_search(args['query'], 'together.bunq.com "knowledge" "legend"')

                            if "error" in search_result:
                                return {"results": f"Error: {search_result['error']}"}
                            # Return the results to the model
                            logger.info(search_result['content'])
                            await session.send(input=search_result['content'])

                            logger.info(f"[{connection_id}] Sent FunctionResponse for {function_name} back to Gemini.")

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
