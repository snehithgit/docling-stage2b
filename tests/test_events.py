import asyncio
import json
import pytest
from app.events import EventBroker

@pytest.mark.asyncio
async def test_unbounded_alert_stream_does_not_drop_burst():
    broker = EventBroker()
    stream = broker.stream(maxsize=0)
    await anext(stream)  # register listener / connected frame
    for i in range(20):
        broker.notify("alert", sequence=i)
    seen=[]
    for _ in range(20):
        frame = await anext(stream)
        payload=json.loads(frame.split("data: ",1)[1].strip())
        seen.append(payload["sequence"])
    assert seen == list(range(20))
    await stream.aclose()
