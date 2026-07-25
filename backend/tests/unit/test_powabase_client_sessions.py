import httpx
import respx

from app.clients.powabase_client import PowabaseClient

BASE_URL = "https://demo.p.powabase.ai"


@respx.mock
def test_insert_session_returns_created_row():
    route = respx.post(f"{BASE_URL}/rest/v1/sessions").mock(
        return_value=httpx.Response(201, json=[{"id": "s1", "name": "New session"}])
    )
    client = PowabaseClient(BASE_URL, "k")

    row = client.insert_session({"id": "s1", "user_slug": "alice", "name": "New session"})

    assert row == {"id": "s1", "name": "New session"}
    # PostgREST needs Prefer: return=representation to return the created row.
    assert route.calls.last.request.headers["prefer"] == "return=representation"


@respx.mock
def test_list_sessions_filters_by_user_and_orders():
    route = respx.get(f"{BASE_URL}/rest/v1/sessions").mock(
        return_value=httpx.Response(200, json=[{"id": "s1", "name": "A", "updated_at": "t"}])
    )
    client = PowabaseClient(BASE_URL, "k")

    rows = client.list_sessions("alice")

    assert rows == [{"id": "s1", "name": "A", "updated_at": "t"}]
    request = route.calls.last.request
    assert request.url.params["user_slug"] == "eq.alice"
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
def test_get_session_messages_calls_api():
    respx.get(f"{BASE_URL}/api/sessions/ps1/messages").mock(
        return_value=httpx.Response(200, json={"messages": []})
    )
    client = PowabaseClient(BASE_URL, "k")
    assert client.get_session_messages("ps1") == {"messages": []}


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
