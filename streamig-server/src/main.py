import fastapi
import uvicorn
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware # Still potentially needed for HTTP upgrade
from pathlib import Path
import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# --- Configure CORS ---
# Even though WebSockets aren't directly subject to CORS after connection,
# the initial HTTP request that upgrades the connection *is*.
# So, CORS middleware is often still necessary.
origins = [
    "http://localhost",
    "http://localhost:8080",
    "http://127.0.0.1",
    "http://127.0.0.1:8080",
    "null", # Origin for file:// URLs
    # Add your frontend origin if deployed
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Define Storage (Example: Save to a file per connection) ---
STREAM_DIRECTORY = Path("./live_streams")
STREAM_DIRECTORY.mkdir(parents=True, exist_ok=True)


@app.websocket("/ws/stream")
async def websocket_endpoint(websocket: WebSocket):
    """
    Handles WebSocket connections for live screen streaming.
    Receives binary video chunks and saves them to a file.
    """
    await websocket.accept()
    logger.info(f"WebSocket connection accepted from: {websocket.client.host}:{websocket.client.port}")

    # Generate a unique filename for this stream session
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    # Could use websocket.client.host for identification if needed, careful with privacy.
    # Or require an ID passed during connection setup (more advanced).
    filename = f"stream_{timestamp}.webm" # Assuming webm based on typical client settings
    save_path = STREAM_DIRECTORY / filename
    file_writer = None

    try:
        # Open the file to write the stream data
        # Use 'ab' mode (append binary) if you might reconnect and append,
        # but 'wb' (write binary) is simpler for a single session.
        file_writer = open(save_path, "wb")
        logger.info(f"Opened file for writing: {save_path}")

        while True:
            # Receive data - expecting binary data (video chunks)
            data = await websocket.receive_bytes()
            logger.debug(f"Received chunk size: {len(data)} bytes") # Use debug level for frequent messages

            # Write the received chunk to the file
            file_writer.write(data)

            # Optional: Send acknowledgement back to client
            # await websocket.send_text(f"Received {len(data)} bytes")

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {websocket.client.host}:{websocket.client.port} (Code: {websocket.client_state.value})") # Need to investigate client_state

    except Exception as e:
        logger.error(f"Error during WebSocket communication: {e}")
        # Attempt to notify the client before closing if possible
        try:
            await websocket.close(code=1011, reason=f"Server error: {e}") # 1011: Internal Server Error
        except Exception:
            pass # Ignore errors during close after another error occurred

    finally:
        # Clean up: Close the file
        if file_writer:
            file_writer.close()
            logger.info(f"Closed file: {save_path}")
        logger.info(f"Finished handling connection for {websocket.client.host}:{websocket.client.port}")


@app.get("/")
async def read_root():
    return {"message": "Screen stream server is running (WebSocket at /ws/stream)"}

# --- Run the server ---
if __name__ == "__main__":
    # Use reload=True for development convenience
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
