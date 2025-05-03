"""
## Documentation
Quickstart: https://github.com/google-gemini/cookbook/blob/main/quickstarts/Get_started_LiveAPI.py

## Setup

To install the dependencies for this script, run:

```
pip install google-genai opencv-python pyaudio pillow mss
```
"""

import os
import asyncio
import base64
import io
import traceback

import cv2
import pyaudio
import PIL.Image
import mss

import argparse

from google import genai
from google.genai import types

FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024

# Add Google Search API constants
# Replace with your actual API key or use environment variables
GOOGLE_SEARCH_API_KEY = os.environ.get("GOOGLE_SEARCH_API_KEY", "AIzaSyB7JSR-1Y5ycRDrBqzVTLQmQC6g-PKZjBw")
GOOGLE_SEARCH_ENGINE_ID = os.environ.get("GOOGLE_SEARCH_ENGINE_ID", "0018ac0bdf4c44bd0")  # You'll get this from the steps below
GOOGLE_SEARCH_BASE_URL = "https://www.googleapis.com/customsearch/v1"

MODEL = "models/gemini-2.0-flash-live-001"

DEFAULT_MODE = "screen"

client = genai.Client(
    http_options={"api_version": "v1beta"},
    api_key=os.environ.get("GEMINI_API_KEY"),
)

tools = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="get_bunq_how_to_steps",
                description="Get Bunq account how to steps from bunq.com, that give you step by step instructions.",
                parameters=genai.types.Schema(
                    type = genai.types.Type.OBJECT,
                    properties = {
                        "query": genai.types.Schema(
                            type = genai.types.Type.STRING,
                            description = "The query to search for on bunq.com."
                        ),
                    },
                ),
            ),
        ]
    ),
]

# While Gemini 2.0 Flash is in experimental preview mode, only one of AUDIO or
# TEXT may be passed here.
CONFIG = types.LiveConnectConfig(
    response_modalities=[
        "AUDIO",
    ],
    speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck")
        )
    ),
    tools=tools,
)

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
                
                # Remove script and style elements
                for script in soup(["script", "style"]):
                    script.extract()
                
                # Get text content
                text = soup.get_text(separator=' ', strip=True)
                
                # Clean text (remove excessive whitespace)
                text = ' '.join(text.split())
                
                print(f"Retrieved content from {url}")
                print(f"Title: {metadata['title']}")
                print(f"Snippet: {metadata['snippet']}")
                print(f"Content: {text[:500]}...")  # Print first 500 characters of content  

                return {
                    "metadata": metadata,
                    "content": text[:5000],  # Limit content length to prevent very large responses
                    "all_results": search_data["items"][:5]  # Include top 5 results for reference
                }
            except requests.RequestException as e:
                return {
                    "metadata": metadata,
                    "error": f"Error retrieving content: {str(e)}",
                    "all_results": search_data["items"][:5]
                }
        else:
            return {"error": "No URL found in search results"}
            
    except requests.RequestException as e:
        print(f"Google Search API error: {e}")
        return {"error": str(e)}

# Function handler for get_bunq_how_to_steps
def get_bunq_how_to_steps(query):
    """
    Searches for Bunq how-to steps on together.bunq.com using Google Search API
    and retrieves the content from the first result.
    """
    search_result = google_search(query, "together.bunq.com")
    
    if "error" in search_result:
        return {"results": f"Error: {search_result['error']}"}
    
    return {"results": search_result}

pya = pyaudio.PyAudio()


class AudioLoop:
    def __init__(self, video_mode=DEFAULT_MODE):
        self.video_mode = video_mode

        self.audio_in_queue = None
        self.out_queue = None

        self.session = None

        self.send_text_task = None
        self.receive_audio_task = None
        self.play_audio_task = None

        self.function_handlers = {
            "get_bunq_how_to_steps": get_bunq_how_to_steps
        }

    async def send_text(self):
        while True:
            text = await asyncio.to_thread(
                input,
                "message > ",
            )
            if text.lower() == "q":
                break
            await self.session.send(input=text or ".", end_of_turn=True)

    def _get_frame(self, cap):
        # Read the frameq
        ret, frame = cap.read()
        # Check if the frame was read successfully
        if not ret:
            return None
        # Fix: Convert BGR to RGB color space
        # OpenCV captures in BGR but PIL expects RGB format
        # This prevents the blue tint in the video feed
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = PIL.Image.fromarray(frame_rgb)  # Now using RGB frame
        img.thumbnail([1024, 1024])

        image_io = io.BytesIO()
        img.save(image_io, format="jpeg")
        image_io.seek(0)

        mime_type = "image/jpeg"
        image_bytes = image_io.read()
        return {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode()}

    async def get_frames(self):
        # This takes about a second, and will block the whole program
        # causing the audio pipeline to overflow if you don't to_thread it.
        cap = await asyncio.to_thread(
            cv2.VideoCapture, 0
        )  # 0 represents the default camera

        while True:
            frame = await asyncio.to_thread(self._get_frame, cap)
            if frame is None:
                break

            await asyncio.sleep(1.0)

            await self.out_queue.put(frame)

        # Release the VideoCapture object
        cap.release()

    def _get_screen(self):
        sct = mss.mss()
        monitor = sct.monitors[0]

        i = sct.grab(monitor)

        mime_type = "image/jpeg"
        image_bytes = mss.tools.to_png(i.rgb, i.size)
        img = PIL.Image.open(io.BytesIO(image_bytes))

        image_io = io.BytesIO()
        img.save(image_io, format="jpeg")
        image_io.seek(0)

        image_bytes = image_io.read()
        return {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode()}

    async def get_screen(self):

        while True:
            frame = await asyncio.to_thread(self._get_screen)
            if frame is None:
                break

            await asyncio.sleep(1.0)

            await self.out_queue.put(frame)

    async def send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send(input=msg)

    async def listen_audio(self):
        mic_info = pya.get_default_input_device_info()
        self.audio_stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            input=True,
            input_device_index=mic_info["index"],
            frames_per_buffer=CHUNK_SIZE,
        )
        if __debug__:
            kwargs = {"exception_on_overflow": False}
        else:
            kwargs = {}
        while True:
            data = await asyncio.to_thread(self.audio_stream.read, CHUNK_SIZE, **kwargs)
            await self.out_queue.put({"data": data, "mime_type": "audio/pcm"})

    async def receive_audio(self):
        "Background task to reads from the websocket and write pcm chunks to the output queue"
        self.is_speaking = False
        self.audio_buffer = []
        
        while True:
            turn = self.session.receive()
            
            # Collect all audio chunks for this turn
            collected_audio = []
            collected_text = ""
            
            # First collect everything from this turn
            async for response in turn:
                if data := response.data:
                    collected_audio.append(data)
                if text := response.text:
                    collected_text += text
            
            # Output the text all at once
            if collected_text:
                print(collected_text, end="")
            
            # Only when we've collected all audio chunks, send them to the queue
            for audio_chunk in collected_audio:
                self.audio_in_queue.put_nowait(audio_chunk)
                
            # If there was no audio in this turn, clear the queue
            if not collected_audio:
                while not self.audio_in_queue.empty():
                    self.audio_in_queue.get_nowait()

    async def play_audio(self):
        stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=RECEIVE_SAMPLE_RATE,
            output=True,
        )
        while True:
            bytestream = await self.audio_in_queue.get()
            await asyncio.to_thread(stream.write, bytestream)

    async def run(self):
        try:
            async with (
                client.aio.live.connect(model=MODEL, config=CONFIG) as session,
                asyncio.TaskGroup() as tg,
            ):
                self.session = session

                self.audio_in_queue = asyncio.Queue()
                self.out_queue = asyncio.Queue(maxsize=5)

                send_text_task = tg.create_task(self.send_text())
                tg.create_task(self.send_realtime())
                tg.create_task(self.listen_audio())
                if self.video_mode == "camera":
                    tg.create_task(self.get_frames())
                elif self.video_mode == "screen":
                    tg.create_task(self.get_screen())

                tg.create_task(self.receive_audio())
                tg.create_task(self.play_audio())

                await send_text_task
                raise asyncio.CancelledError("User requested exit")

        except asyncio.CancelledError:
            pass
        except ExceptionGroup as EG:
            self.audio_stream.close()
            traceback.print_exception(EG)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        type=str,
        default=DEFAULT_MODE,
        help="pixels to stream from",
        choices=["camera", "screen", "none"],
    )
    args = parser.parse_args()
    main = AudioLoop(video_mode=args.mode)
    asyncio.run(main.run())
