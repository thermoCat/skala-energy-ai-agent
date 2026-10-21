"""web_demo_server.py — 에너지 허브 관리자 대시보드(web_chat.html)를 띄우는 서버.

화면은 Vue 3·Leaflet(CDN, 빌드 단계 없음) 한 장이고, server.py(8100, 백엔드)는
건드리지 않는다. 이 스크립트는
- web_chat.html 한 장을 같은 출처(origin)로 띄우고
- /chat/stream 요청을 그대로 8100으로 중계(프록시)해서

브라우저의 CORS 제약 없이 실제 에이전트와 대화할 수 있게 해주는 아주 얇은 레이어다.
server.py는 평소처럼(fake/ollama/openai 아무 백엔드로나) 8100에 미리 띄워 놓아야 한다.

실행:
    python3 web_demo_server.py        # http://localhost:8600 에서 채팅 화면 오픈
"""
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

BACKEND = "http://127.0.0.1:8100"
HERE = Path(__file__).parent

app = FastAPI()


@app.get("/")
async def index():
    return FileResponse(HERE / "web_chat.html")


@app.get("/health")
async def proxy_health():
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{BACKEND}/health")
    return JSONResponse(r.json(), status_code=r.status_code)


@app.post("/chat")
async def proxy_chat(req: Request):
    body = await req.body()
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(f"{BACKEND}/chat", content=body,
                              headers={"content-type": "application/json"})
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get("/chat/stream")
async def proxy_stream(req: Request):
    # 지도 클릭의 station_id·gu·company 도 함께 넘어가도록 쿼리를 통째로 전달
    params = dict(req.query_params)

    async def relay():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", f"{BACKEND}/chat/stream",
                                     params=params) as upstream:
                async for chunk in upstream.aiter_bytes():
                    yield chunk

    return StreamingResponse(relay(), media_type="text/event-stream")


@app.api_route("/api/{path:path}", methods=["GET", "POST"])
async def proxy_api(path: str, req: Request):
    """대시보드 API(/api/...) 중계 — 결정 입력(POST)도 그대로 넘긴다"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.request(req.method, f"{BACKEND}/api/{path}",
                                 params=dict(req.query_params), content=await req.body(),
                                 headers={"content-type": "application/json"})
    return JSONResponse(r.json(), status_code=r.status_code)


if __name__ == "__main__":
    print("브라우저에서 http://localhost:8600 을 열어보세요 (server.py가 8100에 떠 있어야 합니다)")
    uvicorn.run(app, host="0.0.0.0", port=8600)
