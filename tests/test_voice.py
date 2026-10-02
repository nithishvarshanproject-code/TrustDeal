"""Voice mode (Customer page): speech-to-text runs in the browser; the text takes the same
/customer/messages path as typed text. The only difference is the "web-voice" label on that message."""
from tests.test_agent import PHONE, PRIYA, seller


def _send(client, text, voice=None):
    body = {"customer_id": PRIYA, "text": text, "product_id": PHONE}
    if voice is not None:
        body["voice"] = voice
    resp = client.post("/customer/messages", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _comparable(view):
    """The customer view without ids and timestamps."""
    return {"status": view["status"], "offer": view["offer"], "quote": view["quote"], "actions": view["actions"],
            "messages": [(m["sender"], m["text"]) for m in view["messages"]]}


def test_voice_message_takes_the_same_path_and_is_labelled(stack):
    c = stack["client"]
    typed = _send(c, "Can I get 20% off this phone?")
    c.post(f"/customer/requests/{typed['request_id']}/decline", json={"customer_id": PRIYA})
    spoken = _send(c, "Can I get 20% off this phone?", voice=True)

    assert spoken["request_id"] != typed["request_id"]
    assert _comparable(spoken) == _comparable(typed)            # same MeTTa decision, same customer view
    assert "channel" not in spoken["messages"][0]                # the customer view is unchanged

    typed_detail, spoken_detail = seller(c, typed["request_id"]), seller(c, spoken["request_id"])
    assert [m["channel"] for m in spoken_detail["messages"] if m["sender"] == "customer"] == ["web-voice"]
    assert [m["channel"] for m in typed_detail["messages"] if m["sender"] == "customer"] == ["web", "web"]
    assert {m["channel"] for m in spoken_detail["messages"] if m["sender"] == "agent"} == {"web"}
    assert spoken_detail["channel"] == typed_detail["channel"] == "web"
    assert [d["result"] for d in spoken_detail["decisions"]] == [d["result"] for d in typed_detail["decisions"]]


def test_voice_flag_keeps_the_same_limits(stack):
    c = stack["client"]
    resp = c.post("/customer/messages", json={"customer_id": PRIYA, "product_id": PHONE, "text": "x" * 501,
                                              "voice": True})
    assert resp.status_code == 422
    resp = c.post("/customer/messages", json={"customer_id": PRIYA, "product_id": PHONE, "text": "hi",
                                              "voice": "web-voice"})
    assert resp.status_code == 422                               # a strict flag, not a free channel string
