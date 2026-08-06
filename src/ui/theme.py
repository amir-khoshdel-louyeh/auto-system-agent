"""Phase 11 item [110]: one visual language for the workbench.

A default Tkinter window looks like a debug tool. This module turns the same
stdlib widgets into a consistent, modern application: one palette, one font
stack chosen from what the host actually has, a flat ttk theme, and card,
header, toolbar, tree and status styles the screens only have to name.

Colors follow the slate/blue palette used by the documentation diagrams, so
the GUI and the architecture pictures describe the same product. Nothing here
holds behaviour — the screens stay in :mod:`ui.workbench`, which keeps the
view model testable without a display.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Any

#: Slate + blue palette (docs/ARCHITECTURE.md uses the same accent).
COLORS: dict[str, str] = {
    "bg": "#f1f5f9",
    "surface": "#ffffff",
    "surface_alt": "#f8fafc",
    "border": "#e2e8f0",
    "text": "#0f172a",
    "muted": "#64748b",
    "faint": "#94a3b8",
    "header": "#0f172a",
    "header_text": "#f8fafc",
    "header_muted": "#94a3b8",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "accent_pale": "#dbeafe",
    "success": "#15803d",
    "success_bg": "#dcfce7",
    "warning": "#b45309",
    "warning_bg": "#fef3c7",
    "danger": "#b91c1c",
    "danger_bg": "#fee2e2",
    "info": "#0369a1",
    "info_bg": "#e0f2fe",
}

#: First family the host has wins; Tk's own default is the last resort.
FONT_STACK: tuple[str, ...] = (
    "Segoe UI",
    "Inter",
    "SF Pro Text",
    "Helvetica Neue",
    "Ubuntu",
    "DejaVu Sans",
)
MONO_STACK: tuple[str, ...] = (
    "Cascadia Mono",
    "JetBrains Mono",
    "SF Mono",
    "Menlo",
    "Consolas",
    "Ubuntu Mono",
    "DejaVu Sans Mono",
)

#: Status / state badge text colors for Treeview rows and badges.
STATE_COLORS: dict[str, str] = {
    "COMPLETED": COLORS["success"],
    "SUCCEEDED": COLORS["success"],
    "APPROVED": COLORS["success"],
    "RUNNING": COLORS["info"],
    "READY": COLORS["info"],
    "PENDING": COLORS["warning"],
    "BLOCKED": COLORS["warning"],
    "CANCELLED": COLORS["muted"],
    "FAILED": COLORS["danger"],
    "REJECTED": COLORS["danger"],
    "DENY": COLORS["danger"],
    "NEED_APPROVAL": COLORS["warning"],
    "ALLOW": COLORS["success"],
    "EXPIRED": COLORS["muted"],
}


def _pick_family(root: tk.Misc, candidates: tuple[str, ...]) -> str:
    """First installed family from ``candidates``, else Tk's default."""
    available = {name.lower(): name for name in tkfont.families(root)}
    for candidate in candidates:
        if candidate.lower() in available:
            return available[candidate.lower()]
    return str(tkfont.nametofont("TkDefaultFont").actual("family"))


def apply_theme(root: Any) -> tuple[str, str]:
    """Apply the palette/styles to ``root``; returns ``(ui font, mono font)``."""
    family = _pick_family(root, FONT_STACK)
    mono = _pick_family(root, MONO_STACK)
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(name).configure(family=family, size=10)
        except tk.TclError:  # pragma: no cover - platform font names differ
            pass
    try:
        tkfont.nametofont("TkFixedFont").configure(family=mono, size=10)
    except tk.TclError:  # pragma: no cover
        pass

    root.configure(bg=COLORS["bg"])
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:  # pragma: no cover - some builds ship no clam
        pass

    style.configure(".", font=(family, 10), background=COLORS["bg"], foreground=COLORS["text"])
    style.configure("TFrame", background=COLORS["bg"])
    style.configure("Surface.TFrame", background=COLORS["surface"])
    style.configure("Alt.TFrame", background=COLORS["surface_alt"])
    style.configure("Header.TFrame", background=COLORS["header"])
    style.configure("Status.TFrame", background=COLORS["surface"])

    style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
    style.configure("Surface.TLabel", background=COLORS["surface"], foreground=COLORS["text"])
    style.configure("Muted.TLabel", background=COLORS["bg"], foreground=COLORS["muted"])
    style.configure("SurfaceMuted.TLabel", background=COLORS["surface"], foreground=COLORS["muted"])
    style.configure(
        "CardLabel.TLabel",
        background=COLORS["surface"],
        foreground=COLORS["muted"],
        font=(family, 9),
    )
    style.configure(
        "CardValue.TLabel",
        background=COLORS["surface"],
        foreground=COLORS["text"],
        font=(family, 24, "bold"),
    )
    style.configure(
        "Section.TLabel",
        background=COLORS["surface"],
        foreground=COLORS["text"],
        font=(family, 11, "bold"),
    )
    style.configure(
        "SectionHeading.TLabel",
        background=COLORS["bg"],
        foreground=COLORS["text"],
        font=(family, 12, "bold"),
    )
    style.configure(
        "Title.TLabel",
        background=COLORS["bg"],
        foreground=COLORS["text"],
        font=(family, 18, "bold"),
    )
    style.configure("Subtitle.TLabel", background=COLORS["bg"], foreground=COLORS["muted"])
    style.configure(
        "HeaderTitle.TLabel",
        background=COLORS["header"],
        foreground=COLORS["header_text"],
        font=(family, 18, "bold"),
    )
    style.configure(
        "HeaderSub.TLabel",
        background=COLORS["header"],
        foreground=COLORS["header_muted"],
        font=(family, 10),
    )
    style.configure(
        "HeaderChip.TLabel",
        background=COLORS["header"],
        foreground=COLORS["header_text"],
        font=(family, 10),
    )
    style.configure(
        "HeaderMuted.TLabel",
        background=COLORS["header"],
        foreground=COLORS["header_muted"],
        font=(family, 10),
    )
    style.configure("Status.TLabel", background=COLORS["surface"], foreground=COLORS["muted"])
    style.configure(
        "Badge.TLabel",
        background=COLORS["surface"],
        foreground=COLORS["muted"],
        font=(family, 10, "bold"),
    )

    style.configure(
        "TButton",
        padding=(10, 6),
        background=COLORS["surface_alt"],
        foreground=COLORS["text"],
        bordercolor=COLORS["border"],
        lightcolor=COLORS["border"],
        darkcolor=COLORS["border"],
        focusthickness=0,
    )
    style.map(
        "TButton",
        background=[("active", COLORS["border"]), ("disabled", COLORS["surface_alt"])],
        foreground=[("disabled", COLORS["faint"])],
    )
    style.configure(
        "Accent.TButton",
        padding=(14, 6),
        background=COLORS["accent"],
        foreground=COLORS["accent_pale"],
        bordercolor=COLORS["accent"],
        lightcolor=COLORS["accent"],
        darkcolor=COLORS["accent"],
    )
    style.map(
        "Accent.TButton",
        background=[("active", COLORS["accent_hover"]), ("disabled", "#93c5fd")],
        foreground=[("disabled", COLORS["surface"])],
    )
    style.configure(
        "Danger.TButton",
        padding=(14, 6),
        background=COLORS["danger"],
        foreground=COLORS["danger_bg"],
        bordercolor=COLORS["danger"],
        lightcolor=COLORS["danger"],
        darkcolor=COLORS["danger"],
    )
    style.map(
        "Danger.TButton",
        background=[("active", "#991b1b"), ("disabled", "#fca5a5")],
        foreground=[("disabled", COLORS["surface"])],
    )
    style.configure(
        "Ghost.TButton",
        padding=(10, 6),
        background=COLORS["surface"],
        bordercolor=COLORS["border"],
        lightcolor=COLORS["surface"],
        darkcolor=COLORS["surface"],
    )
    style.configure(
        "HeaderAction.TButton",
        padding=(12, 6),
        background=COLORS["header"],
        foreground=COLORS["header_text"],
        bordercolor="#334155",
        lightcolor=COLORS["header"],
        darkcolor=COLORS["header"],
    )
    style.map(
        "HeaderAction.TButton",
        background=[("active", "#1e293b")],
    )

    style.configure(
        "Treeview",
        background=COLORS["surface"],
        fieldbackground=COLORS["surface"],
        foreground=COLORS["text"],
        rowheight=26,
        borderwidth=0,
        font=(family, 10),
    )
    style.configure(
        "Treeview.Heading",
        background=COLORS["surface_alt"],
        foreground=COLORS["muted"],
        font=(family, 9, "bold"),
        relief="flat",
        padding=(8, 6),
    )
    style.map(
        "Treeview.Heading",
        background=[("active", COLORS["border"])],
    )
    style.map(
        "Treeview",
        background=[("selected", COLORS["accent_pale"])],
        foreground=[("selected", COLORS["text"])],
    )

    style.configure(
        "TNotebook",
        background=COLORS["bg"],
        borderwidth=0,
        tabmargins=(14, 8, 14, 0),
    )
    style.configure(
        "TNotebook.Tab",
        padding=(16, 8),
        background=COLORS["bg"],
        foreground=COLORS["muted"],
        font=(family, 10),
        borderwidth=0,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", COLORS["surface"])],
        foreground=[("selected", COLORS["accent"])],
        expand=[("selected", (0, 0, 0, 1))],
    )

    style.configure("TPanedwindow", background=COLORS["bg"])
    style.configure(
        "TLabelframe",
        background=COLORS["surface"],
        bordercolor=COLORS["border"],
        lightcolor=COLORS["border"],
        darkcolor=COLORS["border"],
        relief="solid",
        borderwidth=1,
    )
    style.configure(
        "TLabelframe.Label",
        background=COLORS["surface"],
        foreground=COLORS["muted"],
        font=(family, 9, "bold"),
    )
    style.configure(
        "TEntry",
        fieldbackground=COLORS["surface"],
        bordercolor=COLORS["border"],
        lightcolor=COLORS["border"],
        darkcolor=COLORS["border"],
        padding=6,
    )
    style.configure(
        "TCombobox",
        fieldbackground=COLORS["surface"],
        background=COLORS["surface"],
        bordercolor=COLORS["border"],
        lightcolor=COLORS["border"],
        darkcolor=COLORS["border"],
        padding=4,
        arrowcolor=COLORS["muted"],
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", COLORS["surface"])],
        selectbackground=[("readonly", COLORS["surface"])],
        selectforeground=[("readonly", COLORS["text"])],
    )
    style.configure(
        "TSeparator",
        background=COLORS["border"],
    )
    style.configure(
        "TScrollbar",
        background=COLORS["surface_alt"],
        troughcolor=COLORS["bg"],
        bordercolor=COLORS["bg"],
        arrowcolor=COLORS["muted"],
    )
    return family, mono


def card(parent: tk.Misc) -> tk.Frame:
    """A white surface with a 1px slate border (a dashboard card)."""
    return tk.Frame(
        parent,
        bg=COLORS["surface"],
        highlightbackground=COLORS["border"],
        highlightthickness=1,
        bd=0,
    )


def divider(parent: tk.Misc, *, horizontal: bool = True) -> tk.Frame:
    """A one-pixel rule for separating regions inside a card."""
    if horizontal:
        return tk.Frame(parent, bg=COLORS["border"], height=1)
    return tk.Frame(parent, bg=COLORS["border"], width=1)
