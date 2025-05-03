// main.js for File API approach

// --- Configuration ---
const FASTAPI_WS_URL = 'ws://127.0.0.1:8000/ws/stream'; // Backend WebSocket URL
const TIMESLICE_MS = 1000; // Send WebM chunks every second
const PREFERRED_MIMETYPE = 'video/webm; codecs=vp8,opus'; // Common A/V combo

// --- DOM Elements ---
const startButton = document.getElementById('startButton');
const stopButton = document.getElementById('stopButton');
const previewVideo = document.getElementById('previewVideo'); // Hidden video element
const statusDiv = document.getElementById('status');

// --- State Variables ---
let combinedStream = null; // Holds combined Audio+Video stream
let mediaRecorder = null;
let webSocket = null;
let recordedChunks = []; // Temporarily store chunks if needed (optional)

// --- Helper Functions ---
function updateStatus(message, type = 'info') {
    if(statusDiv) {
        statusDiv.textContent = `Status: ${message}`;
        statusDiv.className = type;
    }
    console.log(`Status Update (${type}): ${message}`);
}

// --- Core Logic ---
async function startRecording() {
    console.log("Attempting to start recording...");
    if (mediaRecorder?.state === 'recording' || webSocket?.readyState === WebSocket.OPEN) {
        console.warn("Already recording or connected.");
        return;
    }

    startButton.disabled = true;
    stopButton.disabled = false;
    updateStatus('Requesting permissions...', 'info');

    try {
        // 1. Get Audio Stream
        const audioStream = await navigator.mediaDevices.getUserMedia({
            audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
            video: false
        });
        console.log("Mic OK.");

        // 2. Get Video Stream (Screen)
        const videoStream = await navigator.mediaDevices.getDisplayMedia({
            video: { mediaSource: "screen", frameRate: 15 },
            audio: false // Get audio only from getUserMedia
        });
        console.log("Screen OK.");

        // Stop stream immediately if user stops sharing via browser UI
        videoStream.getVideoTracks()[0].onended = () => {
             console.log("Screen sharing stopped by user.");
             stopRecording();
         };

        // 3. Combine Streams
        const audioTracks = audioStream.getAudioTracks();
        const videoTracks = videoStream.getVideoTracks();
        if (audioTracks.length === 0 || videoTracks.length === 0) {
            throw new Error("Failed to get both audio and video tracks.");
        }
        // Create a new stream containing tracks from both
        combinedStream = new MediaStream([...videoTracks, ...audioTracks]);

        // Optional: show preview in hidden video element
        previewVideo.srcObject = videoStream; // Show only video for preview simplicity
        previewVideo.play().catch(e => console.warn("Preview play failed:", e));

        // 4. Connect WebSocket
        webSocket = new WebSocket(FASTAPI_WS_URL);
        webSocket.binaryType = "arraybuffer"; // Server expects bytes

        webSocket.onopen = () => {
            console.log("WebSocket open.");
            updateStatus('Connected. Recording...', 'success');
            startMediaRecorder(); // Start recording AFTER WS is open
        };

        webSocket.onerror = (event) => { console.error('WS error:', event); updateStatus('WS error.', 'error'); stopRecording(); };
        webSocket.onclose = (event) => { console.log(`WS closed: ${event.code}`); if (statusDiv.textContent !== 'Status: Idle') updateStatus('Stopped (WS closed).', 'info'); stopRecording(false); }; // Don't signal stop to server if already closed

    } catch (err) {
        console.error("Setup error:", err);
        let msg = `Error: ${err.name || 'Unknown Error'}.`;
        if (err.name === "NotAllowedError") {
            msg = "Error: Permission denied. Check OS/Browser settings for Mic AND Screen Recording!";
        }
        updateStatus(msg, 'error');
        stopRecording(false); // Ensure cleanup on error
        startButton.disabled = false; // Re-enable start on failure
        stopButton.disabled = true;
    }
}

function startMediaRecorder() {
    if (!combinedStream || !webSocket || webSocket.readyState !== WebSocket.OPEN) {
        console.error("Cannot start MediaRecorder: stream or WS not ready.");
        updateStatus('Recorder setup failed.', 'error');
        return;
    }

    let options = { mimeType: PREFERRED_MIMETYPE };
    if (!MediaRecorder.isTypeSupported(options.mimeType)) {
        console.warn(`MimeType ${options.mimeType} not supported. Trying default.`);
        options = {}; // Use browser default
    }

    try {
        mediaRecorder = new MediaRecorder(combinedStream, options);
        console.log(`Using MediaRecorder mimeType: ${mediaRecorder.mimeType}`);

        mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0 && webSocket && webSocket.readyState === WebSocket.OPEN) {
                console.debug(`Sending WebM chunk: ${event.data.size} bytes`);
                webSocket.send(event.data); // Send chunk directly
                // recordedChunks.push(event.data); // Only needed if processing locally later
            }
        };

        mediaRecorder.onstop = () => {
            console.log("MediaRecorder stopped.");
            // Send stop signal to server if WebSocket is still open
            if (webSocket && webSocket.readyState === WebSocket.OPEN) {
                 try {
                      webSocket.send(JSON.stringify({ type: "control", action: "stop" }));
                      console.log("Sent stop signal to server.");
                 } catch (e) { console.error("Error sending stop signal:", e); }
            }
            // No further local processing needed here now
            // recordedChunks = [];
        };

        mediaRecorder.onerror = (event) => {
             console.error("MediaRecorder error:", event.error);
             updateStatus(`Recorder Error: ${event.error.name}`, 'error');
             stopRecording(); // Stop everything on recorder error
        };

        // Start recording, sending data periodically
        mediaRecorder.start(TIMESLICE_MS);
        console.log("MediaRecorder started.");

    } catch (err) {
        console.error("Failed to create MediaRecorder:", err);
        updateStatus(`Recorder init failed: ${err.message}`, 'error');
        stopRecording(false);
    }
}

function stopRecording(sendSignal = true) {
    console.log("Stopping recording...");
    // Stop recorder first (triggers onstop)
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
    }
    mediaRecorder = null;

    // Stop media stream tracks
    combinedStream?.getTracks().forEach(track => track.stop());
    combinedStream = null;
    previewVideo.srcObject = null;

    // Close WebSocket (if not already closing and signal not needed)
    if (webSocket && webSocket.readyState === WebSocket.OPEN && !sendSignal) {
         webSocket.close(1000, "Client stopped");
    } else if (webSocket && webSocket.readyState === WebSocket.CONNECTING) {
         webSocket.close(1000, "Client aborted"); // Abort if still connecting
    }
    // webSocket = null; // Let onclose handler manage final state and UI

    // Update UI (Onclose will set final Idle state)
    if(startButton) startButton.disabled = false;
    if(stopButton) stopButton.disabled = true;
    if (statusDiv.textContent !== 'Status: Idle' && statusDiv.textContent !== 'Status: Stopped (WS closed).') {
        updateStatus("Stopping...", "info");
    }
    console.log("Local stop actions complete.");
}

// --- Event Listeners ---
if (startButton) startButton.addEventListener('click', startRecording);
if (stopButton) stopButton.addEventListener('click', () => stopRecording(true)); // Pass true to send stop signal via recorder onstop

// --- Initial Check ---
window.addEventListener('load', () => {
     let supported = true; let errorMsg = "";
     if (!navigator.mediaDevices?.getUserMedia) { errorMsg = 'getUserMedia unsupported.'; supported = false; }
     else if (!navigator.mediaDevices?.getDisplayMedia) { errorMsg = 'getDisplayMedia unsupported.'; supported = false; }
     else if (!window.MediaRecorder) { errorMsg = 'MediaRecorder unsupported.'; supported = false; }
     else if (!window.WebSocket) { errorMsg = 'WebSocket unsupported.'; supported = false; }
     if (!supported) { updateStatus(`Error: ${errorMsg}`, 'error'); if(startButton) startButton.disabled = true; if(stopButton) stopButton.disabled = true; }
     else { console.log("Browser supports necessary APIs."); }
});
