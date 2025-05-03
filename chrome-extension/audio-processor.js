// audio-processor.js

// Check if running inside an AudioWorkletGlobalScope
if (typeof AudioWorkletProcessor === 'undefined') {
    console.error("audio-processor.js loaded outside AudioWorklet context?");
} else {

    class AudioProcessor extends AudioWorkletProcessor {
        constructor(options) {
            super(options);
            this.bufferSize = 4096; // Process in chunks
            this.buffer = new Int16Array(this.bufferSize);
            this.bufferIndex = 0;
            // Check sample rate if possible (global 'sampleRate' is defined by AudioWorkletGlobalScope)
            try {
                if (typeof sampleRate !== 'undefined' && sampleRate !== 16000) {
                    console.warn(`WORKLET/AUDIO-PROCESSOR: Running at ${sampleRate}Hz, expected 16000Hz.`);
                } else if (typeof sampleRate === 'undefined') {
                    console.warn("WORKLET/AUDIO-PROCESSOR: sampleRate global scope variable not found.");
                }
            } catch (e) { console.warn("WORKLET/AUDIO-PROCESSOR: Error accessing sampleRate:", e); }
            this.port.postMessage({ type: 'processor_ready' }); // Signal readiness
            console.log("WORKLET/AUDIO-PROCESSOR: Processor initialized.");
        }

        process(inputs, outputs, parameters) {
            const input = inputs[0]; // Get the first input (array of channels)
            // Expecting mono input based on getUserMedia constraints
            if (!input || input.length === 0 || !input[0]) {
                // No input data on the expected channel
                return true; // Keep processor alive
            }
            const inputChannelData = input[0]; // Float32Array data for the first channel

            // Convert Float32 [-1, 1] to Int16 [-32768, 32767] and buffer
            for (let i = 0; i < inputChannelData.length; i++) {
                const s = Math.max(-1, Math.min(1, inputChannelData[i]));
                // Correct scaling for Int16
                this.buffer[this.bufferIndex++] = s < 0 ? s * 32768 : s * 32767;

                // When buffer is full, send it
                if (this.bufferIndex === this.bufferSize) {
                    // Post a *copy* of the buffer's underlying ArrayBuffer
                    // slice(0) creates a copy - important as buffer will be reused
                    try {
                        this.port.postMessage(this.buffer.buffer.slice(0));
                    } catch (error) {
                         console.error("WORKLET/AUDIO-PROCESSOR: Error posting message:", error)
                         // If posting fails (e.g., ArrayBuffer not transferable or port closed),
                         // we might need error handling or stop processing.
                         // For now, just log and reset buffer.
                    }
                    this.bufferIndex = 0; // Reset buffer index
                }
            }

            // Keep processor alive
            return true;
        } // End process method
    } // End AudioProcessor class

    try {
         registerProcessor('audio-processor', AudioProcessor);
         // console.log("WORKLET/AUDIO-PROCESSOR: Processor registered."); // Can be noisy
    } catch (e) {
         console.error("WORKLET/AUDIO-PROCESSOR: Failed to register processor:", e)
    }

} // End scope check
