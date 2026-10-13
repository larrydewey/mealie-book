#!/usr/bin/env python3
"""
Mealie Recipe → Beautiful Classic Recipe-Card PDF Exporter
==========================================================
Single-file Python script that exports recipes from a Mealie instance
into a professionally designed Letter-size PDF cookbook.

Features
--------
• Classic recipe-card aesthetic (clean borders, elegant typography, fixed-size images)
• One recipe per logical page (multi-page continuation when content is long)
• Title, main image (if present), description, servings/yield, ingredients,
  instructions, and notes
• Export ALL recipes or select individual ones by name/slug
• Interactive menu or pure command-line mode
• Progress reporting, temporary image caching, graceful error handling

Requirements
------------
  pip install requests reportlab pillow

Usage examples
--------------
  # Interactive mode (recommended)
  python mealie_recipe_pdf_export.py

  # Export everything non-interactively
  python mealie_recipe_pdf_export.py --all --output my_cookbook.pdf

  # Export a few specific recipes
  python mealie_recipe_pdf_export.py --slugs "warm-spiced-milk,homemade-mayo-1"

  # Override connection details
  python mealie_recipe_pdf_export.py --url https://... --token YOUR_TOKEN --all
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
)

# ---------------------------------------------------------------------------
# Configuration – edit these defaults or pass via CLI / environment
# ---------------------------------------------------------------------------
DEFAULT_BASE_URL = ""  # set via --url or MEALIE_URL
DEFAULT_TOKEN = ""  # set via --token or MEALIE_TOKEN
DEFAULT_OUTPUT = "mealie_cookbook.pdf"

# Visual design constants (classic recipe-card)
PAGE_WIDTH, PAGE_HEIGHT = letter
MARGIN = 0.55 * inch
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
IMAGE_MAX_WIDTH = 4.2 * inch          # fixed cohesive size
IMAGE_MAX_HEIGHT = 3.0 * inch
CARD_BORDER_COLOR = colors.Color(0.55, 0.45, 0.35)   # warm brown
ACCENT_COLOR = colors.Color(0.45, 0.32, 0.22)
LIGHT_BG = colors.Color(0.98, 0.96, 0.93)             # parchment-ish
HEADER_BG = colors.Color(0.93, 0.88, 0.80)

# ---------------------------------------------------------------------------
# Font registration (DejaVu – excellent Unicode coverage, professional look)
# Falls back cleanly to Helvetica if DejaVu is not installed on the system.
# ---------------------------------------------------------------------------
_FONTS_AVAILABLE = False

def register_fonts() -> bool:
    """Register DejaVu fonts and their family mappings. Returns True on success."""
    global _FONTS_AVAILABLE

    # Common locations on Linux / macOS / Windows
    candidates = [
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/TTF",
        "/usr/local/share/fonts",
        "/Library/Fonts",
        os.path.expanduser("~/.fonts"),
        os.path.expanduser("~/.local/share/fonts"),
        "C:/Windows/Fonts",
    ]

    dejavu_dir = None
    for d in candidates:
        if os.path.isfile(os.path.join(d, "DejaVuSerif.ttf")):
            dejavu_dir = d
            break

    if not dejavu_dir:
        print("Note: DejaVu fonts not found – falling back to Helvetica.")
        return False

    try:
        # Register individual faces
        pdfmetrics.registerFont(TTFont("DejaVuSerif",       os.path.join(dejavu_dir, "DejaVuSerif.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVuSerif-Bold",  os.path.join(dejavu_dir, "DejaVuSerif-Bold.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVuSerif-Italic",os.path.join(dejavu_dir, "DejaVuSerif-Italic.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVuSerif-BoldItalic",
                                       os.path.join(dejavu_dir, "DejaVuSerif-BoldItalic.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVuSans",        os.path.join(dejavu_dir, "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVuSans-Bold",   os.path.join(dejavu_dir, "DejaVuSans-Bold.ttf")))

        # Critical: tell ReportLab how the family maps to bold/italic variants
        pdfmetrics.registerFontFamily(
            "DejaVuSerif",
            normal="DejaVuSerif",
            bold="DejaVuSerif-Bold",
            italic="DejaVuSerif-Italic",
            boldItalic="DejaVuSerif-BoldItalic",
        )
        pdfmetrics.registerFontFamily(
            "DejaVuSans",
            normal="DejaVuSans",
            bold="DejaVuSans-Bold",
            italic="DejaVuSans",          # no dedicated italic needed for body
            boldItalic="DejaVuSans-Bold",
        )
        _FONTS_AVAILABLE = True
        return True
    except Exception as e:
        print(f"Warning: could not register DejaVu fonts ({e}) – using Helvetica.")
        return False


register_fonts()


def get_styles() -> Dict[str, ParagraphStyle]:
    """Return paragraph styles. Uses DejaVu when available, otherwise Helvetica."""
    styles = getSampleStyleSheet()

    if _FONTS_AVAILABLE:
        serif      = "DejaVuSerif"
        serif_b    = "DejaVuSerif-Bold"
        serif_i    = "DejaVuSerif-Italic"
        sans       = "DejaVuSans"
        sans_b     = "DejaVuSans-Bold"
    else:
        serif = serif_b = serif_i = "Times-Roman"
        # Times does not have a clean bold in the built-in set for all cases,
        # so we fall back to Helvetica for most body text.
        sans = "Helvetica"
        sans_b = "Helvetica-Bold"
        serif_b = "Times-Bold"
        serif_i = "Times-Italic"

    custom = {
        "Title": ParagraphStyle(
            "RecipeTitle",
            parent=styles["Title"],
            fontName=serif_b,
            fontSize=18,
            leading=22,
            alignment=TA_CENTER,
            textColor=ACCENT_COLOR,
            spaceAfter=6,
        ),
        "Meta": ParagraphStyle(
            "Meta",
            fontName=sans,
            fontSize=9,
            leading=12,
            alignment=TA_CENTER,
            textColor=colors.Color(0.35, 0.35, 0.35),
            spaceAfter=4,
        ),
        "Description": ParagraphStyle(
            "Description",
            fontName=serif_i,
            fontSize=9.5,
            leading=13,
            alignment=TA_CENTER,
            textColor=colors.Color(0.25, 0.25, 0.25),
            spaceBefore=2,
            spaceAfter=8,
        ),
        "SectionHeader": ParagraphStyle(
            "SectionHeader",
            fontName=serif_b,
            fontSize=11,
            leading=14,
            textColor=ACCENT_COLOR,
            spaceBefore=10,
            spaceAfter=4,
            borderPadding=2,
        ),
        "Ingredient": ParagraphStyle(
            "Ingredient",
            fontName=sans,
            fontSize=9,
            leading=12,
            leftIndent=4,
            textColor=colors.black,
        ),
        "Instruction": ParagraphStyle(
            "Instruction",
            fontName=sans,
            fontSize=9,
            leading=12.5,
            alignment=TA_JUSTIFY,
            textColor=colors.black,
            spaceAfter=4,
        ),
        "Note": ParagraphStyle(
            "Note",
            fontName=serif_i,
            fontSize=8.5,
            leading=11,
            textColor=colors.Color(0.3, 0.3, 0.3),
            leftIndent=6,
            spaceAfter=3,
        ),
        "Footer": ParagraphStyle(
            "Footer",
            fontName=sans,
            fontSize=7.5,
            alignment=TA_CENTER,
            textColor=colors.Color(0.5, 0.5, 0.5),
        ),
        "Continuation": ParagraphStyle(
            "Continuation",
            fontName=serif_i,
            fontSize=8,
            alignment=TA_CENTER,
            textColor=colors.Color(0.45, 0.45, 0.45),
            spaceAfter=6,
        ),
    }
    return custom


# ---------------------------------------------------------------------------
# Mealie API client
# ---------------------------------------------------------------------------
class MealieClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
        )
        self.session.timeout = 30

    def test_connection(self) -> bool:
        try:
            r = self.session.get(f"{self.base_url}/api/recipes?page=1&perPage=1")
            r.raise_for_status()
            return True
        except Exception as e:
            print(f"Connection failed: {e}", file=sys.stderr)
            return False

    def get_all_recipe_summaries(self) -> List[Dict[str, Any]]:
        """Fetch every recipe summary in one call (perPage=-1)."""
        r = self.session.get(f"{self.base_url}/api/recipes?page=1&perPage=-1")
        r.raise_for_status()
        data = r.json()
        return data.get("items", data.get("data", []))

    def get_recipe(self, slug_or_id: str) -> Optional[Dict[str, Any]]:
        r = self.session.get(f"{self.base_url}/api/recipes/{slug_or_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def download_image(self, recipe_id: str, dest: Path) -> bool:
        """Download the main recipe image (original.webp). Returns True on success."""
        url = f"{self.base_url}/api/media/recipes/{recipe_id}/images/original.webp"
        try:
            r = self.session.get(url, stream=True, timeout=45)
            if r.status_code != 200:
                # try jpeg fallback
                url = f"{self.base_url}/api/media/recipes/{recipe_id}/images/original.jpg"
                r = self.session.get(url, stream=True, timeout=45)
            if r.status_code != 200:
                return False
            # Even if Content-Type is wrong (some reverse proxies), trust the body
            content = r.content
            if len(content) < 100:
                return False
            dest.write_bytes(content)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------
class RecipeCardBorder(Flowable):
    """Draws a subtle classic border around the content area of a page."""

    def __init__(self, width: float, height: float):
        super().__init__()
        self.width = width
        self.height = height

    def draw(self):
        self.canv.setStrokeColor(CARD_BORDER_COLOR)
        self.canv.setLineWidth(1.4)
        self.canv.rect(0, 0, self.width, self.height)
        # inner thin line for classic double-border feel
        self.canv.setLineWidth(0.5)
        inset = 4
        self.canv.rect(inset, inset, self.width - 2 * inset, self.height - 2 * inset)


def make_image_flowable(image_path: Path, max_w: float, max_h: float) -> Optional[Image]:
    try:
        with PILImage.open(image_path) as im:
            im = im.convert("RGB")  # normalize
            # Save as temporary JPEG for reliable ReportLab handling
            tmp = image_path.with_suffix(".jpg")
            im.save(tmp, "JPEG", quality=88, optimize=True)
            w, h = im.size
            aspect = h / float(w)
            display_w = max_w
            display_h = display_w * aspect
            if display_h > max_h:
                display_h = max_h
                display_w = display_h / aspect
            return Image(str(tmp), width=display_w, height=display_h)
    except Exception as e:
        print(f"  ⚠ Could not process image {image_path.name}: {e}")
        return None


# ---------------------------------------------------------------------------
# Aggressive text normalizer – guarantees every character is printable.
# ALL fractions (both constructed super/sub AND proper Unicode vulgar
# fractions) are converted to plain ASCII so we never depend on glyphs
# that some PDF font subsets lack.
# ---------------------------------------------------------------------------
import unicodedata

# Proper Unicode vulgar fractions → ASCII
_VULGAR_FRACTIONS = str.maketrans({
    "½": "1/2",   # U+00BD
    "⅓": "1/3",   # U+2153
    "⅔": "2/3",   # U+2154
    "¼": "1/4",   # U+00BC
    "¾": "3/4",   # U+00BE
    "⅕": "1/5",   # U+2155
    "⅖": "2/5",   # U+2156
    "⅗": "3/5",   # U+2157
    "⅘": "4/5",   # U+2158
    "⅙": "1/6",   # U+2159
    "⅚": "5/6",   # U+215A
    "⅐": "1/7",   # U+2150
    "⅛": "1/8",   # U+215B
    "⅜": "3/8",   # U+215C
    "⅝": "5/8",   # U+215D
    "⅞": "7/8",   # U+215E
    "⅑": "1/9",   # U+2151
    "⅒": "1/10",  # U+2152
    "⅟": "1/",    # U+215F
})

# Constructed super/subscript fraction strings (longest first)
_FRACTION_PATTERNS = [
    ("²¹/₃₂", "2 1/2"),
    ("¹/₃₂", "1/32"), ("³/₃₂", "3/32"), ("⁵/₃₂", "5/32"), ("⁷/₃₂", "7/32"),
    ("⁹/₃₂", "9/32"), ("¹¹/₃₂", "11/32"), ("¹³/₃₂", "13/32"), ("¹⁵/₃₂", "15/32"),
    ("¹⁷/₃₂", "17/32"), ("¹⁹/₃₂", "19/32"), ("²¹/₃₂", "21/32"), ("²³/₃₂", "23/32"),
    ("²⁵/₃₂", "25/32"), ("²⁷/₃₂", "27/32"), ("²⁹/₃₂", "29/32"), ("³¹/₃₂", "31/32"),
    ("¹/₂", "1/2"), ("¹/₃", "1/3"), ("²/₃", "2/3"),
    ("¹/₄", "1/4"), ("³/₄", "3/4"),
    ("¹/₅", "1/5"), ("²/₅", "2/5"), ("³/₅", "3/5"), ("⁴/₅", "4/5"),
    ("¹/₆", "1/6"), ("⁵/₆", "5/6"),
    ("¹/₈", "1/8"), ("³/₈", "3/8"), ("⁵/₈", "5/8"), ("⁷/₈", "7/8"),
    ("¹/₁₆", "1/16"), ("³/₁₆", "3/16"), ("⁵/₁₆", "5/16"), ("⁷/₁₆", "7/16"),
    ("⁹/₁₆", "9/16"), ("¹¹/₁₆", "11/16"), ("¹³/₁₆", "13/16"), ("¹⁵/₁₆", "15/16"),
]

# Super/sub digits + fraction slash → normal
_SUPER_SUB = str.maketrans({
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
    "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
    "⁄": "/",
})


def _is_safe_char(ch: str) -> bool:
    """Characters that are guaranteed present in Helvetica / DejaVu basic sets."""
    if ch in "\n\r\t ":
        return True
    o = ord(ch)
    # Basic printable ASCII
    if 0x20 <= o <= 0x7E:
        return True
    # A few very common Latin-1 symbols that almost every font has
    if ch in "°±×÷":
        return True
    # Common typographic punctuation we keep
    if ch in "–—‘’“”…•·":
        return True
    return False


def normalize_text(text: str) -> str:
    """
    Guarantee every character is printable in the PDF.
    Order of operations:
      1. Proper Unicode vulgar fractions  →  ASCII (1/2, 1/3, 1/8 …)
      2. Constructed super/sub fractions  →  ASCII
      3. Leftover super/sub digits        →  normal digits
      4. Anything still unsafe            →  NFKC or '?'
    """
    if not text:
        return text

    # 1. Proper vulgar fraction characters
    text = text.translate(_VULGAR_FRACTIONS)

    # 2. Constructed fraction strings
    for bad, good in _FRACTION_PATTERNS:
        if bad in text:
            text = text.replace(bad, good)

    # 3. Remaining super/sub digits
    text = text.translate(_SUPER_SUB)

    # 4. Final safety net – never emit a box
    cleaned = []
    for ch in text:
        if _is_safe_char(ch):
            cleaned.append(ch)
        else:
            norm = unicodedata.normalize("NFKC", ch)
            if norm and all(_is_safe_char(c) for c in norm):
                cleaned.append(norm)
            else:
                cleaned.append("?")
    return "".join(cleaned)


def format_ingredient(ing: Dict[str, Any]) -> str:
    """Prefer the ready-made 'display' field; fall back to constructing one."""
    display = (ing.get("display") or "").strip()
    if display:
        return normalize_text(display)
    # fallback construction
    qty = ing.get("quantity")
    unit = (ing.get("unit") or {}).get("name") or ""
    food = (ing.get("food") or {}).get("name") or ""
    note = (ing.get("note") or "").strip()
    parts = []
    if qty is not None and qty != 0:
        # nice fraction display if possible
        if isinstance(qty, float) and qty == int(qty):
            parts.append(str(int(qty)))
        else:
            parts.append(str(qty))
    if unit:
        parts.append(unit)
    if food:
        parts.append(food)
    text = " ".join(parts)
    if note:
        text = f"{text} ({note})" if text else note
    return normalize_text(text) or "—"


def build_recipe_story(
    recipe: Dict[str, Any],
    styles: Dict[str, ParagraphStyle],
    image_path: Optional[Path],
    is_continuation: bool = False,
) -> List[Any]:
    """Build the flowables for one recipe (may span multiple pages)."""
    story: List[Any] = []

    name = normalize_text(recipe.get("name") or "Untitled Recipe")
    if is_continuation:
        story.append(Paragraph(f"{name}  (continued)", styles["Continuation"]))
        story.append(Spacer(1, 4))
    else:
        # Title
        story.append(Paragraph(name, styles["Title"]))
        story.append(
            HRFlowable(
                width="60%",
                thickness=1,
                color=CARD_BORDER_COLOR,
                spaceBefore=2,
                spaceAfter=6,
                hAlign="CENTER",
            )
        )

        # Image
        if image_path and image_path.exists():
            img = make_image_flowable(image_path, IMAGE_MAX_WIDTH, IMAGE_MAX_HEIGHT)
            if img:
                img.hAlign = "CENTER"
                story.append(img)
                story.append(Spacer(1, 8))

        # Meta line (servings / yield)
        meta_parts = []
        servings = recipe.get("recipeServings")
        if servings and servings > 0:
            meta_parts.append(f"Servings: {servings:g}")
        yield_qty = recipe.get("recipeYieldQuantity")
        yield_text = (recipe.get("recipeYield") or "").strip()
        if yield_qty and yield_qty > 0:
            meta_parts.append(f"Yield: {yield_qty:g}" + (f" {yield_text}" if yield_text else ""))
        elif yield_text:
            meta_parts.append(f"Yield: {yield_text}")
        if meta_parts:
            story.append(Paragraph("  ·  ".join(meta_parts), styles["Meta"]))

        # Description
        desc = normalize_text((recipe.get("description") or "").strip())
        if desc:
            # Escape basic HTML entities for Paragraph
            desc = (
                desc.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\n", "<br/>")
            )
            story.append(Paragraph(desc, styles["Description"]))

    # Ingredients
    ingredients = recipe.get("recipeIngredient") or []
    if ingredients:
        story.append(Paragraph("Ingredients", styles["SectionHeader"]))
        story.append(
            HRFlowable(
                width="100%",
                thickness=0.6,
                color=colors.Color(0.7, 0.65, 0.55),
                spaceBefore=0,
                spaceAfter=4,
            )
        )
        # Bold variant of the ingredient style (works with both DejaVu and Helvetica)
        ing_bold = ParagraphStyle(
            "IngTitle",
            parent=styles["Ingredient"],
            fontName=styles["Ingredient"].fontName.replace("Sans", "Sans-Bold")
                     if "Sans" in styles["Ingredient"].fontName
                     else "Helvetica-Bold",
            spaceBefore=3,
        )
        # Safer explicit mapping
        if _FONTS_AVAILABLE:
            ing_bold.fontName = "DejaVuSans-Bold"
        else:
            ing_bold.fontName = "Helvetica-Bold"

        for ing in ingredients:
            title = normalize_text((ing.get("title") or "").strip())
            if title:
                story.append(Paragraph(f"<b>{title}</b>", ing_bold))
            text = format_ingredient(ing)
            text = (
                text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            story.append(Paragraph(f"•  {text}", styles["Ingredient"]))
        story.append(Spacer(1, 4))

    # Instructions
    instructions = recipe.get("recipeInstructions") or []
    if instructions:
        story.append(Paragraph("Instructions", styles["SectionHeader"]))
        story.append(
            HRFlowable(
                width="100%",
                thickness=0.6,
                color=colors.Color(0.7, 0.65, 0.55),
                spaceBefore=0,
                spaceAfter=4,
            )
        )
        step_bold = ParagraphStyle(
            "StepTitle",
            parent=styles["Instruction"],
            spaceBefore=4,
        )
        if _FONTS_AVAILABLE:
            step_bold.fontName = "DejaVuSans-Bold"
        else:
            step_bold.fontName = "Helvetica-Bold"

        for idx, step in enumerate(instructions, 1):
            # Section title inside instructions
            step_title = normalize_text((step.get("title") or "").strip())
            if step_title:
                story.append(Paragraph(f"<b>{step_title}</b>", step_bold))
            text = normalize_text((step.get("text") or "").strip())
            if text:
                text = (
                    text.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                    .replace("\n", "<br/>")
                )
                story.append(Paragraph(f"<b>{idx}.</b>  {text}", styles["Instruction"]))
        story.append(Spacer(1, 4))

    # Notes
    notes = recipe.get("notes") or []
    if notes:
        story.append(Paragraph("Notes", styles["SectionHeader"]))
        story.append(
            HRFlowable(
                width="100%",
                thickness=0.6,
                color=colors.Color(0.7, 0.65, 0.55),
                spaceBefore=0,
                spaceAfter=4,
            )
        )
        for note in notes:
            title = normalize_text((note.get("title") or "").strip())
            text = normalize_text((note.get("text") or "").strip())
            if title:
                text = f"<b>{title}:</b> {text}" if text else f"<b>{title}</b>"
            if text:
                text = (
                    text.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                    .replace("\n", "<br/>")
                )
                story.append(Paragraph(text, styles["Note"]))

    return story


def add_page_decorations(canvas, doc):
    """Called for every page – draws the classic double border and footer."""
    canvas.saveState()
    # Outer border
    canvas.setStrokeColor(CARD_BORDER_COLOR)
    canvas.setLineWidth(1.6)
    canvas.rect(MARGIN - 6, MARGIN - 6, CONTENT_WIDTH + 12, PAGE_HEIGHT - 2 * MARGIN + 12)
    # Inner border
    canvas.setLineWidth(0.5)
    canvas.rect(MARGIN - 2, MARGIN - 2, CONTENT_WIDTH + 4, PAGE_HEIGHT - 2 * MARGIN + 4)

    # Footer
    footer_font = "DejaVuSans" if _FONTS_AVAILABLE else "Helvetica"
    canvas.setFont(footer_font, 7)
    canvas.setFillColor(colors.Color(0.45, 0.45, 0.45))
    page_num = canvas.getPageNumber()
    canvas.drawCentredString(
        PAGE_WIDTH / 2,
        0.32 * inch,
        f"— {page_num} —",
    )
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Main export logic
# ---------------------------------------------------------------------------
def export_recipes(
    client: MealieClient,
    recipes: List[Dict[str, Any]],
    output_path: Path,
    temp_dir: Path,
) -> None:
    styles = get_styles()
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN + 4,
        bottomMargin=MARGIN + 10,
        title="Mealie Recipe Cookbook",
        author="Mealie PDF Exporter",
    )

    full_story: List[Any] = []
    total = len(recipes)
    print(f"\nGenerating PDF for {total} recipe(s)…")

    for i, summary in enumerate(recipes, 1):
        slug = summary.get("slug") or summary.get("id")
        name = summary.get("name", slug)
        print(f"  [{i:3d}/{total}] {name} …", end="", flush=True)

        recipe = client.get_recipe(slug)
        if not recipe:
            print("  ⚠ failed to fetch full recipe")
            continue

        # Image
        image_path: Optional[Path] = None
        recipe_id = recipe.get("id")
        if recipe.get("image") and recipe_id:
            img_file = temp_dir / f"{recipe_id}.webp"
            if client.download_image(recipe_id, img_file):
                image_path = img_file
                print(" 📷", end="")
            else:
                print(" (no image)", end="")

        # Build content
        story = build_recipe_story(recipe, styles, image_path)
        if full_story:  # not the first recipe
            full_story.append(PageBreak())
        full_story.extend(story)
        print(" ✓")

    print("\nWriting PDF…", end="", flush=True)
    doc.build(full_story, onFirstPage=add_page_decorations, onLaterPages=add_page_decorations)
    print(f" done.\n\nSaved → {output_path.resolve()}")
    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"File size: {size_mb:.1f} MB")


# ---------------------------------------------------------------------------
# Interactive helpers
# ---------------------------------------------------------------------------
def interactive_menu(client: MealieClient) -> Tuple[List[Dict[str, Any]], Path]:
    print("\nFetching recipe list…")
    summaries = client.get_all_recipe_summaries()
    summaries.sort(key=lambda r: (r.get("name") or "").lower())
    print(f"Found {len(summaries)} recipes.\n")

    while True:
        print("=" * 50)
        print("  Mealie Recipe PDF Exporter")
        print("=" * 50)
        print("  1) Export ALL recipes")
        print("  2) Search & select individual recipes")
        print("  3) Export by exact slug(s)")
        print("  q) Quit")
        choice = input("\nChoice: ").strip().lower()

        if choice == "q":
            sys.exit(0)
        elif choice == "1":
            out = input(f"Output filename [{DEFAULT_OUTPUT}]: ").strip() or DEFAULT_OUTPUT
            return summaries, Path(out)
        elif choice == "2":
            selected = search_and_select(summaries)
            if not selected:
                continue
            out = input(f"Output filename [{DEFAULT_OUTPUT}]: ").strip() or DEFAULT_OUTPUT
            return selected, Path(out)
        elif choice == "3":
            raw = input("Enter slug(s) separated by commas: ").strip()
            if not raw:
                continue
            wanted = {s.strip().lower() for s in raw.split(",") if s.strip()}
            selected = [r for r in summaries if (r.get("slug") or "").lower() in wanted]
            if not selected:
                print("No matching recipes found.")
                continue
            print(f"Matched {len(selected)} recipe(s).")
            out = input(f"Output filename [{DEFAULT_OUTPUT}]: ").strip() or DEFAULT_OUTPUT
            return selected, Path(out)
        else:
            print("Invalid choice.")


def search_and_select(summaries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    while True:
        q = input("\nSearch (name contains, or empty to list first 30): ").strip().lower()
        if not q:
            matches = summaries[:30]
        else:
            matches = [r for r in summaries if q in (r.get("name") or "").lower()]
        if not matches:
            print("No matches.")
            continue

        print(f"\n{len(matches)} match(es):")
        for idx, r in enumerate(matches, 1):
            print(f"  {idx:3d}. {r.get('name')}  [{r.get('slug')}]")

        sel = input(
            "\nEnter numbers to include (e.g. 1,3,5-8) or 'all' for these results, "
            "or 'back': "
        ).strip().lower()
        if sel == "back":
            return []
        if sel == "all":
            return matches

        chosen: List[Dict[str, Any]] = []
        try:
            for part in sel.split(","):
                part = part.strip()
                if "-" in part:
                    a, b = part.split("-", 1)
                    for i in range(int(a), int(b) + 1):
                        if 1 <= i <= len(matches):
                            chosen.append(matches[i - 1])
                else:
                    i = int(part)
                    if 1 <= i <= len(matches):
                        chosen.append(matches[i - 1])
        except ValueError:
            print("Could not parse selection.")
            continue

        # de-dupe while preserving order
        seen = set()
        unique = []
        for r in chosen:
            sid = r.get("id")
            if sid not in seen:
                seen.add(sid)
                unique.append(r)
        print(f"Selected {len(unique)} recipe(s).")
        return unique


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export Mealie recipes to a beautiful classic recipe-card PDF."
    )
    parser.add_argument("--url", default=os.getenv("MEALIE_URL", DEFAULT_BASE_URL),
                        help="Mealie base URL")
    parser.add_argument("--token", default=os.getenv("MEALIE_TOKEN", DEFAULT_TOKEN),
                        help="Mealie long-lived API token")
    parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT,
                        help="Output PDF path")
    parser.add_argument("--all", action="store_true",
                        help="Export every recipe without prompting")
    parser.add_argument("--slugs", type=str, default="",
                        help="Comma-separated list of recipe slugs to export")
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="Force interactive menu")
    args = parser.parse_args()

    print("Mealie Recipe PDF Exporter")
    if not args.url or not args.token:
        parser.error("Mealie URL and token required (--url/--token or MEALIE_URL/MEALIE_TOKEN)")

    print(f"Connecting to {args.url} …")
    client = MealieClient(args.url, args.token)
    if not client.test_connection():
        print("Cannot reach Mealie. Check URL and token.", file=sys.stderr)
        sys.exit(1)
    print("Connected successfully.\n")

    # Decide which recipes to export
    if args.all:
        summaries = client.get_all_recipe_summaries()
        summaries.sort(key=lambda r: (r.get("name") or "").lower())
        recipes = summaries
        output = Path(args.output)
    elif args.slugs:
        wanted = {s.strip().lower() for s in args.slugs.split(",") if s.strip()}
        all_sum = client.get_all_recipe_summaries()
        recipes = [r for r in all_sum if (r.get("slug") or "").lower() in wanted]
        if not recipes:
            print("No matching recipes found for the given slugs.", file=sys.stderr)
            sys.exit(1)
        output = Path(args.output)
    else:
        # Interactive by default when no flags
        recipes, output = interactive_menu(client)

    if not recipes:
        print("Nothing to export.")
        sys.exit(0)

    with tempfile.TemporaryDirectory(prefix="mealie_pdf_") as tmp:
        temp_dir = Path(tmp)
        export_recipes(client, recipes, output, temp_dir)

    print("\nDone! Enjoy your cookbook.")


if __name__ == "__main__":
    main()
