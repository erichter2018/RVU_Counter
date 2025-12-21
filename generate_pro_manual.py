import os
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.lib import colors
from datetime import datetime
import textwrap

# --- ASSET PATHS (Absolute) ---
BASE_ASSETS_DIR = r"C:\Users\erik.richter\.cursor\projects\c-Users-erik-richter-Desktop-e-tools/assets"
ASSETS = {
    "main_running": os.path.join(BASE_ASSETS_DIR, "c__Users_erik.richter_AppData_Roaming_Cursor_User_workspaceStorage_7c89baefa3998748d56fe7d2e409b43e_images_image-98a1f906-d92a-4c02-8e98-5da2ec5e84bf.png"),
    "statistics": os.path.join(BASE_ASSETS_DIR, "c__Users_erik.richter_AppData_Roaming_Cursor_User_workspaceStorage_7c89baefa3998748d56fe7d2e409b43e_images_image-185f74e6-d14b-4b85-b71f-3bf1eac1275d.png"),
    "settings": os.path.join(BASE_ASSETS_DIR, "c__Users_erik.richter_AppData_Roaming_Cursor_User_workspaceStorage_7c89baefa3998748d56fe7d2e409b43e_images_image-ba8d9ac4-a498-4a98-b33c-20470f3dee5f.png"),
    "compare": os.path.join(BASE_ASSETS_DIR, "c__Users_erik.richter_AppData_Roaming_Cursor_User_workspaceStorage_7c89baefa3998748d56fe7d2e409b43e_images_image-c0a1e2f6-001f-484c-87bb-88b1ddfa43a9.png")
}

OUTPUT_DIR = "documentation/processed_images"
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

def add_callouts(image_key, callouts):
    """Overlays high-precision numbered callouts."""
    src_path = ASSETS[image_key]
    if not os.path.exists(src_path): return None
    img = Image.open(src_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    
    # Precise font selection
    try: font = ImageFont.truetype("arialbd.ttf", 20)
    except: font = ImageFont.load_default()
    
    for i, (x, y) in enumerate(callouts, 1):
        r = 16
        # Outline and shadow for badge
        draw.ellipse([x-r-1, y-r-1, x+r+1, y+r+1], fill=(255, 255, 255))
        draw.ellipse([x-r, y-r, x+r, y+r], fill=(0, 122, 204)) # Windows Blue
        
        txt = str(i)
        w_txt = draw.textlength(txt, font=font) if hasattr(draw, "textlength") else 12
        draw.text((x - w_txt/2, y - 12), txt, fill=(255, 255, 255), font=font)
        
    out_path = os.path.join(OUTPUT_DIR, f"{image_key}_final.png")
    img.save(out_path)
    return out_path

def generate_pdf():
    pdf_path = "RVU_Counter_Technical_Manual.pdf"
    c = canvas.Canvas(pdf_path, pagesize=LETTER)
    width, height = LETTER
    
    # Design constants
    MARGIN = 0.75 * inch
    TITLE_FONT = "Helvetica-Bold"
    BODY_FONT = "Helvetica"
    ACCENT_BLUE = colors.HexColor("#007ACC")
    DARK_TEXT = colors.HexColor("#1E1E1E")
    
    def draw_banner(text):
        c.setFillColor(ACCENT_BLUE)
        c.rect(0, height-1.2*inch, width, 1.2*inch, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont(TITLE_FONT, 24)
        c.drawString(MARGIN, height-0.8*inch, text)

    # --- PAGE 1: COVER ---
    c.setFillColor(colors.HexColor("#F3F3F3"))
    c.rect(0, 0, width, height, fill=1, stroke=0)
    
    c.setFillColor(ACCENT_BLUE)
    c.rect(0, height*0.6, width, height*0.4, fill=1, stroke=0)
    
    c.setFillColor(colors.white)
    c.setFont(TITLE_FONT, 44)
    c.drawCentredString(width/2, height*0.75, "RVU COUNTER")
    c.setFont(BODY_FONT, 18)
    c.drawCentredString(width/2, height*0.70, "Professional Productivity Manual")
    
    c.setFillColor(DARK_TEXT)
    c.setFont(TITLE_FONT, 14)
    c.drawString(MARGIN, height*0.5, "VERSION 1.7 (2025 REVISION)")
    
    intro = (
        "This document serves as the primary technical reference for the RVU Counter. "
        "The software provides radiologists with a background-aware reporting environment, "
        "leveraging deep integration with PowerScribe, Mosaic, and Clario to deliver real-time "
        "throughput intelligence and goal-oriented workflow management."
    )
    ty = height*0.45
    c.setFont(BODY_FONT, 11)
    for line in textwrap.wrap(intro, 70):
        c.drawString(MARGIN, ty, line)
        ty -= 14
        
    c.showPage()

    # --- PAGE 2: MAIN DASHBOARD (DENSE VIEW) ---
    draw_banner("1. Main Interface Dashboard")
    
    # Verify coordinates for 248 x 531 main_running
    callouts_main = [
        (40, 40),    # 1. Start/Stop Toggle
        (200, 40),   # 2. Shift Timer
        (124, 150),  # 3. Core KPI Display
        (124, 235),  # 4. Pace Car Gauge
        (60, 275),   # 5. Dashboard Controls
        (124, 400),  # 6. Session History
        (124, 500)   # 7. Current Reporting Context
    ]
    img_path = add_callouts("main_running", callouts_main)
    
    # Place image on the left, explanations on the right
    if img_path:
        # Scale to 5.5 inches height
        img_w, img_h = 248, 531
        disp_h = 5.5 * inch
        disp_w = (disp_h / img_h) * img_w
        
        c.setStrokeColor(colors.lightgrey)
        c.rect(MARGIN-5, height-disp_h-1.6*inch-5, disp_w+10, disp_h+10, stroke=1, fill=0)
        c.drawImage(img_path, MARGIN, height-disp_h-1.6*inch, width=disp_w, height=disp_h)
        
        # Legends
        lx = MARGIN + disp_w + 0.5*inch
        ly = height - 1.6*inch
        legends = [
            ("Start / Stop Shift", "Initiates background extraction. Toggles global monitoring state."),
            ("Session Clock", "Tracks total elapsed time. Adjusted by retroactive study detection."),
            ("KPI Dashboard", "Real-time wRVU total, hourly average, and shift-end projections."),
            ("Pace Car", "Performance vs. Goal. Blue: Ahead, Purple: On Pace, Red: Behind."),
            ("Navigation", "Quick access to Statistics, Undo, and Configuration."),
            ("Shift Ledger", "Live chronological list of all reports validated in current session."),
            ("Extraction Target", "Shows the specific study currently being reported in PS360/Mosaic.")
        ]
        
        for i, (title, desc) in enumerate(legends, 1):
            c.setFont(TITLE_FONT, 11)
            c.setFillColor(ACCENT_BLUE)
            c.drawString(lx, ly, f"{i}. {title}")
            ly -= 14
            c.setFont(BODY_FONT, 9)
            c.setFillColor(DARK_TEXT)
            for line in textwrap.wrap(desc, 35):
                c.drawString(lx + 0.1*inch, ly, line)
                ly -= 11
            ly -= 8

    c.showPage()

    # --- PAGE 3: PERFORMANCE ANALYTICS (WIDE VIEW) ---
    draw_banner("2. Performance Analytics & Grids")
    
    # Statistics: 1348 x 832
    callouts_stats = [
        (400, 65),   # 1. Perspective Toggles
        (120, 300),  # 2. Historical Filters
        (700, 350),  # 3. Efficiency Heatmap
        (700, 800),  # 4. Summary Totals
        (120, 800)   # 5. Maintenance Tools
    ]
    img_stats = add_callouts("statistics", callouts_stats)
    
    if img_stats:
        # Scale to 6.5 inches width
        disp_w = width - 2*MARGIN
        img_w, img_h = 1348, 832
        disp_h = (disp_w / img_w) * img_h
        
        c.drawImage(img_stats, MARGIN, height-disp_h-1.6*inch, width=disp_w, height=disp_h)
        
        # Legend below wide image
        ly = height - disp_h - 2.0*inch
        col1_y = ly
        col2_y = ly
        
        legends_stats = [
            ("Perspective Toggles", "Switch between Efficiency, Compensation, and Modality views."),
            ("Range Filters", "Scope data to current shift, weekly, monthly, or custom ranges."),
            ("Efficiency Grid", "Minute-by-minute heatmap of diagnostic intensity throughout the day."),
            ("Aggregate Stats", "Cumulative wRVU, count, and complexity averages for the period."),
            ("Database Tools", "Functions for combining shifts and executing secure cloud backups.")
        ]
        
        for i, (title, desc) in enumerate(legends_stats, 1):
            curr_x = MARGIN if i <= 3 else width/2 + 0.2*inch
            curr_y = col1_y if i <= 3 else col2_y
            
            c.setFont(TITLE_FONT, 10)
            c.setFillColor(ACCENT_BLUE)
            c.drawString(curr_x, curr_y, f"{i}. {title}")
            curr_y -= 12
            c.setFont(BODY_FONT, 9)
            c.setFillColor(DARK_TEXT)
            for line in textwrap.wrap(desc, 50):
                c.drawString(curr_x + 0.1*inch, curr_y, line)
                curr_y -= 11
            
            if i <= 3: col1_y = curr_y - 8
            else: col2_y = curr_y - 8

    c.showPage()

    # --- PAGE 4: GOALS & CONFIGURATION ---
    draw_banner("3. Goal Setting & Global Configuration")
    
    # Comparison Window (220 x 381) and Settings (448 x 841)
    img_comp = add_callouts("compare", [(110, 50), (110, 150)])
    img_set = add_callouts("settings", [(224, 80), (224, 250), (224, 480), (224, 700)])
    
    # Layout: Goal top left, Legend top right. Settings bottom.
    if img_comp:
        c.drawImage(img_comp, MARGIN, height-1.6*inch-3.5*inch, width=2.2*inch, height=3.5*inch)
        lx = MARGIN + 2.5*inch
        ly = height - 1.8*inch
        c.setFont(TITLE_FONT, 12)
        c.drawString(lx, ly, "Setting Productivity Targets")
        ly -= 16
        c.setFont(BODY_FONT, 10)
        goal_msg = (
            "The Comparison tool allows you to define a specific RVU/hr goal. "
            "This target serves as the baseline for the 'Pace Car' visualization. "
            "You can target your own historical averages or set a static value."
        )
        for line in textwrap.wrap(goal_msg, 45):
            c.drawString(lx, ly, line)
            ly -= 12

    if img_set:
        disp_h = 4.0 * inch
        img_w, img_h = 448, 841
        disp_w = (disp_h / img_h) * img_w
        c.drawImage(img_set, MARGIN, MARGIN + 0.5*inch, width=disp_w, height=disp_h)
        
        lx = MARGIN + disp_w + 0.5*inch
        ly = MARGIN + disp_h + 0.2*inch
        legends_set = [
            ("Interface Preferences", "Stay-on-top, Dark Mode, and Auto-Resume."),
            ("Information Density", "Customize visible counters and dashboard metrics."),
            ("Role & Scheduling", "Configure Partner/Associate pay and default shift lengths."),
            ("Data Infrastructure", "OneDrive sync intervals and database maintenance.")
        ]
        for i, (title, desc) in enumerate(legends_set, 1):
            c.setFont(TITLE_FONT, 11)
            c.setFillColor(ACCENT_BLUE)
            c.drawString(lx, ly, f"{i}. {title}")
            ly -= 13
            c.setFont(BODY_FONT, 9)
            c.setFillColor(DARK_TEXT)
            for line in textwrap.wrap(desc, 35):
                c.drawString(lx + 0.1*inch, ly, line)
                ly -= 11
            ly -= 10

    c.save()
    print(f"Generated {pdf_path}")

if __name__ == "__main__":
    generate_pdf()




