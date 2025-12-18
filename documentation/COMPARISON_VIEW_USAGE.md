# Shift Comparison View - Quick Start Guide

## Getting Started

### Prerequisites
1. Install required dependencies:
   ```bash
   pip install matplotlib numpy
   ```
   Or update all dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Have at least 2 completed shifts in your database

## How to Use

### Step 1: Open Statistics Window
- Click the **"Statistics"** button in the main RVU Counter window

### Step 2: Select Comparison View
- In the Statistics window, click the **"Comparison"** radio button in the View section
- This is located in the top toolbar alongside "Efficiency", "Compensation", etc.

### Step 3: Select Shifts to Compare
Two dropdown menus will appear in the left panel:

1. **Current Shift** (top dropdown)
   - Defaults to your most recent shift
   - Shows: Day, Date, Time, Total RVU, and Study Count
   - Example: "Mon 12/16 05:00PM - 125.5 RVU (87 studies)"

2. **Prior Shift** (bottom dropdown)
   - Defaults to your second most recent shift
   - Same information format as Current Shift

You can select any two shifts you want to compare from these dropdowns.

### Step 4: Choose Graph Mode

At the top of the comparison view, toggle between:
- **Accumulation**: Shows cumulative values over time (default)
  - See total RVU/studies accumulated hour by hour
  - Useful for tracking overall progress
  
- **Average**: Shows average values per hour
  - See average RVU or studies per hour up to that point
  - Useful for identifying efficiency trends

### Step 5: Interpret the Graphs

#### Graph 1: RVU Accumulation (Top Left)
- **Blue line**: Current shift
- **Purple line**: Prior shift
- Shows how RVU builds up over the shift
- **Look for**: Steeper slopes = faster RVU accumulation

#### Graph 2: RVU Delta from Average (Top Right)
- Shows how each hour compares to the shift's overall average
- **Above zero**: Above-average performance in that hour
- **Below zero**: Below-average performance in that hour
- **Look for**: Patterns in your efficiency (e.g., slower starts, strong finishes)

#### Graph 3: Study Count by Modality (Bottom Left)
- Shows top 3 modalities for both shifts
- Solid lines = Current shift
- Dashed lines = Prior shift
- **Look for**: Which exam types dominated each shift

#### Graph 4: Total Study Accumulation (Bottom Right)
- Overall study count over time
- **Look for**: Pace comparison - who read more studies and when

### Step 6: Review Numerical Comparison

Scroll down to see detailed statistics:

#### Summary Section
- Date and time of each shift
- **Total RVU**: With color-coded difference (green = current higher)
- **Total Compensation**: Dollar amounts with difference
- **Total Studies**: Count with difference

#### Studies by Modality Section
- Complete breakdown by exam type
- See exactly how many CT, MR, US, etc. you did in each shift
- Differences show where you had more/less of each type

## Tips and Tricks

### 1. Compare Similar Shifts
- Compare shifts from the same day of week for more relevant insights
- Night shifts vs night shifts, day shifts vs day shifts

### 2. Use as Performance Benchmark
- Compare current shift against your best prior shift
- Set goals based on historical performance

### 3. Toggle Between Modes
- Start with **Accumulation** to see overall progress
- Switch to **Average** to identify efficiency patterns

### 4. Identify Patterns
- Look for consistent slow/fast hours
- Notice if certain modalities appear more in certain shifts
- Track improvement over time

### 5. Mouse Wheel Scrolling
- Use your mouse wheel to scroll through the comparison view
- Graphs are at the top, detailed numbers at the bottom

## Understanding the X-Axis

### Matching Start Times
If both shifts start at the same hour (e.g., both 5 PM):
- X-axis shows actual clock time: "17:00", "18:00", "19:00", etc.
- Makes it easy to see performance at specific times of day

### Different Start Times
If shifts start at different hours:
- X-axis shows elapsed time: "Hour 0", "Hour 1", "Hour 2", etc.
- Compares relative progress regardless of actual clock time

## Troubleshooting

### "No shifts available for comparison"
- You haven't completed any shifts yet
- Complete at least one shift to start collecting data

### "At least two shifts are required"
- You only have one completed shift
- Complete another shift to use the comparison feature

### "Matplotlib is required for comparison view"
- The graphing library is not installed
- Run: `pip install matplotlib numpy`
- Restart the application

### Shifts look very different lengths
- This is normal - some shifts are longer/shorter
- The graphs automatically adjust to show the full duration of each shift

### Can't see all graphs
- Scroll down - the comparison view is scrollable
- Make the Statistics window larger if needed

## Example Interpretations

### Scenario 1: Current shift RVU higher
**Graphs show**: Current shift line consistently above prior shift
**Interpretation**: You're performing better - more efficient, higher complexity cases, or longer shift

### Scenario 2: Similar total RVU, different patterns
**Graphs show**: Lines cross multiple times, end at similar values
**Interpretation**: Different case mix or workflow patterns led to same result

### Scenario 3: More studies but lower RVU
**Graphs show**: Study count higher but RVU lower
**Interpretation**: Doing more lower-complexity cases in current shift

### Scenario 4: Delta graph shows negative start
**Graphs show**: First few hours below zero, then above
**Interpretation**: Slow start, strong finish - possible "warm-up" effect

## Advanced Uses

- Track weekly improvement by comparing each new shift to the prior week's same day
- Analyze impact of different case mixes on productivity
- Identify optimal working hours (when you're most efficient)
- Compare pre/post-vacation performance
- Evaluate the effect of workflow changes or new protocols

---

**Note**: This feature requires matplotlib and numpy. These are automatically included if you installed from requirements.txt.










