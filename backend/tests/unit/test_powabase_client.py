import json

import httpx
import pytest
import respx

from app.clients.powabase_client import PowabaseAPIError, PowabaseClient

BASE_URL = "https://demo.p.powabase.ai"


@respx.mock
def test_upload_source_returns_new_source_id():
    respx.post(f"{BASE_URL}/api/sources/upload").mock(
        return_value=httpx.Response(
            201, json={"id": "src-1", "extraction_status": "pending"}
        )
    )
    client = PowabaseClient(BASE_URL, "test-key")

    result = client.upload_source("doc.pdf", b"%PDF-1.4 fake bytes")

    assert result["id"] == "src-1"


@respx.mock
def test_upload_source_reuses_duplicate_on_409():
    respx.post(f"{BASE_URL}/api/sources/upload").mock(
        return_value=httpx.Response(
            409,
            json={"error": "duplicate_source", "duplicate": {"id": "src-existing"}},
        )
    )
    client = PowabaseClient(BASE_URL, "test-key")

    result = client.upload_source("doc.pdf", b"%PDF-1.4 fake bytes")

    assert result["id"] == "src-existing"


@respx.mock
def test_get_source_raises_powabase_api_error_on_404():
    respx.get(f"{BASE_URL}/api/sources/missing").mock(
        return_value=httpx.Response(404, json={"error": "not_found"})
    )
    client = PowabaseClient(BASE_URL, "test-key")

    with pytest.raises(PowabaseAPIError) as exc_info:
        client.get_source("missing")

    assert exc_info.value.status_code == 404


@respx.mock
def test_run_agent_parses_sse_events():
    sse_body = (
        "event: start\n"
        'data: {"session_id": "sess-1"}\n'
        "\n"
        "event: complete\n"
        'data: {"answer": "The answer.", "citations": []}\n'
        "\n"
    )
    respx.post(f"{BASE_URL}/api/agents/agent-1/run/stream").mock(
        return_value=httpx.Response(
            200, text=sse_body, headers={"content-type": "text/event-stream"}
        )
    )
    client = PowabaseClient(BASE_URL, "test-key")

    events = client.run_agent("agent-1", "hello")

    assert events[0] == {"event": "start", "data": {"session_id": "sess-1"}}
    assert events[1]["event"] == "complete"
    assert events[1]["data"]["answer"] == "The answer."


@respx.mock
def test_run_agent_retries_once_on_503():
    route = respx.post(f"{BASE_URL}/api/agents/agent-1/run/stream")
    route.side_effect = [
        httpx.Response(503, json={"error": "billing service unreachable"}),
        httpx.Response(
            200,
            text='event: complete\ndata: {"answer": "ok"}\n\n',
            headers={"content-type": "text/event-stream"},
        ),
    ]
    client = PowabaseClient(BASE_URL, "test-key")

    events = client.run_agent("agent-1", "hello")

    assert events[0]["data"]["answer"] == "ok"
    assert route.call_count == 2


@respx.mock
def test_run_agent_sync_returns_json_body():
    respx.post(f"{BASE_URL}/api/agents/agent-1/run").mock(
        return_value=httpx.Response(200, json={"content": '{"needs_kb": true}', "status": "completed"})
    )
    client = PowabaseClient(BASE_URL, "test-key")
    body = client.run_agent_sync("agent-1", "hi", response_format={"type": "json_schema"})
    assert body["content"] == '{"needs_kb": true}'


@respx.mock
def test_create_context_handler_posts_query_and_kbs():
    route = respx.post(f"{BASE_URL}/api/context-handlers").mock(
        return_value=httpx.Response(201, json={"id": "handler-1"})
    )
    client = PowabaseClient(BASE_URL, "test-key")
    result = client.create_context_handler(
        "what is x", [{"id": "kb-1", "top_k": 4}], max_context_tokens=2000
    )
    assert result["id"] == "handler-1"
    sent = json.loads(route.calls[0].request.content)
    assert sent == {"query": "what is x", "knowledge_bases": [{"id": "kb-1", "top_k": 4}], "max_context_tokens": 2000}


@respx.mock
def test_run_agent_includes_context_handler_id_when_set():
    route = respx.post(f"{BASE_URL}/api/agents/agent-1/run/stream").mock(
        return_value=httpx.Response(200, text='data: {"event": "complete", "content": "ok"}\n\n',
                                    headers={"content-type": "text/event-stream"})
    )
    client = PowabaseClient(BASE_URL, "test-key")
    client.run_agent("agent-1", "hi", context_handler_id="handler-1")
    sent = json.loads(route.calls[0].request.content)
    assert sent["context_handler_id"] == "handler-1"


@respx.mock
def test_create_agent_includes_settings_when_set():
    route = respx.post(f"{BASE_URL}/api/agents").mock(
        return_value=httpx.Response(201, json={"id": "a-1"})
    )
    client = PowabaseClient(BASE_URL, "test-key")
    client.create_agent("r", model="gpt-4o-mini", system_prompt="p", settings={"temperature": 0})
    sent = json.loads(route.calls[0].request.content)
    assert sent["settings"] == {"temperature": 0}


@respx.mock
def test_create_kb_includes_indexing_config_when_set():
    route = respx.post(f"{BASE_URL}/api/knowledge-bases").mock(
        return_value=httpx.Response(201, json={"id": "kb-1"})
    )
    client = PowabaseClient(BASE_URL, "test-key")
    client.create_knowledge_base("n", description="d", indexing_config={"strategy": "full_document"})
    sent = json.loads(route.calls[0].request.content)
    assert sent == {"name": "n", "description": "d", "indexing_config": {"strategy": "full_document"}}


@respx.mock
def test_create_kb_omits_indexing_config_when_none():
    route = respx.post(f"{BASE_URL}/api/knowledge-bases").mock(
        return_value=httpx.Response(201, json={"id": "kb-1"})
    )
    client = PowabaseClient(BASE_URL, "test-key")
    client.create_knowledge_base("n")
    sent = json.loads(route.calls[0].request.content)
    assert "indexing_config" not in sent


@respx.mock
def test_create_kb_includes_retrieval_config_when_set():
    route = respx.post(f"{BASE_URL}/api/knowledge-bases").mock(
        return_value=httpx.Response(201, json={"id": "kb-1"})
    )
    PowabaseClient(BASE_URL, "k").create_knowledge_base(
        "n", retrieval_config={"reranker": {"model": "m", "candidate_count": 20}}
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent["retrieval_config"] == {"reranker": {"model": "m", "candidate_count": 20}}


@respx.mock
def test_update_knowledge_base_patches():
    route = respx.patch(f"{BASE_URL}/api/knowledge-bases/kb-1").mock(
        return_value=httpx.Response(200, json={"id": "kb-1"})
    )
    PowabaseClient(BASE_URL, "k").update_knowledge_base("kb-1", {"retrieval_config": {"x": 1}})
    assert json.loads(route.calls[0].request.content) == {"retrieval_config": {"x": 1}}


@respx.mock
def test_update_agent_patches_fields():
    route = respx.patch(f"{BASE_URL}/api/agents/a-1").mock(
        return_value=httpx.Response(200, json={"id": "a-1", "model": "gpt-4o-mini"})
    )
    result = PowabaseClient(BASE_URL, "k").update_agent("a-1", {"model": "gpt-4o-mini"})
    assert result["model"] == "gpt-4o-mini"
    assert json.loads(route.calls[0].request.content) == {"model": "gpt-4o-mini"}


@respx.mock
def test_remove_source_from_kb_deletes_the_link_not_the_source():
    # upload_source reuses duplicates on 409, so one source can live in several
    # KBs. Untraining must unlink, never delete the source itself.
    route = respx.delete(f"{BASE_URL}/api/knowledge-bases/kb-1/sources/src-1").mock(
        return_value=httpx.Response(204)
    )
    PowabaseClient(BASE_URL, "k").remove_source_from_kb("kb-1", "src-1")
    assert route.called


@respx.mock
def test_insert_agent_row_returns_the_created_row():
    respx.post(f"{BASE_URL}/rest/v1/agents").mock(
        return_value=httpx.Response(201, json=[{"id": "ag-1", "name": "Tutor"}])
    )
    row = PowabaseClient(BASE_URL, "k").insert_agent_row({"name": "Tutor"})
    assert row["id"] == "ag-1"


@respx.mock
def test_list_agent_rows_filters_by_owner():
    route = respx.get(f"{BASE_URL}/rest/v1/agents").mock(
        return_value=httpx.Response(200, json=[{"id": "ag-1"}])
    )
    rows = PowabaseClient(BASE_URL, "k").list_agent_rows("o1")
    assert rows == [{"id": "ag-1"}]
    assert "owner_id=eq.o1" in str(route.calls[0].request.url)


@respx.mock
def test_get_agent_row_returns_none_when_absent():
    respx.get(f"{BASE_URL}/rest/v1/agents").mock(return_value=httpx.Response(200, json=[]))
    assert PowabaseClient(BASE_URL, "k").get_agent_row("nope") is None


@respx.mock
def test_update_agent_row_patches_by_id():
    route = respx.patch(f"{BASE_URL}/rest/v1/agents").mock(return_value=httpx.Response(204))
    PowabaseClient(BASE_URL, "k").update_agent_row("ag-1", {"name": "New"})
    assert "id=eq.ag-1" in str(route.calls[0].request.url)
    assert json.loads(route.calls[0].request.content) == {"name": "New"}


@respx.mock
def test_delete_agent_row_deletes_by_id():
    route = respx.delete(f"{BASE_URL}/rest/v1/agents").mock(return_value=httpx.Response(204))
    PowabaseClient(BASE_URL, "k").delete_agent_row("ag-1")
    assert "id=eq.ag-1" in str(route.calls[0].request.url)




@respx.mock
def test_run_agent_sends_runtime_knowledge_bases():
    """Query-specific KB attachment: the KBs ride along with the run itself."""
    route = respx.post(f"{BASE_URL}/api/agents/agent-1/run/stream").mock(
        return_value=httpx.Response(200, text='data: {"event": "complete", "content": "ok"}\n\n',
                                    headers={"content-type": "text/event-stream"})
    )
    client = PowabaseClient(BASE_URL, "test-key")
    client.run_agent(
        "agent-1", "hi",
        runtime_knowledge_bases=[{"id": "kb-1", "top_k": 4, "source_ids": ["src-1"]}],
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent["runtime_knowledge_bases"] == [
        {"id": "kb-1", "top_k": 4, "source_ids": ["src-1"]}
    ]
    # Mutually exclusive with the other context sources — sending both is a 400.
    assert "context_handler_id" not in sent


@respx.mock
def test_run_agent_omits_runtime_knowledge_bases_when_empty():
    """An empty list must not be sent; Powabase rejects an empty context source."""
    route = respx.post(f"{BASE_URL}/api/agents/agent-1/run/stream").mock(
        return_value=httpx.Response(200, text='data: {"event": "complete", "content": "ok"}\n\n',
                                    headers={"content-type": "text/event-stream"})
    )
    client = PowabaseClient(BASE_URL, "test-key")
    client.run_agent("agent-1", "hi", runtime_knowledge_bases=[])
    sent = json.loads(route.calls[0].request.content)
    assert "runtime_knowledge_bases" not in sent
