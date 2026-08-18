"""화면에서 MCP 도구를 쓰게 하는 다리.

Claude Desktop 이 내부에서 하는 일을 그대로 만든 것이다. 사용자는 한국말로 묻고,
LLM 이 어떤 도구를 부를지 고르고, 그 결과로 다시 답을 만든다.

    ① MCP 서버에 붙어 도구 목록을 받는다
    ② 그 목록을 LLM 이 아는 형식으로 바꾼다
    ③ 사용자 질문 + 도구 목록을 LLM 에 보낸다
    ④ LLM 이 도구를 지목하면 MCP 로 실행하고 결과를 되돌려준다
    ⑤ LLM 이 최종 답을 낼 때까지 ③~④ 를 반복한다

엔진과 같은 프로세스에서 도는데도 MCP 를 거치는 이유가 있다. 도구를 추가·교체할 때
엔진을 건드리지 않아도 되고, 같은 MCP 서버를 이 화면과 Claude Desktop 이 함께 쓴다.
도구를 한 번 만들면 두 곳에서 쓰인다.

대화 상태는 여기서 들고 있지 않는다. 화면이 기록을 보내고 서버는 매번 새로 판단한다 --
API 의 다른 경로와 같은 방식이라, 서버를 여러 대로 늘려도 그대로 돈다.
"""

from __future__ import annotations

import sys
from contextlib import AsyncExitStack
from typing import Any, Optional

import ollama
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app import config

# LLM 이 도구를 부르고 -> 결과를 보고 -> 또 부르는 것을 몇 번까지 허용할지.
# 없으면 같은 도구를 계속 부르며 끝나지 않는 일이 생긴다.
MAX_ROUNDS = 5

# 도구 결과가 길면(원문 수천 자) 그대로 넣을 때 모델 컨텍스트를 넘긴다.
MAX_TOOL_RESULT_CHARS = 4000

SYSTEM_PROMPT = """당신은 송장·영수증 검증 시스템의 조회 도우미입니다.

- 반드시 한국어로 답하세요.
- 문서 정보를 물으면 반드시 도구를 사용해 실제 값을 확인한 뒤 답하세요.
  추측하거나 지어내지 마세요.
- 도구 결과에 없는 내용은 "확인되지 않는다"고 말하세요.
- 답은 짧고 구체적으로. 문서는 '#31 invoice-7-0.pdf' 처럼 번호와 파일명을 함께 씁니다.
- 승인·삭제·업로드는 도구에 없습니다. 요청받으면 화면에서 직접 해야 한다고 안내하세요.

'문서' 와 '오류 신고' 는 다릅니다.
- 문서: 업로드한 송장·영수증. '오류 난 문서' 는 list_documents(status='ERROR').
- 오류 신고: 사람이 화면에서 접수한 버그 신고. list_reports.

중요: 답변은 처음부터 끝까지 한국어로만 씁니다. 중국어나 영어 문장을 섞지 마세요.
"""


class McpBridge:
    """MCP 서버에 붙어 있는 연결 하나를 앱이 사는 동안 유지한다.

    요청마다 새로 붙으면 그때마다 파이썬 프로세스가 뜬다. 문서 조회 한 번에 수 초가
    걸려 쓸 수 없다.
    """

    def __init__(self) -> None:
        self._stack: Optional[AsyncExitStack] = None
        self.session: Optional[ClientSession] = None
        self.tools: list[dict[str, Any]] = []
        self.tool_names: list[str] = []

    async def start(self) -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["mcp_server.py"],
            cwd=str(config.BASE_DIR),
            env={"PYTHONIOENCODING": "utf-8", "MSSQL_DATABASE": config.MSSQL_DATABASE},
        )
        stack = AsyncExitStack()
        read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        # ② MCP 도구 명세를 Ollama 가 아는 형식으로 옮긴다.
        #    input_schema 가 이미 JSON Schema 라 parameters 에 그대로 들어간다.
        listed = await session.list_tools()
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": (t.description or "").strip(),
                    "parameters": t.input_schema,
                },
            }
            for t in listed.tools
        ]
        self.tool_names = [t.name for t in listed.tools]
        self._stack = stack
        self.session = session

    async def stop(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self.session = None

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        """도구 하나를 실행하고 글자로 돌려준다."""
        if self.session is None:
            return '{"error": "MCP 서버에 연결되어 있지 않습니다."}'
        result = await self.session.call_tool(name, arguments or {})
        parts = [getattr(c, "text", "") for c in (result.content or [])]
        text = "\n".join(p for p in parts if p)
        if len(text) > MAX_TOOL_RESULT_CHARS:
            text = text[:MAX_TOOL_RESULT_CHARS] + "\n...(잘림)"
        return text or "(빈 결과)"


bridge = McpBridge()


async def answer(
    question: str, history: Optional[list[dict[str, str]]] = None
) -> dict[str, Any]:
    """질문 하나에 답한다. 어떤 도구를 거쳤는지도 함께 돌려준다.

    도구 사용 내역을 숨기지 않는 것은 의도한 것이다. 무엇을 근거로 답했는지 화면에서
    볼 수 있어야 답을 믿을 수 있다.
    """
    if bridge.session is None:
        return {"answer": "MCP 서버에 연결되어 있지 않습니다.", "tool_calls": [], "rounds": 0}

    client = ollama.AsyncClient(host=config.OLLAMA_HOST)
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in history or []:
        role = turn.get("role")
        if role in ("user", "assistant") and turn.get("content"):
            messages.append({"role": role, "content": turn["content"]})
    messages.append({"role": "user", "content": question})

    used: list[dict[str, Any]] = []

    for round_no in range(1, MAX_ROUNDS + 1):
        # ③ 질문 + 도구 목록을 LLM 에 보낸다
        response = await client.chat(
            model=config.OLLAMA_MODEL,
            messages=messages,
            tools=bridge.tools,
            options={"temperature": 0, "num_ctx": config.OLLAMA_NUM_CTX},
        )
        message = response["message"]
        calls = message.get("tool_calls") or []
        messages.append(message)

        # ⑤ 도구를 더 부르지 않으면 그것이 최종 답이다
        if not calls:
            return {
                "answer": (message.get("content") or "").strip(),
                "tool_calls": used,
                "rounds": round_no,
            }

        # ④ 지목한 도구를 실행해 결과를 되돌려준다
        for call in calls:
            name = call["function"]["name"]
            args = call["function"].get("arguments") or {}
            if isinstance(args, str):
                import json as _json

                try:
                    args = _json.loads(args)
                except ValueError:
                    args = {}

            if name not in bridge.tool_names:
                # 모델이 없는 도구를 지어내는 일이 있다. 조용히 넘기지 않고 알려 준다.
                result = f'{{"error": "그런 도구는 없습니다: {name}"}}'
            else:
                result = await bridge.call(name, args)

            used.append({"name": name, "arguments": args, "result": result[:600]})
            messages.append({"role": "tool", "name": name, "content": result})

    # 여기까지 왔다면 도구만 계속 부르고 답을 못 낸 것이다
    return {
        "answer": (
            f"도구를 {MAX_ROUNDS}번 호출했지만 답을 정리하지 못했습니다. "
            f"질문을 더 좁혀서 다시 물어봐 주세요."
        ),
        "tool_calls": used,
        "rounds": MAX_ROUNDS,
    }
