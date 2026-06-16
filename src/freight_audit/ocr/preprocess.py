"""
Preprocessing for messy real-world document photos.

Item #6 on the "not ready" list was: "Accuracy on messy real docs ... real scans,
handwriting, and skewed photos will need accuracy work." This is the first slice
of that work -- a cheap, dependency-light pipeline that lifts OCR accuracy on a
phone photo of a receipt before a single character is read.

None of this is exotic; it's the standard preprocessing that turns a 70%-accurate
raw OCR pass into a 90%+ one. In production you'd tune these per document type.
"""
from __future__ import annotations

import cv2
import numpy as np


def _deskew(gray: np.ndarray) -> np.ndarray:
    """Estimate and correct small rotation (phone photos are rarely straight)."""
    inv = cv2.bitwise_not(gray)
    thr = cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thr > 0))
    if coords.shape[0] < 50:
        return gray
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = 90 + angle
    # only correct meaningful skew; ignore tiny noise
    if abs(angle) < 0.4 or abs(angle) > 20:
        return gray
    (h, w) = gray.shape
    M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(gray, M, (w, h),
                          flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def preprocess(image_path: str, scale: float = 2.0) -> np.ndarray:
    """Return a cleaned, binarized image (numpy array) ready for Tesseract.

    Steps: grayscale -> upscale -> denoise -> deskew -> adaptive threshold.
    """
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(image_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # upscale small/low-dpi photos so thin thermal-printer strokes survive
    if scale and scale != 1.0:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    # remove speckle while keeping edges
    gray = cv2.fastNlMeansDenoising(gray, None, h=10, templateWindowSize=7, searchWindowSize=21)

    gray = _deskew(gray)

    # adaptive threshold handles uneven lighting / shadows across a curled receipt
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
        blockSize=31, C=15)
    return binary


def save_preprocessed(image_path: str, out_path: str, scale: float = 2.0) -> str:
    binary = preprocess(image_path, scale=scale)
    cv2.imwrite(out_path, binary)
    return out_path
