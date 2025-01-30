import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request, WebSocket
from starlette.testclient import TestClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.uid = uuid.uuid4()
    print(f"App started with UID: {app.state.uid}")
    yield
    print(f"App stopped with UID: {app.state.uid}")


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)

    @app.get("/uid")
    def get_uid(request: Request):
        print(f"Request headers: {request.headers}")
        return {"uid": str(app.state.uid)}

    @app.get("/error")
    def get_error(request: Request):
        print(f"Request headers: {request.headers}")
        raise ValueError(f"Something went wrong for user {request.headers['user']}")

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        data = await websocket.receive_text()
        print(f"Got message: {data}")
        await websocket.send_text(f"Message text was: {data}")

    return app


class TestServer:
    def __init__(self):
        self.app = create_app()
        self.server = TestClient(self.app)
        self.clients: list[httpx.Client] = []
        self._is_running = False

    def start(self) -> None:
        self.server.__enter__()
        self._is_running = True

    def launch_client(self, name: str) -> httpx.Client:
        if not self._is_running:
            raise RuntimeError("Server has been stopped")

        client = httpx.Client(transport=self.server._transport, base_url="http://testserver", headers={"user": name})
        self.clients.append(client)
        return client

    def close(self) -> None:
        if self._is_running:
            self._is_running = False
            for client in self.clients:
                client.close()
            self.server.__exit__(None, None, None)

    def __enter__(self) -> "TestServer":
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def launch_server() -> TestServer:
    server = TestServer()
    server.start()
    return server


def launch_client(server: TestServer, name: str) -> httpx.Client:
    return server.launch_client(name)


if __name__ == "__main__":
    with TestServer() as server:
        client = server.launch_client("claire@openmined.org")
        response = client.get("/uid")
        print(response.json())

        with TestServer() as server_2:
            client = server_2.launch_client("dave@openmined.org")
            response = client.get("/uid")
            print(response.json())
