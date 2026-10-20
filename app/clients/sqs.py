"""SQS 큐 하나를 다루는 클라이언트. boto3는 동기 호출이라 스레드에서 실행합니다."""
from __future__ import annotations

import asyncio
import json

import boto3


# Long Polling 최대값. 받은 메시지가 없어도 20초 동안 기다렸다가 돌아옵니다.
RECEIVE_WAIT_SECONDS = 20


def sqs_client(region: str):
    # 자격 증명은 기본 체인(운영: ECS Task Role, 로컬: 프로필·환경변수)을 씁니다.
    return boto3.client("sqs", region_name=region)


class SqsQueue:
    def __init__(self, client, url: str) -> None:
        self.client = client
        self.url = url

    async def receive(self) -> list[dict]:
        response = await asyncio.to_thread(
            self.client.receive_message, QueueUrl=self.url, MaxNumberOfMessages=1,
            WaitTimeSeconds=RECEIVE_WAIT_SECONDS, MessageSystemAttributeNames=["ApproximateReceiveCount"],
        )
        return response.get("Messages", [])

    async def send(self, body: dict) -> None:
        await asyncio.to_thread(self.client.send_message, QueueUrl=self.url,
                                MessageBody=json.dumps(body, ensure_ascii=False))

    async def delete(self, receipt_handle: str) -> None:
        await asyncio.to_thread(self.client.delete_message, QueueUrl=self.url, ReceiptHandle=receipt_handle)
