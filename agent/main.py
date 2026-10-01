"""Safe local AFF hybrid entrypoint; no legacy worker, SDK or provider imports."""
from agent.hybrid.api import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=app.state.settings.port, reload=False)
