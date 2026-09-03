"""Studio's built-in copies of Omarchy's stock color schemes.

These values are deliberately kept in the application instead of being read
from an Omarchy installation.  That makes the themes available on every
desktop, including Windows, macOS, and non-Omarchy Linux systems.
"""

from __future__ import annotations

from dataclasses import replace

from .palette import Palette


# name, display name, mode, accent, selection, background, dark background,
# lighter background, foreground, muted foreground, red, yellow, green,
# bright red.  Sourced from Omarchy's stock colors.toml files.
_THEMES = (
    ("omarchy-catppuccin", "Catppuccin", "dark", "#89b4fa", "#45475a", "#1e1e2e", "#161622", "#313244", "#cdd6f4", "#6c7086", "#f38ba8", "#f9e2af", "#a6e3a1", "#f38ba8"),
    ("omarchy-catppuccin-latte", "Catppuccin Latte", "light", "#1e66f5", "#ccd0da", "#eff1f5", "#e3e4e8", "#dce0e8", "#4c4f69", "#9ca0b0", "#d20f39", "#df8e1d", "#40a02b", "#d20f39"),
    ("omarchy-ethereal", "Ethereal", "dark", "#7d82d9", "#252e56", "#060B1E", "#040816", "#131a3a", "#ffcead", "#6d7db6", "#ED5B5A", "#E9BB4F", "#92a593", "#faaaa9"),
    ("omarchy-everforest", "Everforest", "dark", "#7fbbb3", "#3d484d", "#2d353b", "#21272c", "#343f44", "#d3c6aa", "#4f585e", "#e67e80", "#dbbc7f", "#a7c080", "#e67e80"),
    ("omarchy-flexoki-light", "Flexoki Light", "light", "#205EA6", "#CECDC3", "#FFFCF0", "#f2efe4", "#E6E4D9", "#100F0F", "#878580", "#D14D41", "#D0A215", "#879A39", "#D14D41"),
    ("omarchy-gruvbox", "Gruvbox", "dark", "#7daea3", "#504945", "#282828", "#1e1e1e", "#3c3836", "#d4be98", "#7c6f64", "#ea6962", "#d8a657", "#a9b665", "#ea6962"),
    ("omarchy-hackerman", "Hackerman", "dark", "#82FB9C", "#1f253a", "#0B0C16", "#080910", "#151828", "#ddf7ff", "#6a6e95", "#50f872", "#50f7d4", "#4fe88f", "#85ff9d"),
    ("omarchy-kanagawa", "Kanagawa", "dark", "#dcd7ba", "#363646", "#1f1f28", "#17171e", "#223249", "#dcd7ba", "#727169", "#c34043", "#c0a36e", "#76946a", "#e82424"),
    ("omarchy-last-horizon", "Last Horizon", "dark", "#b59790", "#584e51", "#0c0b0c", "#090809", "#0c0b0c", "#FAFCFB", "#584e51", "#c38b7b", "#6B5E73", "#87a9b0", "#c38b7b"),
    ("omarchy-lumon", "Lumon", "dark", "#8bc9eb", "#243d56", "#16242d", "#101b21", "#1b2d40", "#d6e2ee", "#4d86b0", "#4d86b0", "#6fa4c9", "#5e95bc", "#73a6cb"),
    ("omarchy-lupine", "Lupine", "light", "#3264eb", "#d0d0d0", "#fafafa", "#ececec", "#f5f5f5", "#212121", "#757575", "#c900c4", "#026fde", "#4a2fd0", "#f930fb"),
    ("omarchy-matte-black", "Matte Black", "dark", "#e68e0d", "#2a2a2a", "#121212", "#0d0d0d", "#1e1e1e", "#bebebe", "#555555", "#D35F5F", "#b91c1c", "#FFC107", "#B91C1C"),
    ("omarchy-miasma", "Miasma", "dark", "#78824b", "#383838", "#222222", "#191919", "#2c2c2c", "#c2c2b0", "#555555", "#685742", "#b36d43", "#5f875f", "#685742"),
    ("omarchy-nord", "Nord", "dark", "#81a1c1", "#434c5e", "#2e3440", "#222730", "#3b4252", "#d8dee9", "#667080", "#bf616a", "#ebcb8b", "#a3be8c", "#bf616a"),
    ("omarchy-osaka-jade", "Osaka Jade", "dark", "#509475", "#32473B", "#111c18", "#0c1512", "#23372B", "#C1C497", "#81B8A8", "#FF5345", "#459451", "#549e6a", "#db9f9c"),
    ("omarchy-retro-82", "Retro 82", "dark", "#faa968", "#134e5a", "#05182e", "#031222", "#0a2540", "#f6dcac", "#3f8f8a", "#f85525", "#e97b3c", "#028391", "#f85525"),
    ("omarchy-ristretto", "Ristretto", "dark", "#f38d70", "#403e41", "#2c2525", "#211b1b", "#3d2f2a", "#e6d9db", "#72696a", "#fd6883", "#f9cc6c", "#adda78", "#ff8297"),
    ("omarchy-rose-pine", "Rose Pine", "light", "#56949f", "#dfdad9", "#faf4ed", "#ede7e1", "#f2e9e1", "#575279", "#9893a5", "#b4637a", "#ea9d34", "#286983", "#b4637a"),
    ("omarchy-solitude", "Solitude", "dark", "#798186", "#343d41", "#101315", "#0c0e10", "#101315", "#cacccc", "#4b4e55", "#565d60", "#d9dbdc", "#9fa5a9", "#de6145"),
    ("omarchy-tokyo-night", "Tokyo Night", "dark", "#7aa2f7", "#292e42", "#1a1b26", "#13141c", "#24283b", "#a9b1d6", "#565f89", "#f7768e", "#e0af68", "#9ece6a", "#ff7a93"),
    ("omarchy-vantablack", "Vantablack", "dark", "#8d8d8d", "#1a1a1a", "#000000", "#090909", "#1a1a1a", "#ffffff", "#505050", "#a4a4a4", "#cecece", "#b6b6b6", "#a4a4a4"),
    ("omarchy-white", "White", "light", "#6e6e6e", "#c0c0c0", "#ffffff", "#f5f5f5", "#c0c0c0", "#000000", "#c0c0c0", "#2a2a2a", "#4a4a4a", "#3a3a3a", "#2a2a2a"),
)


def _palette(theme: tuple[str, ...]) -> Palette:
    # Import lazily: package initialization loads omarchy.py before palette.py
    # has finished defining its built-ins.
    from .omarchy import _to_palette

    name, label, mode, accent, selection, background, dark_background, lighter_background, foreground, dark_foreground, red, yellow, green, bright_red = theme
    colors = {
        "mode": mode,
        "accent": accent,
        "selection": selection,
        "background": background,
        "dark_background": dark_background,
        "lighter_background": lighter_background,
        "foreground": foreground,
        "dark_foreground": dark_foreground,
        "red": red,
        "yellow": yellow,
        "green": green,
        "bright_red": bright_red,
    }
    return replace(_to_palette(colors), name=name, label=label)


def palettes() -> tuple[Palette, ...]:
    """Build the bundled palettes without needing an Omarchy installation."""
    return tuple(_palette(theme) for theme in _THEMES)
