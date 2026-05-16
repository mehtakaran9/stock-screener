# Design tokens — mirrors the CSS custom properties in frontend/src/App.css (:root)
# and the helper functions in frontend/src/components/StockTable.tsx.
# Update here when the web UI palette or logic changes.

BG_EMAIL_BODY  = "#0a0a0a"
BG_PRIMARY     = "#121212"
BG_SECONDARY   = "#1e1e1e"
BG_CARD        = "#2c2c2c"
BG_DETAIL_ROW  = "#1a1a1a"
BG_AMBER_BANNER = "#2a1a00"
ACCENT        = "#bb86fc"
SUCCESS       = "#03dac6"
DANGER        = "#cf6679"
BORDER        = "#333"
BORDER_SUBTLE = "#2a2a2a"
TEXT_PRIMARY  = "rgba(255,255,255,0.87)"
TEXT_SECONDARY = "rgba(255,255,255,0.5)"
TEXT_FAINT    = "rgba(255,255,255,0.4)"
AMBER         = "#f59e0b"


def fmt_number(n: float) -> str:
    """Compact number formatter — mirrors StockTable.tsx formatNumber."""
    if n >= 1e12: return f"{n / 1e12:.2f}T"
    if n >= 1e9:  return f"{n / 1e9:.2f}B"
    if n >= 1e6:  return f"{n / 1e6:.2f}M"
    if n >= 1e3:  return f"{n / 1e3:.2f}K"
    return f"{n:,.0f}"


def rsi_color(rsi: float) -> str:
    """RSI colour — mirrors StockTable.tsx getRsiColor."""
    if rsi > 70:  return DANGER
    if rsi >= 55: return SUCCESS
    if rsi >= 40: return AMBER
    return TEXT_SECONDARY


def rsi_label(rsi: float) -> str:
    """RSI label — mirrors StockTable.tsx getRsiLabel."""
    if rsi > 70:  return "OB"
    if rsi >= 55: return "Bull"
    if rsi >= 40: return "Neut"
    return "Weak"
