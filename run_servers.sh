#!/bin/bash

# Define colors for console output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}Starting Bunq Hackathon Challenge Servers...${NC}"

# Function to handle cleanup when script is terminated
cleanup() {
  echo -e "${GREEN}Stopping all servers...${NC}"
  kill $HTTP_PID $STREAM_PID 2>/dev/null
  exit 0
}

# Set trap for script termination
trap cleanup SIGINT SIGTERM

# Project directories
PROJECT_ROOT="$(pwd)"
EXTENSION_DIR="${PROJECT_ROOT}/chrome-extension"
STREAMING_DIR="${PROJECT_ROOT}/streamig-server"
STREAM_SERVER="${STREAMING_DIR}/src/main.py"

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
  echo -e "${RED}Python 3 is not installed. Please install Python 3 and try again.${NC}"
  exit 1
fi

# Setup streaming server virtual environment if it doesn't exist
if [ ! -d "${STREAMING_DIR}/venv" ]; then
  echo -e "${YELLOW}Setting up virtual environment for streaming server...${NC}"
  cd "${STREAMING_DIR}"
  python3 -m venv venv
  source venv/bin/activate
  echo -e "${YELLOW}Installing streaming server dependencies...${NC}"
  pip install -r requirements.txt
  deactivate
  cd "${PROJECT_ROOT}"
else
  echo -e "${BLUE}Using existing virtual environment for streaming server.${NC}"
fi

# Setup an extension server virtual environment if it doesn't exist
if [ ! -d "${PROJECT_ROOT}/venv" ]; then
  echo -e "${YELLOW}Setting up virtual environment for HTTP server...${NC}"
  cd "${PROJECT_ROOT}"
  python3 -m venv venv
  cd "${PROJECT_ROOT}"
else
  echo -e "${BLUE}Using existing virtual environment for HTTP server.${NC}"
fi

# Start HTTP server for Chrome extension using Python from venv
echo -e "${BLUE}Starting HTTP server for Chrome extension on port 8080...${NC}"
cd "${EXTENSION_DIR}"
source "${PROJECT_ROOT}/venv/bin/activate"
python -m http.server 8080 &
HTTP_PID=$!
deactivate

# Start streaming server using the specific venv
echo -e "${BLUE}Starting Gemini Streaming server on port 8000...${NC}"
cd "${STREAMING_DIR}"
source "${STREAMING_DIR}/venv/bin/activate"
python src/main.py &
STREAM_PID=$!
deactivate

cd "${PROJECT_ROOT}"

echo -e "${GREEN}All servers started. Press Ctrl+C to stop all servers.${NC}"
echo -e "${BLUE}Chrome extension server running at: ${NC}http://localhost:8080"
echo -e "${BLUE}Streaming server running at: ${NC}http://localhost:8000"

# Wait for both processes - this keeps the script running
wait $HTTP_PID $STREAM_PID