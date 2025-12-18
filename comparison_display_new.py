# New optimized comparison display implementation
# This will replace the current _display_comparison method

# Key improvements:
# 1. Options placed next to relevant graphs
# 2. Dark mode support
# 3. Performance optimization (don't recreate everything)
# 4. Dropdown persistence
# 5. Remove 4th graph when "All" is selected and sum modalities
# 6. Proper Y-axis alignment

# Implementation notes for integration:
# - Get theme colors from self.app.get_theme_colors()
# - Apply to figure background, text colors, grid
# - Use conditional graph creation based on modality filter
# - Cache figure and update only changed parts
