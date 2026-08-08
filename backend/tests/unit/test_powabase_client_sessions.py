import json

import httpx
import respx

from app.clients.powabase_client import PowabaseClient

BASE_URL = "https://demo.p.powabase.ai"


@respx.mock
def test_insert_session_returns_created_row():
    route = respx.post(f"{BASE_URL}/rest/v1/sessions").mock(
        return_value=httpx.Response(201, json=[{"id": "s1", "name": "New chat"}])
    )
    client = PowabaseClient(BASE_URL, "k")

    row = client.insert_session({"id": "s1", "user_slug": "alice", "name": "New chat"})

    assert row == {"id": "s1", "name": "New chat"}
    # PostgREST needs Prefer: return=representation to return the created row.
    assert route.calls.last.request.headers["prefer"] == "return=representation"


@respx.mock
def test_list_sessions_filters_by_owner_id():
    route = respx.get(f"{BASE_URL}/rest/v1/sessions").mock(
        return_value=httpx.Response(200, json=[{"id": "s1", "name": "A", "updated_at": "t"}])
    )
    client = PowabaseClient(BASE_URL, "k")

    rows = client.list_sessions("owner-1")

    assert rows == [{"id": "s1", "name": "A", "updated_at": "t"}]
    request = route.calls.last.request
    assert request.url.params["owner_id"] == "eq.owner-1"
    assert request.url.params["order"] == "updated_at.desc"


@respx.mock
def test_get_session_row_returns_first_or_none():
    route = respx.get(f"{BASE_URL}/rest/v1/sessions").mock(
        return_value=httpx.Response(200, json=[{"id": "s1", "agent_id": "a1"}])
    )
    client = PowabaseClient(BASE_URL, "k")
    assert client.get_session_row("s1") == {"id": "s1", "agent_id": "a1"}
    # Must filter by id — a missing filter would return the wrong session's row.
    assert route.calls.last.request.url.params["id"] == "eq.s1"


@respx.mock
def test_get_session_row_returns_none_when_empty():
    respx.get(f"{BASE_URL}/rest/v1/sessions").mock(
        return_value=httpx.Response(200, json=[])
    )
    client = PowabaseClient(BASE_URL, "k")
    assert client.get_session_row("missing") is None


@respx.mock
def test_update_session_patches_by_id():
    route = respx.patch(f"{BASE_URL}/rest/v1/sessions").mock(
        return_value=httpx.Response(204)
    )
    client = PowabaseClient(BASE_URL, "k")

    client.update_session("s1", {"name": "Renamed"})

    assert route.calls.last.request.url.params["id"] == "eq.s1"


@respx.mock
def test_get_session_row_returns_none_on_malformed_id_400():
    # PostgREST rejects a non-uuid id with 400; it matches no session → None.
    respx.get(f"{BASE_URL}/rest/v1/sessions").mock(
        return_value=httpx.Response(400, json={"message": "invalid input syntax for type uuid"})
    )
    client = PowabaseClient(BASE_URL, "k")
    assert client.get_session_row("not-a-uuid") is None


@respx.mock
def test_delete_session_row_deletes_by_id():
    route = respx.delete(f"{BASE_URL}/rest/v1/sessions").mock(
        return_value=httpx.Response(204)
    )
    client = PowabaseClient(BASE_URL, "k")
    client.delete_session_row("s1")
    assert route.calls.last.request.url.params["id"] == "eq.s1"


@respx.mock
def test_delete_knowledge_base_calls_api():
    route = respx.delete(f"{BASE_URL}/api/knowledge-bases/kb1").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = PowabaseClient(BASE_URL, "k")
    client.delete_knowledge_base("kb1")
    assert route.called


@respx.mock
def test_delete_agent_calls_api():
    route = respx.delete(f"{BASE_URL}/api/agents/a1").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = PowabaseClient(BASE_URL, "k")
    client.delete_agent("a1")
    assert route.called


@respx.mock
def test_insert_user_returns_created_row():
    respx.post(f"{BASE_URL}/rest/v1/users").mock(
        return_value=httpx.Response(201, json=[{"id": "u-1", "username": "alice"}])
    )
    client = PowabaseClient(BASE_URL, "k")
    row = client.insert_user({"username": "alice", "password_hash": "h"})
    assert row["id"] == "u-1"


@respx.mock
def test_get_user_by_username_found_and_missing():
    route = respx.get(f"{BASE_URL}/rest/v1/users")
    route.mock(return_value=httpx.Response(200, json=[{"id": "u-1", "username": "alice"}]))
    client = PowabaseClient(BASE_URL, "k")
    assert client.get_user_by_username("alice")["id"] == "u-1"
    route.mock(return_value=httpx.Response(200, json=[]))
    assert client.get_user_by_username("nobody") is None


@respx.mock
def test_get_user_returns_row_or_none():
    route = respx.get(f"{BASE_URL}/rest/v1/users").mock(
        return_value=httpx.Response(200, json=[{"id": "u-1", "username": "alice"}])
    )
    client = PowabaseClient(BASE_URL, "k")
    assert client.get_user("u-1")["id"] == "u-1"
    # Must filter by id
    assert route.calls.last.request.url.params["id"] == "eq.u-1"


@respx.mock
def test_list_users_orders_by_created():
    route = respx.get(f"{BASE_URL}/rest/v1/users").mock(
        return_value=httpx.Response(200, json=[{"id": "u1", "username": "a"}])
    )
    client = PowabaseClient(BASE_URL, "k")
    assert client.list_users()[0]["id"] == "u1"
    assert route.calls[0].request.url.params["order"] == "created_at.desc"


@respx.mock
def test_list_all_sessions_returns_rows():
    respx.get(f"{BASE_URL}/rest/v1/sessions").mock(
        return_value=httpx.Response(200, json=[{"id": "s1", "owner_id": "u1"}])
    )
    assert PowabaseClient(BASE_URL, "k").list_all_sessions()[0]["owner_id"] == "u1"


@respx.mock
def test_update_user_patches_by_id():
    route = respx.patch(f"{BASE_URL}/rest/v1/users").mock(return_value=httpx.Response(204))
    PowabaseClient(BASE_URL, "k").update_user("u1", {"username": "b"})
    assert route.calls[0].request.url.params["id"] == "eq.u1"
    assert json.loads(route.calls[0].request.content) == {"username": "b"}


@respx.mock
def test_delete_user_deletes_by_id():
    route = respx.delete(f"{BASE_URL}/rest/v1/users").mock(return_value=httpx.Response(204))
    PowabaseClient(BASE_URL, "k").delete_user("u1")
    assert route.calls[0].request.url.params["id"] == "eq.u1"


@respx.mock
def test_insert_and_list_messages_use_our_own_table():
    # The transcript lives in public.messages now: Powabase threads are bound
    # to one agent, so they can't carry a multi-agent conversation.
    respx.post(f"{BASE_URL}/rest/v1/messages").mock(
        return_value=httpx.Response(201, json=[{"id": "m-1"}])
    )
    route = respx.get(f"{BASE_URL}/rest/v1/messages").mock(
        return_value=httpx.Response(200, json=[{"id": "m-1", "role": "user"}])
    )
    c = PowabaseClient(BASE_URL, "k")

    assert c.insert_message({"session_id": "s1", "role": "user", "content": "hi"})["id"] == "m-1"
    assert c.list_messages("s1")[0]["role"] == "user"
    url = str(route.calls[0].request.url)
    assert "session_id=eq.s1" in url
    assert "order=created_at.asc" in url
