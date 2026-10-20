import uvicorn

from incubrix.config import Settings

if __name__ == "__main__":
    config = Settings.from_env()
    uvicorn.run("incubrix.api:app", host=config.api_host, port=config.api_port, reload=False)
