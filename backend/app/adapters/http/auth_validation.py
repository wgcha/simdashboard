"""Authentication validation errors must never echo submitted passwords."""

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute


class PrivateInputRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                raise HTTPException(422, detail={
                    "code": "ACCOUNT_INPUT_INVALID",
                    "message": "입력 항목과 허용 길이를 확인해 주세요.",
                }) from exc

        return validated
