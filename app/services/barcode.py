"""영수증 이미지에서 바코드 디코딩 (zxing-cpp).

영수증 바코드에는 거래번호만 들어 있다. 품목 정보가 아니라 "같은 영수증 중복 등록 방지 키"로만 쓴다.
"""

import logging

from PIL import Image

logger = logging.getLogger(__name__)

# 거래번호 바코드는 1차원이다. QR(설문·이벤트 URL)이나 EAN/UPC(사진에 같이 찍힌 상품 바코드)는
# 영수증마다 같은 값이 나와 엉뚱한 중복 판정을 만들 수 있어서 쓰지 않는다.
_RECEIPT_FORMATS = {"CODE128", "CODE39", "CODE39STD", "CODE39EXT", "CODE93", "ITF", "ITF14", "CODABAR"}
_MIN_LENGTH = 8
_MAX_LENGTH = 64


def _format_name(result: object) -> str:
    fmt = getattr(result, "format", None)
    name = getattr(fmt, "name", None) or str(fmt)
    return "".join(ch for ch in name.split(".")[-1].upper() if ch.isalnum())


def decode_receipt_barcode(image: Image.Image) -> str | None:
    """영수증 바코드 문자열. 못 읽으면 None (업로드는 그대로 진행한다)."""
    try:
        import zxingcpp

        results = zxingcpp.read_barcodes(image)
    except Exception as exc:
        logger.warning("바코드 디코딩 실패: %s", exc)
        return None

    candidates = []
    for result in results:
        text = (getattr(result, "text", "") or "").strip()
        if _format_name(result) in _RECEIPT_FORMATS and _MIN_LENGTH <= len(text) <= _MAX_LENGTH:
            candidates.append(text)
    # 여러 개면 가장 긴 것(거래번호가 보통 가장 길다), 길이가 같으면 사전순으로 골라 항상 같은 값을 낸다.
    return max(candidates, key=lambda text: (len(text), text)) if candidates else None
