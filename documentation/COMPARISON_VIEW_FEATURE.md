# Shift Comparison View Feature

## Overview
A comprehensive comparison view has been added to the Statistics window that allows users to compare two different shifts side-by-side with detailed graphs and numerical comparisons.

## Features Implemented

### 1. View Mode Toggle
- Added "Comparison" radio button to the view mode toggles in the Statistics window
- When selected, displays the comparison interface

### 2. Shift Selection Interface
- Two dropdown comboboxes in the left panel for selecting shifts to compare
- **Current Shift**: Defaults to the most recent shift
- **Prior Shift**: Defaults to the second most recent shift
- Displays shift date, time, total RVU, and study count in dropdown options
- Dynamically updates when selections change

### 3. Graph Display Mode Toggle
- **Accumulation Mode**: Shows cumulative values over time
- **Average Mode**: Shows average values per hour
- Toggle buttons at the top of the comparison view
- Applies to all graphs simultaneously

### 4. Four Comparison Graphs

#### Graph 1: RVU Accumulation/Average Progression
- Blue line: Current shift
- Purple line: Prior shift
- Shows how RVU accumulates (or averages) over the course of each shift
- Y-axis: Cumulative RVU or Average RVU per hour
- X-axis: Time (actual time or hours from start)

#### Graph 2: Delta from Average RVU
- Compares each shift's performance against its own average RVU per study
- Shows whether performance was above or below average at each hour
- Zero line reference for quick visual comparison
- Helps identify periods of high/low efficiency

#### Graph 3: Study Count by Modality (Top 3)
- Shows the top 3 modalities across both shifts
- Solid lines with circle markers: Current shift
- Dashed lines with square markers: Prior shift
- Different colors for each modality
- Can show accumulation or average studies per hour

#### Graph 4: Total Study Accumulation/Average
- Overall study count progression for both shifts
- Blue line: Current shift
- Purple line: Prior shift
- Shows total workflow pace comparison

### 5. Time Axis Logic
- **Matching Times**: If both shifts start at the same hour (e.g., both start at 5 PM), uses actual clock time (HH:MM format)
- **Different Times**: If shifts start at different hours, uses "Hours from Start" (Hour 0, Hour 1, etc.)
- Automatically detects and applies the appropriate format
- Hour segments are displayed on the x-axis with rotation for readability

### 6. Numerical Comparison Table
Below the graphs, a detailed table shows:

#### Summary Statistics
- Date and time of each shift
- Total RVU with difference
- Total Compensation with difference (in dollars)
- Total Studies with difference
- Color-coded differences (green = positive, red = negative)

#### Studies by Modality
- Complete breakdown of study counts by modality
- Shows counts for both shifts
- Differences color-coded for easy comparison
- Includes all modalities present in either shift

### 7. Graph Styling
- Professional appearance with:
  - Clear legends
  - Grid lines for easier reading
  - Proper axis labels
  - Bold titles
  - Color-coded lines (blue for current, purple for prior)
  - Different marker styles for easy distinction

## Technical Implementation

### Dependencies Added
- `matplotlib>=3.5.0`: For graph rendering
- `numpy>=1.21.0`: Required by matplotlib
- Uses TkAgg backend for Tkinter integration

### Key Methods Implemented

1. **`_populate_comparison_shifts()`**: Populates shift selection dropdowns
2. **`on_comparison_shift_selected()`**: Handles shift selection changes
3. **`_display_comparison()`**: Main method that orchestrates the comparison view
4. **`_process_shift_data_for_comparison()`**: Processes shift data into hourly buckets
5. **`_plot_rvu_progression()`**: Plots RVU accumulation/average graph
6. **`_plot_rvu_delta()`**: Plots delta from average RVU graph
7. **`_plot_modality_progression()`**: Plots modality study count graph
8. **`_plot_total_studies()`**: Plots total study accumulation/average graph
9. **`_create_comparison_table()`**: Creates the numerical comparison table

### State Variables Added
- `comparison_shift1_index`: Index of first selected shift
- `comparison_shift2_index`: Index of second selected shift
- `comparison_graph_mode`: Toggle between "accumulation" and "average"

### UI Components Added
- Comparison LabelFrame in left panel with two comboboxes
- Graph mode toggle buttons
- Scrollable canvas for graphs and comparison table
- Matplotlib figure with 2x2 subplot grid

## Usage Instructions

1. Open Statistics window
2. Click "Comparison" in the View mode toggles
3. Select two shifts to compare using the dropdown menus in the left panel
4. Toggle between "Accumulation" and "Average" modes to view different perspectives
5. Review the four graphs for visual comparison
6. Scroll down to see the detailed numerical comparison table

## Benefits

- **Performance Tracking**: Easily compare current performance against previous shifts
- **Pattern Recognition**: Identify trends and patterns in RVU accumulation
- **Efficiency Analysis**: See when during a shift you're most/least efficient
- **Modality Insights**: Understand how different types of studies impact overall performance
- **Goal Setting**: Use prior shifts as benchmarks for improvement
- **Flexible Analysis**: Switch between accumulation and average views for different insights

## Future Enhancements (Potential)

- Compare more than two shifts simultaneously
- Add export functionality for graphs
- Include average across multiple historical shifts as a baseline
- Add confidence intervals or trend lines
- Filter by specific modalities or study types
- Save favorite shift comparisons










