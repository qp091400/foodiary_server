"""에러 응답을 한국어로 통일한다. 모든 에러는 `{"detail": "..."}` 형태다."""

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

_VALIDATION_MESSAGES = {
    "missing": "필수 항목입니다.",
    "string_type": "문자열이어야 합니다.",
    "string_too_short": "최소 {min_length}자 이상이어야 합니다.",
    "string_too_long": "최대 {max_length}자까지 입력할 수 있습니다.",
    "string_pattern_mismatch": "형식이 올바르지 않습니다.",
    "int_type": "정수여야 합니다.",
    "int_parsing": "정수여야 합니다.",
    "int_from_float": "정수여야 합니다.",
    "float_type": "숫자여야 합니다.",
    "float_parsing": "숫자여야 합니다.",
    "bool_type": "true 또는 false여야 합니다.",
    "bool_parsing": "true 또는 false여야 합니다.",
    "greater_than": "{gt}보다 커야 합니다.",
    "greater_than_equal": "{ge} 이상이어야 합니다.",
    "less_than": "{lt}보다 작아야 합니다.",
    "less_than_equal": "{le} 이하여야 합니다.",
    "date_type": "날짜는 YYYY-MM-DD 형식이어야 합니다.",
    "date_parsing": "날짜는 YYYY-MM-DD 형식이어야 합니다.",
    "date_from_datetime_parsing": "날짜는 YYYY-MM-DD 형식이어야 합니다.",
    "date_from_datetime_inexact": "날짜는 YYYY-MM-DD 형식이어야 합니다.",
    "literal_error": "허용되지 않는 값입니다. (가능한 값: {expected})",
    "enum": "허용되지 않는 값입니다. (가능한 값: {expected})",
    "list_type": "목록이어야 합니다.",
    "too_short": "항목이 {min_length}개 이상 필요합니다.",
    "too_long": "항목은 {max_length}개까지 보낼 수 있습니다.",
    "model_attributes_type": "객체(JSON) 형식이어야 합니다.",
    "dict_type": "객체(JSON) 형식이어야 합니다.",
    "json_invalid": "JSON 형식이 올바르지 않습니다.",
}

_HTTP_MESSAGES = {
    404: "요청한 경로를 찾을 수 없습니다.",
    405: "허용되지 않는 요청 방식입니다.",
}


def _field(location: tuple) -> str | None:
    parts = [str(part) for part in location[1:]] if location else []
    return ".".join(parts) or None


def _message(error: dict) -> str:
    context = error.get("ctx") or {}
    if error.get("type") == "value_error":  # 스키마의 한국어 ValueError 메시지를 그대로 쓴다
        return str(context.get("error") or error.get("msg", "")).removeprefix("Value error, ")
    template = _VALIDATION_MESSAGES.get(error.get("type", ""))
    if template:
        try:
            return template.format(**context).replace(" or ", ", ")
        except (KeyError, IndexError):
            pass
    return "값이 올바르지 않습니다."


async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [{"field": _field(error.get("loc", ())), "message": _message(error)} for error in exc.errors()]
    detail = "입력값이 올바르지 않습니다."
    if errors:
        first = errors[0]
        detail = f"{first['field']}: {first['message']}" if first["field"] else first["message"]
    return JSONResponse(status_code=422, content={"detail": detail, "errors": errors})


async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    try:
        if detail == HTTPStatus(exc.status_code).phrase:  # 라우터가 직접 쓰지 않은 기본 영어 문구
            detail = _HTTP_MESSAGES.get(exc.status_code, "요청을 처리할 수 없습니다.")
    except ValueError:
        pass
    return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=exc.headers)


async def _server_error(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("처리되지 않은 오류", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."})


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _server_error)
