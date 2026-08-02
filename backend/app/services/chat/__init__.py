"""대화형 질의 — 세션·오케스트레이터·툴.

구조:

    sessions.py   세션과 메시지의 저장 (LangGraph checkpointer 미사용 — models/chat.py 주석)
    artifacts.py  답변의 **형태** 정의. 렌더 툴의 인자 스키마가 곧 프론트의 렌더링 계약이다.
    tools.py      모델에 노출하는 툴 — 검색 계열과 렌더 계열
    agent.py      툴 루프 (LangGraph StateGraph)
"""
