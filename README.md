# Novel Engine

A RAG-based novel engine with Knowledge Graph and Story Planning capabilities.

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Set up environment variables:
   Copy `.env.example` to `.env` (if applicable) and add your API keys.

3. Run the server:
   ```bash
   uvicorn app.main:app --reload
   ```
