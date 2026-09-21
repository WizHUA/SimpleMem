"""Run the local development API on loopback."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("agent_memory.api:create_app", factory=True, host="127.0.0.1", port=8088)
