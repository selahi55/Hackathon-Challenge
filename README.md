# Bunq Hackathon Challenge

A Chrome extension with a Python backend that integrates with Google's Gemini AI to provide real-time assistance for Bunq users.

## Project Overview

This project consists of two main components:

1. **Chrome Extension**: A browser extension that captures audio input and screenshots from the Bunq web interface and sends them to the backend server.

2. **Streaming Server**: A Python-based backend that processes the inputs using Google's Gemini AI and returns relevant responses, including both text and audio.

## Features

- Real-time audio processing and transcription
- Screenshot analysis using Gemini vision capabilities
- Integration with Bunq's knowledge base through Google Search API
- Text-to-speech responses for a conversational experience
- Cross-platform support (Windows/macOS/Linux)

## Prerequisites

Before you start, ensure you have the following installed:

- Python 3.10 or higher
- Google Chrome browser
- Git (for cloning the repository)
- API Keys:
  - Google API Key with access to Gemini API
  - Google Search API Key
  - Google Search Engine ID

## Project Structure

```
├── chrome-extension/           # Chrome extension files
│   ├── audio-processor.js      # Audio processing code
│   ├── main.html               # Main extension UI
│   ├── main.js                 # Extension logic
│   ├── manifest.json           # Extension manifest
│   ├── text.html               # Text interface
│   └── images/                 # Extension icons
├── streamig-server/            # Backend server
│   ├── src/                    # Source code
│   │   ├── main.py            # Main server application
│   │   └── temp.py            # Utility functions
│   ├── requirements.txt        # Python dependencies
│   └── README.md              # Server-specific documentation
├── run_servers.sh              # Script to start servers on macOS/Linux
├── run_servers.bat             # Script to start servers on Windows
└── README.md                   # This file
```

## Installation

### 1. Clone the repository

```bash
git clone <repository-url>
cd Hackathon-Challenge
```

### 2. Set up environment variables

Create a `.env` file in the `streamig-server` directory with the following variables:

```
GOOGLE_API_KEY=your_google_api_key_here
GOOGLE_SEARCH_API_KEY=your_google_search_api_key_here
GOOGLE_SEARCH_ENGINE_ID=your_google_search_engine_id_here
```

### 3. Install the Chrome Extension

1. Open Chrome and navigate to `chrome://extensions/`
2. Enable "Developer mode" in the top right corner
3. Click "Load unpacked" and select the `chrome-extension` directory from this project
4. The extension should now appear in your extensions list and in your toolbar

## Running the Application

### Option 1: Using the provided scripts

For macOS/Linux:
```bash
chmod +x run_servers.sh
./run_servers.sh
```

For Windows:
```batch
run_servers.bat
```

This will:
- Create and activate virtual environments
- Install all required Python dependencies
- Start an HTTP server for the Chrome extension on port 8080
- Start the streaming server on port 8000

### Option 2: Manual setup

If you prefer to set up the environment manually:

1. Set up the streaming server:
```bash
cd streamig-server
python3 -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate
pip install -r requirements.txt
python src/main.py
```

2. In a separate terminal, serve the Chrome extension:
```bash
cd chrome-extension
python -m http.server 8080
```

## Usage

1. Make sure both servers are running
2. Navigate to the Bunq website in Chrome
3. Click the extension icon in your browser toolbar
4. Allow microphone access when prompted
5. Speak your query related to Bunq services
6. The extension will display the response and play it back as audio

## Troubleshooting

- **Extension not connecting to server**: Ensure both servers are running and check console logs for errors
- **No audio input/output**: Check browser microphone permissions and speaker settings
- **"API key not valid" errors**: Verify your API keys in the `.env` file are correct and have the necessary permissions
- **Performance issues**: Consider closing other applications that might be using substantial system resources

## Development

To modify the project:

- Chrome Extension: Edit files in the `chrome-extension` directory
- Backend Server: Modify Python code in the `streamig-server/src` directory

After making changes to the extension, reload it in `chrome://extensions/`

## License

[Specify your license here]

## Acknowledgements

- Google Gemini API
- Bunq Knowledge Base
- FastAPI and Uvicorn for the backend server
- WebSocket protocol for real-time communication