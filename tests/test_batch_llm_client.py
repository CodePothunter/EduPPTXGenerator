import json
from unittest.mock import MagicMock

from edupptx.llm_client import BatchLLMClient


def test_batch_client_submit_returns_batch_id():
    mock_client = MagicMock()
    mock_client.batches.create.return_value = MagicMock(id="batch_abc123")
    mock_client.files.create.return_value = MagicMock(id="file_input_001")

    client = BatchLLMClient.__new__(BatchLLMClient)
    client._client = mock_client
    client._model = "test-model"

    batch_id = client.submit_batch([
        {"role": "user", "content": "prompt 1"},
        {"role": "user", "content": "prompt 2"},
    ])
    assert batch_id == "batch_abc123"
    mock_client.batches.create.assert_called_once()


def test_batch_client_poll_returns_results():
    mock_client = MagicMock()
    mock_client.batches.retrieve.return_value = MagicMock(
        status="completed",
        output_file_id="file_xyz",
    )
    mock_client.files.content.return_value = MagicMock(
        text="\n".join([
            json.dumps({"custom_id": "req_0", "response": {"body": {"choices": [{"message": {"content": "result 0"}}]}}}),
            json.dumps({"custom_id": "req_1", "response": {"body": {"choices": [{"message": {"content": "result 1"}}]}}}),
        ])
    )

    client = BatchLLMClient.__new__(BatchLLMClient)
    client._client = mock_client
    client._model = "test-model"

    results = client.poll_batch("batch_abc123", interval=0)
    assert len(results) == 2
    assert results[0] == "result 0"
    assert results[1] == "result 1"


def test_batch_client_poll_raises_on_failure():
    mock_client = MagicMock()
    mock_client.batches.retrieve.return_value = MagicMock(status="failed")

    client = BatchLLMClient.__new__(BatchLLMClient)
    client._client = mock_client
    client._model = "test-model"

    try:
        client.poll_batch("batch_fail", interval=0)
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "failed" in str(e)
