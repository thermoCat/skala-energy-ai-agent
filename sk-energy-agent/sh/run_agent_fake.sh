#!/usr/bin/env bash
#
# fake LLM(결정론)으로 에이전트를 기동해 두는 스크립트 — 시연·개발용, 과금 없음.
#
# 실제 모델 대신 규칙으로 답하는 fake LLM을 쓰므로 같은 질문엔 항상 같은 답이 나온다.
# 분석 에이전트가 경유 유휴 위험을 일부러 1건 틀리게 판별해, 권위 게이트가 교정하는
# 장면이 매번 재현된다. 숫자는 실제 스냅샷(data/snapshots/)에서 나온다.
#
# 종료하려면 이 터미널에서 Ctrl+C.
#
set -euo pipefail
cd "$(dirname "$0")/.."

[ -x ".venv/bin/python3" ] && export PATH="$PWD/.venv/bin:$PATH"

# LangSmith 추적은 .env가 있으면 켜고, 없으면 끈 채로 실행한다
if [ -f .env ]; then
    set -a; source .env; set +a
    export LANGCHAIN_PROJECT="capstone-all-total-fake"
else
    export LANGCHAIN_TRACING_V2=false
fi
echo "LangSmith: project=${LANGCHAIN_PROJECT:-} tracing=${LANGCHAIN_TRACING_V2:-}"

# 이전 실행 정리 — 대화·판별 이력(session.db)도 새로 시작한다
lsof -ti:8100 | xargs kill -9 2>/dev/null || true
rm -f session.db

cleanup() {
    echo
    echo "에이전트를 종료합니다..."
    lsof -ti:8100 | xargs kill -9 2>/dev/null || true
}
trap cleanup EXIT

LLM_BACKEND=fake python3 server.py &
SERVER_PID=$!
for _ in $(seq 1 40); do
    curl -s http://127.0.0.1:8100/health >/dev/null 2>&1 && break
    sleep 0.5
done

echo
curl -s http://127.0.0.1:8100/health | python3 -m json.tool --no-ensure-ascii

cat <<EOF

################################################################
 에이전트가 떠 있습니다 (http://127.0.0.1:8100, LLM_BACKEND=fake)
################################################################

1) 주유소 진단 (지도 핀 클릭과 같은 요청: 동대문구 SK에너지 풍한주유소)
   curl -s -X POST localhost:8100/chat -H 'Content-Type: application/json' \\
     -d '{"message":"이 주유소에 무엇을 더해야 해?","thread_id":"t-test","station_id":"A0009172"}' \\
     | python3 -m json.tool --no-ensure-ascii

2) 자치구 묶음 진단을 실시간 진행 상황과 함께 보기 (스트리밍)
   curl -sN -G "localhost:8100/chat/stream" \\
     --data-urlencode "message=동대문구 SK 거점 8곳을 어떻게 나눠 써야 해?" \\
     --data-urlencode "gu=동대문구" \\
     --data-urlencode "thread_id=t-test-sse"

3) 시나리오·이름 찾기
   curl -s -X POST localhost:8100/chat -H 'Content-Type: application/json' \\
     -d '{"message":"박미주유소에 급속 2기 넣으면?","thread_id":"t-test-2"}' \\
     | python3 -m json.tool --no-ensure-ascii

4) 브라우저 채팅 화면으로 써보기 (새 터미널에서)
   bash sh/run_frontend.sh   → http://localhost:8600

종료하려면 이 터미널에서 Ctrl+C.
EOF

wait "$SERVER_PID"
