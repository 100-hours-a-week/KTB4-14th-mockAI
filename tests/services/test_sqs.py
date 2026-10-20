import asyncio

from botocore.stub import Stubber

from app.clients.sqs import SqsQueue, sqs_client


URL = "https://sqs.ap-northeast-2.amazonaws.com/123456789012/Audigo-AI-Job-Queue"


def test_sqs_queue_calls_match_aws_api(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    client = sqs_client("ap-northeast-2")
    queue = SqsQueue(client, URL)
    with Stubber(client) as stub:
        stub.add_response("receive_message", {"Messages": [{"MessageId": "m", "ReceiptHandle": "r", "Body": "{}"}]}, {
            "QueueUrl": URL, "MaxNumberOfMessages": 1, "WaitTimeSeconds": 20,
            "MessageSystemAttributeNames": ["ApproximateReceiveCount"],
        })
        stub.add_response("receive_message", {}, None)
        stub.add_response("send_message", {"MessageId": "s", "MD5OfMessageBody": "x"},
                          {"QueueUrl": URL, "MessageBody": '{"type": "완료"}'})
        stub.add_response("delete_message", {}, {"QueueUrl": URL, "ReceiptHandle": "r"})

        assert [m["ReceiptHandle"] for m in asyncio.run(queue.receive())] == ["r"]
        assert asyncio.run(queue.receive()) == []
        asyncio.run(queue.send({"type": "완료"}))
        asyncio.run(queue.delete("r"))
        stub.assert_no_pending_responses()
