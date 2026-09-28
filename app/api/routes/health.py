from fastapi import APIRouter


router = APIRouter(tags=["system"])


@router.get("/health")
async def health():
    return {"status": "ok", "mode": "mock"}
