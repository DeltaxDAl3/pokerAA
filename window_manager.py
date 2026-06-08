"""
window_manager.py
Cross-platform (with strong macOS support) poker client window detection.

Returns a simple object with:
  .title, .left, .top, .width, .height
  .activate() -> None
  .resize_to(width, height) -> None   # best effort

This replaces direct reliance on pygetwindow objects.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class PokerWindow:
    """Simple window handle that works across platforms for this bot."""
    title: str
    left: int
    top: int
    width: int
    height: int

    def activate(self) -> None:
        """Bring the window to the front if possible."""
        system = platform.system()
        if system == "Darwin":
            _macos_activate_window(self.title)
        else:
            # On Windows we could use the original pygetwindow object if needed,
            # but for simplicity we just try to be non-destructive here.
            print("[window_manager] Activate requested (best effort on this platform).")

    def resize_to(self, width: int, height: int) -> None:
        """Best-effort resize. On macOS this is often unreliable -> warn user."""
        system = platform.system()
        if system == "Darwin":
            success = _macos_resize_window(self.title, width, height)
            if not success:
                print(f"[window_manager] Could not auto-resize on macOS. "
                      f"Please manually resize the PokerStars table to ~{width}x{height}.")
            else:
                self.width = width
                self.height = height
        else:
            try:
                # Fallback: if someone passed a real pygetwindow object we could use it,
                # but here we just document the intent.
                print(f"[window_manager] resize_to({width}, {height}) called on non-macOS (best effort).")
            except Exception as e:
                print(f"[window_manager] resize failed: {e}")


def get_poker_window(search_terms=("PokerStars",)) -> Optional[PokerWindow]:
    """
    Main entry point used by main.py.
    Tries to find the active PokerStars cash game table.
    """
    system = platform.system()
    if system == "Darwin":
        return _find_window_macos(search_terms)
    else:
        return _find_window_windows(search_terms)

def get_poker_windows(search_terms=("PokerStars",), max_windows: int = 2) -> list[PokerWindow]:
    """
    Restituisce fino a `max_windows` tavoli PokerStars ordinati con finestra attiva in testa.
    """
    system = platform.system()
    if max_windows <= 0:
        return []
    if system == "Darwin":
        return _find_windows_macos(search_terms=search_terms, max_windows=max_windows)
    single = _find_window_windows(search_terms)
    return [single] if single else []


def take_table_screenshot(window_info: dict, save_path: str = "table_screenshot.png") -> str:
    """Cattura screenshot della regione della tabella e salva su disco"""
    try:
        left = int(window_info.get("left", 0))
        top = int(window_info.get("top", 0))
        width = int(window_info.get("width", 0))
        height = int(window_info.get("height", 0))
    except Exception as e:
        print(f"[window_manager] Screenshot error: window_info non valido ({e})")
        return ""

    if width <= 0 or height <= 0:
        print("[window_manager] Screenshot error: dimensioni finestra non valide.")
        return ""

    abs_path = os.path.abspath(save_path)
    system = platform.system()

    if system == "Darwin":
        try:
            from Quartz import (
                CGWindowListCreateImage,
                CGRectMake,
                kCGNullWindowID,
                kCGWindowImageDefault,
                kCGWindowListOptionOnScreenOnly,
            )
            from AppKit import NSBitmapImageRep, NSPNGFileType
        except Exception as e:
            print(f"[window_manager] Screenshot error: Quartz/AppKit non disponibili ({e})")
            return ""

        try:
            rect = CGRectMake(left, top, width, height)
            image_ref = CGWindowListCreateImage(
                rect,
                kCGWindowListOptionOnScreenOnly,
                kCGNullWindowID,
                kCGWindowImageDefault,
            )
            if not image_ref:
                print("[window_manager] Screenshot error: CGWindowListCreateImage ha restituito None.")
                return ""

            bitmap = NSBitmapImageRep.alloc().initWithCGImage_(image_ref)
            png_data = bitmap.representationUsingType_properties_(NSPNGFileType, None)
            if not png_data:
                print("[window_manager] Screenshot error: conversione PNG fallita.")
                return ""

            ok = png_data.writeToFile_atomically_(abs_path, True)
            if not ok:
                print(f"[window_manager] Screenshot error: salvataggio fallito su {abs_path}")
                return ""

            print(f"[window_manager] Table screenshot salvato: {abs_path}")
            return abs_path
        except Exception as e:
            print(f"[window_manager] Screenshot error (Quartz): {e}")
            return ""

    try:
        import mss
        import mss.tools
        with mss.mss() as sct:
            monitor = {"left": left, "top": top, "width": width, "height": height}
            shot = sct.grab(monitor)
            mss.tools.to_png(shot.rgb, shot.size, output=abs_path)
        print(f"[window_manager] Table screenshot salvato (mss): {abs_path}")
        return abs_path
    except Exception as e:
        print(f"[window_manager] Screenshot error (mss fallback): {e}")
        return ""


_EASYOCR_READER = None
_OCR_ALLOWLIST = "A23456789TJQK10SHDCshdc♠♥♦♣"
_OCR_FAST_MODE = os.getenv("OCR_FAST_MODE", "1").strip().lower() not in ("0", "false", "no", "off")
_OCR_MIN_CONF = 0.52 if _OCR_FAST_MODE else 0.58
_HOLE_CARD_ROIS = [
    # Hero card 1 / 2 calibrated from table relative coordinates.
    (0.430, 0.615, 0.070, 0.110),
    (0.492, 0.615, 0.070, 0.110),
]
_BOARD_CARD_ROIS = [
    (0.334, 0.345, 0.066, 0.105),
    (0.398, 0.345, 0.066, 0.105),
    (0.463, 0.345, 0.066, 0.105),
    (0.526, 0.345, 0.066, 0.105),
    (0.593, 0.345, 0.066, 0.105),
]
_HOLE_CARD_PRE_CROP = (0.00, 0.00, 1.00, 1.00)
_BOARD_CARD_PRE_CROP = (0.00, 0.00, 1.00, 1.00)
_OCR_CACHE_MAX_SIZE = 1600
_OCR_TOKEN_CACHE: dict[tuple[str, int], str] = {}
_RANK_TEMPLATE_CACHE: dict[str, any] = {}
_SUIT_TEMPLATE_CACHE: dict[str, any] = {}


def _get_easyocr_reader():
    global _EASYOCR_READER
    if _EASYOCR_READER is None:
        import easyocr
        _EASYOCR_READER = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _EASYOCR_READER


def initialize_easyocr() -> bool:
    """Inizializza EasyOCR una sola volta all'avvio."""
    try:
        _get_easyocr_reader()
        return True
    except Exception as e:
        print(f"[window_manager] OCR init error: {e}")
        return False


def _extract_card_token(texts) -> str:
    suit_map = {"S": "s", "H": "h", "D": "d", "C": "c", "♠": "s", "♥": "h", "♦": "d", "♣": "c"}
    for raw in texts:
        token = (raw or "").upper().replace(" ", "")
        token = token.replace("10", "T").replace("I", "1").replace("L0", "T")
        token = token.replace("O", "0")
        token = token.replace("♠", "S").replace("♥", "H").replace("♦", "D").replace("♣", "C")

        m = re.search(r"(A|K|Q|J|T|[2-9])([SHDC])", token)
        if m:
            rank = m.group(1)
            suit = suit_map.get(m.group(2), "")
            if suit:
                return f"{rank}{suit}"
    return ""


def _is_valid_card_token(token: str) -> bool:
    if not token or len(token) != 2:
        return False
    rank = token[0].upper()
    suit = token[1].lower()
    return rank in {"A", "K", "Q", "J", "T", "9", "8", "7", "6", "5", "4", "3", "2"} and suit in {"s", "h", "d", "c"}


def _dedupe_valid_cards(cards: list[str]) -> list[str]:
    unique_cards: list[str] = []
    seen: set[str] = set()
    for card in cards:
        normalized = (card or "").strip()
        if not _is_valid_card_token(normalized):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        unique_cards.append(normalized)
    return unique_cards


def _load_card_templates():
    global _RANK_TEMPLATE_CACHE, _SUIT_TEMPLATE_CACHE
    if _RANK_TEMPLATE_CACHE and _SUIT_TEMPLATE_CACHE:
        return _RANK_TEMPLATE_CACHE, _SUIT_TEMPLATE_CACHE
    try:
        import cv2
    except Exception:
        return {}, {}

    images_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images")
    rank_files = {
        "A": "A.png",
        "K": "K.png",
        "Q": "Q.png",
        "J": "J.png",
        "T": "10.png",
        "9": "9.png",
        "8": "8.png",
        "7": "7.png",
        "6": "6.png",
        "5": "5.png",
        "4": "4.png",
        "3": "3.png",
        "2": "2.png",
    }
    suit_files = {
        "s": "Spades.png",
        "h": "Hearts.png",
        "d": "Diamonds.png",
        "c": "Clover.png",
    }

    rank_cache: dict[str, any] = {}
    suit_cache: dict[str, any] = {}
    for rank, file_name in rank_files.items():
        path = os.path.join(images_dir, file_name)
        template = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if template is not None:
            rank_cache[rank] = template
    for suit, file_name in suit_files.items():
        path = os.path.join(images_dir, file_name)
        template = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if template is not None:
            suit_cache[suit] = template

    _RANK_TEMPLATE_CACHE = rank_cache
    _SUIT_TEMPLATE_CACHE = suit_cache
    return _RANK_TEMPLATE_CACHE, _SUIT_TEMPLATE_CACHE


def _template_match_best(region, templates: dict[str, any]) -> tuple[str, float]:
    try:
        import cv2
    except Exception:
        return "", 0.0
    if region is None or getattr(region, "size", 0) == 0:
        return "", 0.0
    best_key = ""
    best_score = 0.0
    for key, template in templates.items():
        if template is None:
            continue
        th, tw = template.shape[:2]
        rh, rw = region.shape[:2]
        if rh < th or rw < tw:
            continue
        result = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        score = float(max_val)
        if score > best_score:
            best_score = score
            best_key = key
    return best_key, best_score


def _extract_card_token_template(card_crop) -> str:
    try:
        import cv2
    except Exception:
        return ""
    if card_crop is None or getattr(card_crop, "size", 0) == 0:
        return ""

    rank_templates, suit_templates = _load_card_templates()
    if not rank_templates or not suit_templates:
        return ""

    gray = cv2.cvtColor(card_crop, cv2.COLOR_BGR2GRAY) if len(card_crop.shape) == 3 else card_crop
    h, w = gray.shape[:2]
    if h <= 0 or w <= 0:
        return ""

    # Rank in upper-left zone; suit just below (classic card glyph placement).
    rank_zone = gray[0:max(1, int(h * 0.62)), 0:max(1, int(w * 0.72))]
    suit_zone = gray[int(h * 0.30):h, 0:max(1, int(w * 0.75))]

    rank, rank_score = _template_match_best(rank_zone, rank_templates)
    suit, suit_score = _template_match_best(suit_zone, suit_templates)

    if rank and suit and rank_score >= 0.55 and suit_score >= 0.50:
        return f"{rank}{suit}"
    return ""


def _extract_card_token_with_conf(results, min_conf: float) -> str:
    suit_map = {"S": "s", "H": "h", "D": "d", "C": "c", "♠": "s", "♥": "h", "♦": "d", "♣": "c"}
    for item in results:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        raw = item[1]
        conf = float(item[2] or 0.0)
        if conf < min_conf:
            continue
        token = (raw or "").upper().replace(" ", "")
        token = token.replace("10", "T").replace("I", "1").replace("L0", "T")
        token = token.replace("O", "0")
        token = token.replace("♠", "S").replace("♥", "H").replace("♦", "D").replace("♣", "C")
        m = re.search(r"(A|K|Q|J|T|[2-9])([SHDC])", token)
        if m:
            rank = m.group(1)
            suit = suit_map.get(m.group(2), "")
            if suit:
                return f"{rank}{suit}"
    return ""


def _crop_card_focus_zone(card_crop, card_type: str):
    if card_crop is None or getattr(card_crop, "size", 0) == 0:
        return card_crop
    h, w = card_crop.shape[:2]
    if h <= 0 or w <= 0:
        return card_crop

    rel_x, rel_y, rel_w, rel_h = _HOLE_CARD_PRE_CROP if card_type == "hole" else _BOARD_CARD_PRE_CROP
    x1 = max(0, int(w * rel_x))
    y1 = max(0, int(h * rel_y))
    x2 = min(w, int(w * (rel_x + rel_w)))
    y2 = min(h, int(h * (rel_y + rel_h)))
    if x2 <= x1 or y2 <= y1:
        return card_crop
    return card_crop[y1:y2, x1:x2]


def _ocr_card_crop(card_crop, card_type: str = "hole") -> str:
    try:
        import cv2
    except Exception:
        return ""

    if card_crop is None or card_crop.size == 0:
        return ""
    focused_crop = _crop_card_focus_zone(card_crop, card_type)
    if focused_crop is None or focused_crop.size == 0:
        return ""

    reader = _get_easyocr_reader()
    gray = cv2.cvtColor(focused_crop, cv2.COLOR_BGR2GRAY) if len(focused_crop.shape) == 3 else focused_crop
    h, w = gray.shape[:2]
    if h <= 0 or w <= 0:
        return ""
    thumb = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    cache_key = (card_type, hash(thumb.tobytes()))
    cached = _OCR_TOKEN_CACHE.get(cache_key)
    if cached is not None:
        return cached

    scale_factor = 2 if _OCR_FAST_MODE else 3
    scaled = cv2.resize(gray, (w * scale_factor, h * scale_factor), interpolation=cv2.INTER_CUBIC)
    blur = cv2.GaussianBlur(scaled, (3, 3), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if _OCR_FAST_MODE:
        attempts = [th, scaled]
    else:
        _, th_inv = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        attempts = [th, th_inv, scaled]
    for img in attempts:
        results = reader.readtext(img, detail=1, paragraph=False, allowlist=_OCR_ALLOWLIST)
        token = _extract_card_token_with_conf(results, _OCR_MIN_CONF)
        if token:
            if len(_OCR_TOKEN_CACHE) >= _OCR_CACHE_MAX_SIZE:
                _OCR_TOKEN_CACHE.pop(next(iter(_OCR_TOKEN_CACHE)))
            _OCR_TOKEN_CACHE[cache_key] = token
            return token
        texts = [r[1] for r in results if isinstance(r, (list, tuple)) and len(r) > 1]
        token = _extract_card_token(texts)
        if token:
            if len(_OCR_TOKEN_CACHE) >= _OCR_CACHE_MAX_SIZE:
                _OCR_TOKEN_CACHE.pop(next(iter(_OCR_TOKEN_CACHE)))
            _OCR_TOKEN_CACHE[cache_key] = token
            return token
    template_token = _extract_card_token_template(focused_crop)
    if template_token:
        if len(_OCR_TOKEN_CACHE) >= _OCR_CACHE_MAX_SIZE:
            _OCR_TOKEN_CACHE.pop(next(iter(_OCR_TOKEN_CACHE)))
        _OCR_TOKEN_CACHE[cache_key] = template_token
        return template_token
    if len(_OCR_TOKEN_CACHE) >= _OCR_CACHE_MAX_SIZE:
        _OCR_TOKEN_CACHE.pop(next(iter(_OCR_TOKEN_CACHE)))
    _OCR_TOKEN_CACHE[cache_key] = ""
    return ""


def read_table_cards_ocr(window_info: dict, screenshot_path: str):
    """
    Legge hole cards e community cards dallo screenshot della tabella.
    Ritorna (hole_cards, board_cards).
    """
    try:
        import cv2
    except Exception as e:
        print(f"[window_manager] OCR error: opencv non disponibile ({e})")
        return [], []

    image = cv2.imread(screenshot_path)
    if image is None:
        print(f"[window_manager] OCR error: screenshot non leggibile ({screenshot_path})")
        return [], []

    img_h, img_w = image.shape[:2]

    def crop_rel(rel_x: float, rel_y: float, rel_w: float, rel_h: float):
        x1 = max(0, int(img_w * rel_x))
        y1 = max(0, int(img_h * rel_y))
        x2 = min(img_w, int(img_w * (rel_x + rel_w)))
        y2 = min(img_h, int(img_h * (rel_y + rel_h)))
        if x2 <= x1 or y2 <= y1:
            return None
        return image[y1:y2, x1:x2]

    hole_rois_primary = _HOLE_CARD_ROIS
    board_rois = _BOARD_CARD_ROIS

    hole_cards = []
    for roi in hole_rois_primary:
        hole_cards.append(_ocr_card_crop(crop_rel(*roi), card_type="hole"))

    for idx, card in enumerate(hole_cards):
        if card:
            continue
        x, y, w, h = hole_rois_primary[idx]
        expanded = (
            max(0.0, x - 0.008),
            max(0.0, y - 0.006),
            min(1.0 - x, w + 0.015),
            min(1.0 - y, h + 0.012),
        )
        hole_cards[idx] = _ocr_card_crop(crop_rel(*expanded), card_type="hole")

    board_cards = []
    for idx, roi in enumerate(board_rois):
        card = _ocr_card_crop(crop_rel(*roi), card_type="board")
        if not card:
            x, y, w, h = board_rois[idx]
            expanded = (
                max(0.0, x - 0.007),
                max(0.0, y - 0.006),
                min(1.0 - x, w + 0.014),
                min(1.0 - y, h + 0.012),
            )
            card = _ocr_card_crop(crop_rel(*expanded), card_type="board")
        if card:
            board_cards.append(card)

    hole_cards_clean = _dedupe_valid_cards(hole_cards[:2])
    board_cards_clean = _dedupe_valid_cards(board_cards[:5])

    # Evita duplicati impossibili tra hero hole e board (tipico errore OCR).
    hole_set = set(hole_cards_clean)
    board_without_hole_dupes = [c for c in board_cards_clean if c not in hole_set]

    return hole_cards_clean[:2], board_without_hole_dupes[:5]


def format_cards_line(hole_cards, board_cards) -> str:
    hole_text = " ".join(hole_cards) if hole_cards else "--"
    board_text = " ".join(board_cards) if board_cards else "--"
    return f"Hole: {hole_text} | Board: {board_text}"

_BUTTON_SPECS = {
    "fold": {
        "center": (0.516, 0.907),
        "region": (0.438, 0.846, 0.154, 0.118),
        "labels": ("fold",),
    },
    "check": {
        "center": (0.679, 0.907),
        "region": (0.600, 0.846, 0.154, 0.118),
        "labels": ("check",),
    },
    "call": {
        "center": (0.679, 0.907),
        "region": (0.600, 0.846, 0.154, 0.118),
        "labels": ("call",),
    },
    "raise": {
        "center": (0.842, 0.907),
        "region": (0.762, 0.846, 0.154, 0.118),
        "labels": ("raise", "allin", "all"),
    },
    "bet": {
        "center": (0.842, 0.907),
        "region": (0.762, 0.846, 0.154, 0.118),
        "labels": ("bet", "allin", "all"),
    },
}
_CLICK_VISIBILITY_RETRIES = 3
_CLICK_ACTION_RETRIES = 3
_CLICK_RETRY_BASE_DELAY = 0.05
_WINDOW_REACTIVATE_MIN_INTERVAL_SEC = 0.30
_LAST_WINDOW_REACTIVATION_TS = 0.0
_CENTER_SLOT_FALLBACK_RETRIES = 2


def _get_window_rect(window_info: dict):
    try:
        left = int(window_info["left"])
        top = int(window_info["top"])
        width = int(window_info["width"])
        height = int(window_info["height"])
    except Exception as e:
        print(f"[window_manager] Click error: window_info non valido ({e})")
        return None
    if width <= 0 or height <= 0:
        print("[window_manager] Click error: dimensioni finestra non valide.")
        return None
    return left, top, width, height

def _best_effort_reactivate_window_for_click(window_info: dict, force: bool = False) -> None:
    global _LAST_WINDOW_REACTIVATION_TS
    if platform.system() != "Darwin":
        return
    title = str((window_info or {}).get("title", "")).strip()
    if not title:
        return
    now = time.time()
    if not force and (now - _LAST_WINDOW_REACTIVATION_TS) < _WINDOW_REACTIVATE_MIN_INTERVAL_SEC:
        return
    _macos_activate_window(title)
    _LAST_WINDOW_REACTIVATION_TS = time.time()



def _relative_region_to_pixels(window_info: dict, rel_region: tuple[float, float, float, float]):
    rect = _get_window_rect(window_info)
    if rect is None:
        return None
    left, top, width, height = rect
    rel_x, rel_y, rel_w, rel_h = rel_region
    x = left + int(width * rel_x)
    y = top + int(height * rel_y)
    w = max(1, int(width * rel_w))
    h = max(1, int(height * rel_h))
    return x, y, w, h


def _capture_button_region(window_info: dict, button_name: str):
    spec = _BUTTON_SPECS.get(button_name)
    if spec is None:
        return None
    pixel_region = _relative_region_to_pixels(window_info, spec["region"])
    if pixel_region is None:
        return None
    try:
        import numpy as np
        import pyautogui
    except Exception:
        return None
    try:
        screenshot = pyautogui.screenshot(region=pixel_region)
        return np.array(screenshot)
    except Exception:
        return None


def _normalize_button_label(raw_text: str) -> str:
    return re.sub(r"[^a-z]", "", (raw_text or "").lower())


def _read_button_label(button_rgb_image) -> str:
    if button_rgb_image is None:
        return ""
    try:
        import cv2
        import pytesseract
    except Exception:
        return ""
    try:
        image = cv2.cvtColor(button_rgb_image, cv2.COLOR_RGB2GRAY)
        image = cv2.resize(image, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        _, threshold = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        text = pytesseract.image_to_string(
            threshold,
            config="--oem 3 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
        )
        return _normalize_button_label(text)
    except Exception:
        return ""


_BET_INPUT_CENTER = (0.630, 0.860)

def _button_pixels_look_visible(button_rgb_image) -> bool:
    if button_rgb_image is None or getattr(button_rgb_image, "size", 0) == 0:
        return False
    img = button_rgb_image
    brightness = (0.2126 * img[:, :, 0]) + (0.7152 * img[:, :, 1]) + (0.0722 * img[:, :, 2])
    mean_brightness = float(brightness.mean())
    bright_ratio = float((brightness > 135).mean())
    white_ratio = float(
        (
            (img[:, :, 0] >= 180)
            & (img[:, :, 1] >= 180)
            & (img[:, :, 2] >= 180)
        ).mean()
    )
    blue_ratio = float(
        (
            (img[:, :, 0] >= 90) & (img[:, :, 0] <= 140)
            & (img[:, :, 1] >= 145) & (img[:, :, 1] <= 220)
            & (img[:, :, 2] >= 170) & (img[:, :, 2] <= 255)
        ).mean()
    )
    return (mean_brightness >= 38.0 and bright_ratio >= 0.05) or white_ratio >= 0.010 or blue_ratio >= 0.016



def _button_region_looks_visible(
    window_info: dict,
    button_name: str,
    retries: int = 1,
    reactivate_on_retry: bool = False,
) -> bool:
    total_attempts = max(1, retries)
    for attempt in range(total_attempts):
        if attempt > 0 and reactivate_on_retry:
            _best_effort_reactivate_window_for_click(window_info)
        button_region = _capture_button_region(window_info, button_name)
        if button_region is not None and _button_pixels_look_visible(button_region):
            return True
        time.sleep(0.03)
    return False

def _button_label_matches(button_name: str, label: str) -> bool:
    spec = _BUTTON_SPECS.get(button_name)
    if spec is None:
        return False
    expected_labels = spec["labels"]
    return any(expected in label for expected in expected_labels)

def _button_is_visible(
    window_info: dict,
    button_name: str,
    retries: int = _CLICK_VISIBILITY_RETRIES,
    reactivate_on_retry: bool = False,
) -> bool:
    spec = _BUTTON_SPECS.get(button_name)
    if spec is None:
        return False
    total_attempts = max(1, retries)
    for attempt in range(total_attempts):
        if attempt > 0 and reactivate_on_retry:
            _best_effort_reactivate_window_for_click(window_info)
        button_region = _capture_button_region(window_info, button_name)
        if button_region is None:
            time.sleep(0.03)
            continue
        if not _button_pixels_look_visible(button_region):
            time.sleep(0.03)
            continue
        label = _read_button_label(button_region)
        if _button_label_matches(button_name, label):
            return True
        if button_name in ("raise", "bet") and ("allin" in label or label == "all"):
            return True
        if not label and button_name in ("fold", "check", "call", "raise", "bet"):
            # In caso OCR testo bottone non affidabile, usiamo fallback su presenza visiva.
            return True
        if button_name in ("check", "call") and ("check" in label or "call" in label):
            return True
        if button_name in ("fold", "check", "call", "raise", "bet") and attempt == total_attempts - 1:
            # Ultimo fallback: quando OCR legge testo sporco ma l'area pulsante è chiaramente visibile.
            return True
        time.sleep(0.03)
    return False


def _click_relative(window_info: dict, rel_x: float, rel_y: float) -> bool:
    try:
        import pyautogui
    except Exception as e:
        print(f"[window_manager] Click error: pyautogui non disponibile ({e})")
        return False
    rect = _get_window_rect(window_info)
    if rect is None:
        return False
    left, top, width, height = rect
    x = left + int(width * rel_x)
    y = top + int(height * rel_y)
    try:
        pyautogui.moveTo(x, y, duration=0.05)
        pyautogui.click(x=x, y=y, button="left")
        return True
    except Exception as e:
        print(f"[window_manager] Click error: {e}")
        return False


def _click_button_once_without_visibility_check(window_info: dict, button_name: str) -> bool:
    spec = _BUTTON_SPECS.get(button_name)
    if spec is None:
        print(f"[window_manager] Click error: bottone '{button_name}' non riconosciuto.")
        return False
    rel_x, rel_y = spec["center"]
    return _click_relative(window_info, rel_x, rel_y)


def _click_button_once(window_info: dict, button_name: str) -> bool:
    spec = _BUTTON_SPECS.get(button_name)
    if spec is None:
        print(f"[window_manager] Click error: bottone '{button_name}' non riconosciuto.")
        return False
    if not _button_is_visible(window_info, button_name, retries=2, reactivate_on_retry=True):
        print(f"[window_manager] Click annullato: bottone '{button_name}' non visibile.")
        return False
    return _click_button_once_without_visibility_check(window_info, button_name)


def _resolve_center_button_name(
    window_info: dict,
    preferred_button: str,
    retries: int = 1,
    reactivate_on_retry: bool = False,
) -> str:
    alternate_button = "call" if preferred_button == "check" else "check"
    if _button_is_visible(
        window_info,
        preferred_button,
        retries=retries,
        reactivate_on_retry=reactivate_on_retry,
    ):
        return preferred_button
    if _button_is_visible(
        window_info,
        alternate_button,
        retries=retries,
        reactivate_on_retry=reactivate_on_retry,
    ):
        return alternate_button
    if _button_region_looks_visible(window_info, preferred_button, retries=1):
        return preferred_button
    if _button_region_looks_visible(window_info, alternate_button, retries=1):
        return alternate_button
    return ""


def _center_slot_looks_clickable(
    window_info: dict,
    retries: int = _CENTER_SLOT_FALLBACK_RETRIES,
) -> bool:
    if _button_region_looks_visible(window_info, "call", retries=retries, reactivate_on_retry=True):
        return True
    if _button_region_looks_visible(window_info, "check", retries=retries, reactivate_on_retry=True):
        return True
    if _button_is_visible(window_info, "call", retries=1, reactivate_on_retry=True):
        return True
    if _button_is_visible(window_info, "check", retries=1, reactivate_on_retry=True):
        return True
    return False


def _click_center_slot_fallback(window_info: dict, preferred_button: str = "check") -> bool:
    target = "call" if preferred_button == "call" else "check"
    if not _center_slot_looks_clickable(window_info):
        return False
    return _click_button_once_without_visibility_check(window_info, target)


def _resolve_right_button_name(
    window_info: dict,
    preferred_button: str,
    retries: int = 1,
    reactivate_on_retry: bool = False,
) -> str:
    alternate_button = "bet" if preferred_button == "raise" else "raise"
    if _button_is_visible(
        window_info,
        preferred_button,
        retries=retries,
        reactivate_on_retry=reactivate_on_retry,
    ):
        return preferred_button
    if _button_is_visible(
        window_info,
        alternate_button,
        retries=retries,
        reactivate_on_retry=reactivate_on_retry,
    ):
        return alternate_button
    if _button_region_looks_visible(window_info, preferred_button, retries=1):
        return preferred_button
    if _button_region_looks_visible(window_info, alternate_button, retries=1):
        return alternate_button
    return ""


def _sleep_for_click_retry(attempt: int) -> None:
    time.sleep(_CLICK_RETRY_BASE_DELAY + (attempt * 0.03))


def _click_right_action_with_amount(
    window_info: dict,
    preferred_button: str,
    amount: float,
    action_label: str,
) -> bool:
    last_error = f"bottone '{preferred_button}' non visibile"
    for attempt in range(_CLICK_ACTION_RETRIES):
        if attempt > 0:
            _best_effort_reactivate_window_for_click(window_info, force=True)
            _sleep_for_click_retry(attempt)
        if not hero_action_buttons_ready(window_info):
            last_error = "layer azioni hero non pronto"
            continue
        target_button = _resolve_right_button_name(window_info, preferred_button, retries=1)
        if not target_button:
            last_error = f"bottone '{preferred_button}' non visibile"
            continue
        if not _type_bet_amount(window_info, amount):
            last_error = "input amount fallito"
            continue
        time.sleep(0.04)
        target_after_input = _resolve_right_button_name(
            window_info,
            target_button,
            retries=2,
            reactivate_on_retry=True,
        )
        if not target_after_input:
            alternate_after_input = "bet" if target_button == "raise" else "raise"
            right_slot_visible = (
                _button_region_looks_visible(window_info, target_button, retries=1)
                or _button_region_looks_visible(window_info, alternate_after_input, retries=1)
            )
            if right_slot_visible and _click_button_once_without_visibility_check(window_info, target_button):
                return True
            last_error = f"bottone '{target_button}' non più visibile dopo input amount"
            continue
        if _click_button_once_without_visibility_check(window_info, target_after_input):
            return True
        last_error = f"click su bottone '{target_after_input}' fallito"

    if click_call(window_info, quiet=True):
        print(f"[window_manager] {action_label} fallback: bottone destro instabile, eseguito bottone centrale.")
        return True
    if _click_center_slot_fallback(window_info, preferred_button="check"):
        print(f"[window_manager] {action_label} fallback: bottone destro instabile, eseguito click centrale blind.")
        return True
    print(f"[window_manager] {action_label} annullato: {last_error}.")
    return False


def hero_action_buttons_ready(window_info: dict) -> bool:
    """
    True quando il layer azioni hero risulta realmente attivo (fold o check/call visibili).
    """
    center_ready = (
        _button_region_looks_visible(window_info, "check", retries=1)
        or _button_region_looks_visible(window_info, "call", retries=1)
    )
    fold_ready = _button_region_looks_visible(window_info, "fold", retries=1)
    right_ready = (
        _button_region_looks_visible(window_info, "raise", retries=1)
        or _button_region_looks_visible(window_info, "bet", retries=1)
    )
    if center_ready and (fold_ready or right_ready):
        return True
    return (
        _button_is_visible(window_info, "fold", retries=1)
        or _button_is_visible(window_info, "check", retries=1)
        or _button_is_visible(window_info, "call", retries=1)
    )


def click_fold(window_info: dict, quiet: bool = False) -> bool:
    for attempt in range(_CLICK_ACTION_RETRIES):
        if attempt > 0:
            _best_effort_reactivate_window_for_click(window_info, force=True)
            _sleep_for_click_retry(attempt)
        if _button_is_visible(window_info, "fold", retries=1, reactivate_on_retry=True):
            return _click_button_once_without_visibility_check(window_info, "fold")
        if _click_center_slot_fallback(window_info, preferred_button="check"):
            if not quiet:
                print("[window_manager] Fold fallback: bottone 'fold' non visibile, eseguito bottone centrale.")
            return True
    if not quiet:
        print("[window_manager] Click annullato: bottone 'fold' non visibile.")
    return False


def click_check(window_info: dict, quiet: bool = False) -> bool:
    for attempt in range(_CLICK_ACTION_RETRIES):
        if attempt > 0:
            _best_effort_reactivate_window_for_click(window_info, force=True)
            _sleep_for_click_retry(attempt)
        target_button = _resolve_center_button_name(window_info, preferred_button="check", retries=1)
        if target_button:
            # Check/Call condividono lo stesso bottone centrale.
            return _click_button_once_without_visibility_check(window_info, target_button)
    if _click_center_slot_fallback(window_info, preferred_button="check"):
        if not quiet:
            print("[window_manager] Check fallback: bottone centrale stimato, eseguito click blind.")
        return True
    if not quiet:
        print("[window_manager] Click annullato: bottone 'check' non visibile.")
    return False


def click_call(window_info: dict, quiet: bool = False) -> bool:
    for attempt in range(_CLICK_ACTION_RETRIES):
        if attempt > 0:
            _best_effort_reactivate_window_for_click(window_info, force=True)
            _sleep_for_click_retry(attempt)
        target_button = _resolve_center_button_name(window_info, preferred_button="call", retries=1)
        if target_button:
            # Call/Check condividono lo stesso bottone centrale.
            return _click_button_once_without_visibility_check(window_info, target_button)
    if _click_center_slot_fallback(window_info, preferred_button="call"):
        if not quiet:
            print("[window_manager] Call fallback: bottone centrale stimato, eseguito click blind.")
        return True
    if not quiet:
        print("[window_manager] Click annullato: bottone 'call' non visibile.")
    return False


def fold(window_info: dict) -> bool:
    return click_fold(window_info)


def call(window_info: dict) -> bool:
    return click_call(window_info)


def check(window_info: dict) -> bool:
    return click_check(window_info)


def _type_bet_amount(window_info: dict, amount: float) -> bool:
    try:
        import pyautogui
    except Exception as e:
        print(f"[window_manager] Bet sizing error: pyautogui non disponibile ({e})")
        return False
    rect = _get_window_rect(window_info)
    if rect is None:
        return False
    left, top, width, height = rect
    amount_str = str(round(float(amount), 2)).rstrip("0").rstrip(".")
    input_rel_x, input_rel_y = _BET_INPUT_CENTER
    input_x = left + int(width * input_rel_x)
    input_y = top + int(height * input_rel_y)
    select_all_modifier = "command" if platform.system() == "Darwin" else "ctrl"
    for attempt in range(2):
        if attempt > 0:
            _best_effort_reactivate_window_for_click(window_info, force=True)
            time.sleep(0.05)
        try:
            pyautogui.click(input_x, input_y)
            time.sleep(0.04)
            pyautogui.hotkey(select_all_modifier, "a")
            time.sleep(0.02)
            pyautogui.write(amount_str, interval=0.01)
            time.sleep(0.03)
            return True
        except Exception as e:
            if attempt == 1:
                print(f"[window_manager] Bet sizing error: {e}")
    return False


def click_raise(window_info: dict, amount: float) -> bool:
    return _click_right_action_with_amount(window_info, preferred_button="raise", amount=amount, action_label="Raise")


def click_bet(window_info: dict, amount: float) -> bool:
    return _click_right_action_with_amount(window_info, preferred_button="bet", amount=amount, action_label="Bet")


def raise_(window_info: dict, amount: float) -> bool:
    return raise_to(window_info, amount)


def raise_to(window_info: dict, amount: float) -> bool:
    return click_raise(window_info, amount)


def bet(window_info: dict, amount: float) -> bool:
    return bet_to(window_info, amount)


def bet_to(window_info: dict, amount: float) -> bool:
    return click_bet(window_info, amount)


# ---------------- macOS implementation (Quartz + AppKit / osascript) ----------------

def _score_poker_window(win: PokerWindow) -> int:
    s = 0
    t = win.title.lower()
    w = win.width
    h = win.height
    s += min(w, 1200) // 80
    s += min(h, 800) // 60
    if any(word in t for word in ["zoom", "bb", "poker", "tavolo", "table", "limit", "hold", "usd", "money", "$", "€"]):
        s += 18
    if 650 < w < 1000 and 500 < h < 760:
        s += 22
    if w < 400 or h < 300:
        s = -100
    return s


def _find_windows_macos(search_terms=("PokerStars",), max_windows: int = 2) -> list[PokerWindow]:
    try:
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGWindowListOptionOnScreenOnly,
            kCGNullWindowID,
        )
    except Exception as e:
        print(f"[macOS] Quartz not available: {e}")
        return []

    try:
        window_list = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID) or []
    except Exception as e:
        print(f"[macOS] Failed to list windows via Quartz: {e}")
        return []

    candidates: list[tuple[int, int, PokerWindow]] = []
    for z_order, w in enumerate(window_list):
        owner = (w.get("kCGWindowOwnerName") or "").lower()
        name = (w.get("kCGWindowName") or "").lower()
        bounds = w.get("kCGWindowBounds") or {}
        title = w.get("kCGWindowName") or w.get("kCGWindowOwnerName") or "Unknown"

        text = f"{owner} {name}"
        is_pokerstars_window = "pokerstars" in text and any(term.lower() in text for term in search_terms)
        if not is_pokerstars_window:
            continue

        try:
            x = int(bounds.get("X", 0))
            y = int(bounds.get("Y", 0))
            wdt = int(bounds.get("Width", 0))
            hgt = int(bounds.get("Height", 0))
        except Exception:
            continue

        # Accept any PokerStars window above this baseline size.
        if wdt <= 600 or hgt <= 400:
            continue
        candidate = PokerWindow(title=title, left=x, top=y, width=wdt, height=hgt)
        candidates.append((_score_poker_window(candidate), z_order, candidate))

    if not candidates:
        print("[macOS] No suitable PokerStars window (>600x400) found via Quartz.")
        return []

    # prima prendiamo i tavoli migliori per qualità, poi ordiniamo per z-order (attiva in testa)
    by_quality = sorted(candidates, key=lambda item: item[0], reverse=True)
    top_n = by_quality[: max(1, max_windows)]
    ordered_active_first = sorted(top_n, key=lambda item: item[1])
    windows = [item[2] for item in ordered_active_first]
    debug_windows = ", ".join(
        [f"'{w.title}'({w.width}x{w.height})" for w in windows]
    )
    print(f"[macOS] Poker windows selected: {debug_windows}")
    return windows


def _find_window_macos(search_terms):
    windows = _find_windows_macos(search_terms=search_terms, max_windows=1)
    return windows[0] if windows else None


def _macos_activate_window(title_hint: str) -> None:
    """Bring PokerStars and the specific table window to the front (best effort)."""
    try:
        safe_title = _escape_applescript_string(title_hint or "")
        script = f'''
        set targetTitle to "{safe_title}"
        tell application "System Events"
            if not (exists process "PokerStars") then
                return "NOT_RUNNING"
            end if
            tell process "PokerStars"
                set frontmost to true
                set matchedWindow to missing value
                repeat with w in windows
                    try
                        if (name of w) is equal to targetTitle then
                            set matchedWindow to w
                            exit repeat
                        end if
                    end try
                end repeat
                if matchedWindow is missing value then
                    repeat with w in windows
                        try
                            if targetTitle is not "" and ((name of w) contains targetTitle) then
                                set matchedWindow to w
                                exit repeat
                            end if
                        end try
                    end repeat
                end if
                if matchedWindow is missing value then
                    try
                        set matchedWindow to front window
                    end try
                end if
                if matchedWindow is not missing value then
                    try
                        perform action "AXRaise" of matchedWindow
                    end try
                    try
                        set value of attribute "AXMain" of matchedWindow to true
                    end try
                    try
                        set value of attribute "AXFocused" of matchedWindow to true
                    end try
                    return "OK"
                end if
                return "WINDOW_NOT_FOUND"
            end tell
        end tell
        '''
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=4
        )
        output = (result.stdout or "").strip().upper()
        if "NOT_RUNNING" in output:
            print("[macOS] PokerStars non risulta in esecuzione.")
            return
        if "WINDOW_NOT_FOUND" in output:
            print(f"[macOS] Finestra non trovata per titolo: '{title_hint}'.")
            return
        time.sleep(0.25)
    except Exception as e:
        print(f"[macOS] activate warning: {e}")


def _escape_applescript_string(value: str) -> str:
    """Escape string content for safe interpolation inside AppleScript quotes."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _macos_resize_window(title_hint: str, width: int, height: int) -> bool:
    """Try to resize via AppleScript (works for some windows)."""
    try:
        # This is fragile but better than nothing. Many users will size manually.
        script = f'''
        tell application "System Events"
            tell process "PokerStars"
                try
                    set size of front window to {{{width}, {height}}}
                    return "ok"
                on error
                    return "fail"
                end try
            end tell
        end tell
        '''
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=4
        )
        return "ok" in result.stdout.lower()
    except Exception as e:
        print(f"[macOS] resize attempt failed: {e}")
        return False


# ---------------- Windows fallback (keeps old behavior) ----------------

def _find_window_windows(search_terms):
    """Original logic using pygetwindow, with the existing FallbackWindow shim."""
    try:
        import pygetwindow as gw
    except ImportError:
        print("[Windows] pygetwindow not installed.")
        return None

    def _get_wins():
        if hasattr(gw, "getWindowsWithTitle"):
            return gw.getWindowsWithTitle("No Limit")
        # Fallback path from original main.py
        wins = []
        for title in gw.getAllTitles():
            if "No Limit" in title:
                try:
                    left, top, w, h = gw.getWindowGeometry(title)
                    class Fallback:
                        def __init__(self):
                            self.title = title
                            self.left = int(left)
                            self.top = int(top)
                            self.width = int(w)
                            self.height = int(h)
                        def activate(self): pass
                        def resizeTo(self, ww, hh):
                            self.width = int(ww); self.height = int(hh)
                    wins.append(Fallback())
                except Exception:
                    continue
        return wins

    try:
        windows = _get_wins()
        for window in windows:
            t = (getattr(window, "title", "") or "").lower()
            if any(term.lower() in t for term in search_terms):
                if "usd" in t or "money" in t or "$" in t:
                    print(f"[Windows] Poker client window found: {window.title} "
                          f"Size: {window.width}x{window.height}")
                    try:
                        window.resizeTo(963, 692)
                    except Exception as e:
                        print(f"[Windows] resize warning: {e}")
                    return PokerWindow(
                        title=getattr(window, "title", "PokerStars"),
                        left=int(getattr(window, "left", 0)),
                        top=int(getattr(window, "top", 0)),
                        width=963,
                        height=692,
                    )
    except Exception as e:
        print(f"[Windows] Window detection error: {e}")

    print("[Windows] Poker client window NOT found.")
    return None


def find_and_activate_best_poker_window() -> Optional[dict]:
    """Trova la finestra migliore, la attiva e restituisce le sue info"""
    best_window = get_poker_window(search_terms=("PokerStars",))
    if best_window is None:
        return None
    best_window.activate()
    print(f"[window_manager] Activated best PokerStars candidate: '{best_window.title}'")
    return {
        "title": best_window.title,
        "left": best_window.left,
        "top": best_window.top,
        "width": best_window.width,
        "height": best_window.height,
    }


def find_and_activate_poker_windows(max_windows: int = 2) -> list[dict]:
    """
    Trova fino a `max_windows` tavoli PokerStars e marca la finestra frontmost come attiva.
    Restituisce lista ordinata con la finestra attiva al primo posto.
    """
    windows = get_poker_windows(search_terms=("PokerStars",), max_windows=max_windows)
    if not windows:
        return []

    # La prima è la frontmost selezionata da _find_windows_macos; la attiviamo esplicitamente.
    try:
        windows[0].activate()
    except Exception:
        pass

    result = []
    for idx, win in enumerate(windows):
        result.append(
            {
                "title": win.title,
                "left": win.left,
                "top": win.top,
                "width": win.width,
                "height": win.height,
                "is_active": idx == 0,
            }
        )
    return result
