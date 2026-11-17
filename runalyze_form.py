"""Runalyze HTML form parser and body builder.

The key insight: always GET the full edit form, replace only the target field,
and POST all fields back unchanged. This avoids clobbering other activity data.
"""
import re
from html.parser import HTMLParser
from urllib.parse import urlencode


class _FormParser(HTMLParser):
    """Extract all named form controls from an HTML edit page."""

    def __init__(self):
        super().__init__()
        self.fields: dict[str, str] = {}
        self.selects: dict[str, dict] = {}  # name -> {value: label}
        self.selected: dict[str, str]  = {}  # name -> currently selected value
        self._cur_select: str | None   = None
        self._cur_option_val: str | None = None
        self._submit_buttons: list[str] = []
        # textarea state
        self._cur_textarea: str | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        name = a.get("name", "")
        val  = a.get("value", "")
        typ  = a.get("type", "").lower()

        if tag == "input":
            if typ in ("submit", "button"):
                if name:
                    self._submit_buttons.append(name)
                return
            if typ == "checkbox":
                if "checked" in a and name:
                    self.fields[name] = val or "1"
                return
            if typ == "radio":
                if "checked" in a and name:
                    self.fields[name] = val
                return
            if name and typ not in ("file",):
                self.fields[name] = val

        elif tag == "select":
            self._cur_select = name
            self.selects[name] = {}
            self.selected[name] = ""

        elif tag == "option":
            if self._cur_select is not None:
                self._cur_option_val = val
                if "selected" in a:
                    self.selected[self._cur_select] = val

        elif tag == "textarea":
            self._cur_textarea = name

    def handle_endtag(self, tag):
        if tag == "select":
            self._cur_select = None
            self._cur_option_val = None
        elif tag == "textarea":
            self._cur_textarea = None

    def handle_data(self, data):
        if self._cur_textarea and self._cur_textarea not in self.fields:
            self.fields[self._cur_textarea] = data.strip()

    @property
    def preferred_submit(self) -> str | None:
        for name in ("activity[saveAndClose]", "activity[save]",
                     "equipment[save]"):
            if name in self._submit_buttons:
                return name
        return self._submit_buttons[0] if self._submit_buttons else None


def parse_form(html: str) -> _FormParser:
    p = _FormParser()
    p.feed(html)
    return p


def equipment_field_name(category_id: int) -> str:
    return f"activity[equipment][{category_id}]"


def current_equipment(parsed: _FormParser, category_id: int) -> str | None:
    """Return the currently selected equipment ID for this category, or None."""
    key = equipment_field_name(category_id)
    return parsed.selected.get(key)


def has_equipment_field(parsed: _FormParser, category_id: int) -> bool:
    return equipment_field_name(category_id) in parsed.selects


def equipment_options(parsed: _FormParser, category_id: int) -> list[str]:
    """Return valid equipment IDs for this category (option values)."""
    key = equipment_field_name(category_id)
    return list(parsed.selects.get(key, {}).keys())


def build_body(parsed: _FormParser,
               overrides: dict[str, str],
               include_submit: bool = True) -> str:
    """Build a URL-encoded POST body with field overrides applied."""
    body = {}
    # Start from all plain inputs / textareas
    body.update(parsed.fields)
    # Add all selected <select> values
    for name, val in parsed.selected.items():
        body[name] = val
    # Apply overrides (target equipment, etc.)
    body.update(overrides)
    # Add submit button
    if include_submit and parsed.preferred_submit:
        body[parsed.preferred_submit] = "1"
    return urlencode(body)


# ── textarea fallback: notes field for equipment form ─────────────────────────
def extract_notes_textarea(html: str) -> str:
    """Extract equipment[notes] via regex as HTMLParser misses nested content."""
    m = re.search(
        r'<textarea[^>]+name="equipment\[notes\]"[^>]*>(.*?)</textarea>',
        html, re.DOTALL
    )
    return m.group(1).strip() if m else ""
