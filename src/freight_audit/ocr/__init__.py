"""
OCR extraction subpackage.

Turns a document photo into structured, self-validated data:
    photo -> preprocess -> OCR -> noisy text -> layout-driven field mapping
          -> structured Receipt -> self-consistency validation -> verdict

The OCR step is isolated behind ``extract.ocr_image`` so a cloud OCR API
(AWS Textract / Google Document AI / Veryfi) can be dropped in without touching
the parser, the validator, or the tests.
"""
from .extract import (
    Receipt, ReceiptLine, extract_receipt, parse_receipt, parse_money,
    ocr_image, ocr_mean_confidence,
)
from .layouts import LayoutProfile, DEFAULT_LAYOUT, TABULAR_LAYOUT, LAYOUTS
from .validate import validate, ValidationResult, Check, Severity
from .preprocess import preprocess, save_preprocessed

# alias to disambiguate from the engine's Severity when both are imported
OcrSeverity = Severity

__all__ = [
    "Receipt", "ReceiptLine", "extract_receipt", "parse_receipt", "parse_money",
    "ocr_image", "ocr_mean_confidence",
    "LayoutProfile", "DEFAULT_LAYOUT", "TABULAR_LAYOUT", "LAYOUTS",
    "validate", "ValidationResult", "Check", "Severity", "OcrSeverity",
    "preprocess", "save_preprocessed",
]
