# RVU Counter

A real-time RVU (Relative Value Unit) tracking application for medical professionals using PowerScribe 360.

## Features

- **Real-time Study Tracking**: Automatically detects and tracks studies from PowerScribe 360
- **RVU Calculation**: Calculates wRVU values based on study types using configurable lookup tables
- **Multiple Counters**: 
  - Total wRVU since shift start
  - Average per hour
  - Last hour
  - Last full hour (with time range)
  - Projected wRVU for current hour
- **Study Management**: 
  - View recent studies
  - Delete individual studies
  - Undo last study
- **Flexible Classification**: 
  - Direct lookup table for exact procedure matches
  - Classification rules with keyword matching
  - Fallback to generic study types
- **Data Persistence**: Saves all study records and settings to JSON files
- **Auto-resume**: Automatically resumes shifts automatically on application restart

## Requirements

- Python 3.7+
- Windows (for PowerScribe 360 integration)
- pywinauto==0.6.8

## Installation

1. Clone this repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the application:
   ```bash
   py RVUCounter.py
   ```

## Usage

1. Launch PowerScribe 360
2. Start the RVU Counter:
   ```bash
   py RVUCounter.py
   ```
3. Click "Start Shift" to begin tracking
4. The application will automatically detect studies as you work in PowerScribe 360

## Configuration

Settings and RVU tables are stored in `rvu_settings.json`. You can edit this file directly or use the Settings button in the application.

### RVU Table

The RVU table maps study types to their wRVU values. Edit `rvu_settings.json` to modify values.

### Classification Rules

Classification rules allow you to match procedure descriptions to study types using keywords. Rules support:
- Required keywords (all must be present)
- Excluded keywords (none can be present)

## Files

- `RVUCounter.py` - Main application
- `rvu_settings.json` - Settings, RVU table, classification rules, window positions
- `rvu_records.json` - Study records and shift data
- `requirements.txt` - Python dependencies

## License

MIT License







