# AI use disclosure

This application was authored in this session with an AI assistant using Copilot SDK in VS Code. The assistant helped draft and integrate Python modules, tests, and documentation from the project requirements. No other coding assistant or runtime model was used to generate the implementation.

At runtime, the application can send user narration and style/avoid settings to the configured Groq account for scene analysis. Uploaded audio is sent to Groq for Whisper transcription. Those runtime AI calls are optional for script analysis but required for uploaded-audio transcription. Review Groq's current data handling terms before sending sensitive content.

The renderer does not use AI-generated footage. Footage comes from configured providers or user-supplied clips. Deterministic fallback scene descriptions and token-overlap relevance scores are heuristics and are labeled as such.
