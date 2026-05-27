from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter()


@router.get("/v1/health", status_code=200)
async def health() -> Response:
    return Response(status_code=200)
