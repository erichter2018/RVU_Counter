"""
RVU Comparison Script
Scans all Excel files in the current directory and compares procedures/RVUs
against rvu_settings.json rules, generating a report for each file.
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime

try:
    import openpyxl
except ImportError:
    print("ERROR: openpyxl is required. Install with: pip install openpyxl")
    sys.exit(1)


def load_rvu_settings(settings_file='rvu_settings.json'):
    """Load RVU settings from JSON file."""
    # Handle PyInstaller bundled files
    if getattr(sys, 'frozen', False):
        # Running as compiled executable
        base_path = Path(sys._MEIPASS)
    else:
        # Running as script
        base_path = Path(__file__).parent.absolute()
    
    settings_path = base_path / settings_file
    
    # Also check current working directory as fallback
    if not settings_path.exists():
        cwd_path = Path.cwd() / settings_file
        if cwd_path.exists():
            settings_path = cwd_path
    
    try:
        with open(settings_path, 'r', encoding='utf-8') as f:
            settings = json.load(f)
        return settings
    except FileNotFoundError:
        print(f"ERROR: {settings_file} not found")
        if getattr(sys, 'frozen', False):
            print(f"  Running as frozen executable")
            print(f"  Checked bundled location: {base_path / settings_file}")
            print(f"  sys._MEIPASS: {sys._MEIPASS}")
            # List contents for debugging
            try:
                files_in_bundle = list(Path(sys._MEIPASS).iterdir())
                print(f"  Files in bundle: {[f.name for f in files_in_bundle]}")
            except:
                pass
        else:
            print(f"  Checked: {settings_path}")
        print(f"  Also checked working directory: {Path.cwd() / settings_file}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in {settings_file}: {e}")
        sys.exit(1)


def find_column_index(headers, target_names):
    """
    Find column index by exact match of target names (case-insensitive).
    Returns (index, matched_name) or (None, None) if not found.
    Only matches exact column names, not partial matches.
    """
    headers_lower = [str(h).lower().strip() if h else "" for h in headers]
    target_names_lower = [name.lower().strip() for name in target_names]
    
    for idx, header in enumerate(headers_lower):
        if header in target_names_lower:
            return idx, headers[idx] if idx < len(headers) else None
    
    return None, None


def check_procedure_match(procedure_text, rvu_table, classification_rules, direct_lookups):
    """
    Check what study type and RVU would be assigned to this procedure.
    Simulates the match_study_type function from RVUCounter.pyw
    """
    if not procedure_text:
        return None, None
    
    procedure_lower = procedure_text.lower().strip()
    procedure_stripped = procedure_text.strip()
    
    # FIRST: Check direct lookups (exact match, case-insensitive)
    if direct_lookups:
        for lookup_proc, lookup_value in direct_lookups.items():
            if lookup_proc.lower().strip() == procedure_lower:
                if isinstance(lookup_value, str):
                    # Maps to a study type - look up RVU from rvu_table
                    study_type_name = lookup_value
                    rvu_value = rvu_table.get(study_type_name, 0.0)
                    return study_type_name, rvu_value
                else:
                    # Legacy: direct RVU value, return procedure name as study type
                    return procedure_stripped, lookup_value
    
    # SECOND: Check classification rules
    for study_type, rules_list in classification_rules.items():
        if not isinstance(rules_list, list):
            continue
        
        for rule in rules_list:
            required_keywords = rule.get("required_keywords", [])
            excluded_keywords = rule.get("excluded_keywords", [])
            any_of_keywords = rule.get("any_of_keywords", [])
            
            # Check excluded keywords
            if excluded_keywords:
                if study_type == "CT Spine":
                    # Special case: all excluded must be present
                    all_excluded = all(kw.lower() in procedure_lower for kw in excluded_keywords)
                    if all_excluded:
                        continue
                else:
                    # Any excluded keyword present = skip
                    if any(kw.lower() in procedure_lower for kw in excluded_keywords):
                        continue
            
            # Check required keywords
            required_match = True
            if required_keywords:
                required_match = all(kw.lower() in procedure_lower for kw in required_keywords)
            
            # Check any_of keywords
            any_of_match = True
            if any_of_keywords:
                any_of_match = any(kw.lower() in procedure_lower for kw in any_of_keywords)
            
            if required_match and any_of_match:
                rvu = rvu_table.get(study_type, 0.0)
                return study_type, rvu
    
    # THIRD: Try exact match in RVU table
    for study_type, rvu in rvu_table.items():
        if study_type.lower() == procedure_lower:
            return study_type, rvu
    
    # FOURTH: Try keyword matching
    if "ct cap" in procedure_lower:
        return "CT CAP", rvu_table.get("CT CAP", 3.06)
    if "ct ap" in procedure_lower or ("ct" in procedure_lower and "abd" in procedure_lower and "pel" in procedure_lower and "chest" not in procedure_lower):
        return "CT AP", rvu_table.get("CT AP", 1.68)
    if "cta" in procedure_lower:
        if "brain" in procedure_lower and "neck" in procedure_lower:
            return "CTA Brain and Neck", rvu_table.get("CTA Brain and Neck", 3.5)
        if "brain" in procedure_lower or "head" in procedure_lower:
            return "CTA Brain", rvu_table.get("CTA Brain", 1.75)
        if "neck" in procedure_lower:
            return "CTA Neck", rvu_table.get("CTA Neck", 1.75)
        return "CTA Brain", rvu_table.get("CTA Brain", 1.75)
    if "ultrasound" in procedure_lower or ("us" in procedure_lower and " " in procedure_lower):
        return "US Other", rvu_table.get("US Other", 0.68)
    if "mri" in procedure_lower or "mr " in procedure_lower:
        if "brain" in procedure_lower or "head" in procedure_lower:
            return "MRI Brain", rvu_table.get("MRI Brain", 2.3)
        return "MRI Other", rvu_table.get("MRI Other", 1.75)
    if "x-ray" in procedure_lower or ("xr" in procedure_lower and " " in procedure_lower):
        return "XR Other", rvu_table.get("XR Other", 0.3)
    
    # Fallback to CT Other if starts with CT
    if procedure_lower.startswith("ct "):
        return "CT Other", rvu_table.get("CT Other", 1.0)
    
    return None, None


def process_excel_file(excel_path, settings):
    """Process a single Excel file and return comparison results."""
    try:
        wb = openpyxl.load_workbook(excel_path, data_only=True)
    except Exception as e:
        return {
            'error': f"Error opening file: {e}",
            'sheets': [],
            'total_procedures': 0,
            'outliers': []
        }
    
    rvu_table = settings.get('rvu_table', {})
    direct_lookups = settings.get('direct_lookups', {})
    classification_rules = settings.get('classification_rules', {})
    
    all_outliers = []
    sheet_results = []
    total_procedures = 0
    
    # First pass: Find which sheet(s) have the required columns
    sheets_with_columns = []
    all_sheets_checked = []
    
    for sheet_name in wb.sheetnames:
        # Skip "Summary" sheet (case-insensitive)
        if sheet_name.lower() == 'summary':
            continue
        ws = wb[sheet_name]
        
        # Find header row (first non-empty row)
        header_row = None
        headers = None
        for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
            if any(cell for cell in row):
                header_row = row_idx
                headers = list(row)
                break
        
        if header_row is None:
            sheet_results.append({
                'name': sheet_name,
                'error': 'No header row found',
                'procedures': 0,
                'outliers': []
            })
            all_sheets_checked.append(sheet_name)
            continue
        
        # Check for exact column matches (only StandardProcedureName and wRVU_Matrix)
        proc_col_idx, proc_col_name = find_column_index(
            headers, 
            ['StandardProcedureName']
        )
        rvu_col_idx, rvu_col_name = find_column_index(
            headers,
            ['wRVU_Matrix']
        )
        
        if proc_col_idx is not None and rvu_col_idx is not None:
            sheets_with_columns.append({
                'name': sheet_name,
                'worksheet': ws,
                'header_row': header_row,
                'headers': headers,
                'proc_col_idx': proc_col_idx,
                'proc_col_name': proc_col_name,
                'rvu_col_idx': rvu_col_idx,
                'rvu_col_name': rvu_col_name
            })
        else:
            # Sheet doesn't have required columns - report it
            missing = []
            if proc_col_idx is None:
                missing.append('StandardProcedureName')
            if rvu_col_idx is None:
                missing.append('wRVU_Matrix')
            sheet_results.append({
                'name': sheet_name,
                'error': f'Missing required columns: {", ".join(missing)}',
                'procedures': 0,
                'outliers': []
            })
        
        all_sheets_checked.append(sheet_name)
    
    # Process only sheets that have both required columns
    for sheet_info in sheets_with_columns:
        sheet_name = sheet_info['name']
        ws = sheet_info['worksheet']
        header_row = sheet_info['header_row']
        proc_col_idx = sheet_info['proc_col_idx']
        rvu_col_idx = sheet_info['rvu_col_idx']
        proc_col_name = sheet_info['proc_col_name']
        rvu_col_name = sheet_info['rvu_col_name']
        
        # Process data rows
        procedures_with_rvu = []
        for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
            if proc_col_idx < len(row) and rvu_col_idx < len(row):
                proc = row[proc_col_idx]
                rvu_val = row[rvu_col_idx]
                
                if proc and str(proc).strip():
                    proc_str = str(proc).strip()
                    rvu = None
                    if rvu_val is not None:
                        try:
                            rvu = float(rvu_val)
                        except (ValueError, TypeError):
                            pass
                    
                    if rvu is not None:
                        procedures_with_rvu.append((proc_str, rvu))
        
        # Compare procedures
        outliers = []
        for proc, excel_rvu in procedures_with_rvu:
            matched_type, matched_rvu = check_procedure_match(
                proc, rvu_table, classification_rules, direct_lookups
            )
            
            if matched_type is None:
                outliers.append({
                    'procedure': proc,
                    'excel_rvu': excel_rvu,
                    'matched_type': 'NO MATCH',
                    'matched_rvu': None,
                    'reason': 'No matching rule found'
                })
            elif abs(matched_rvu - excel_rvu) > 0.01:  # Allow small floating point differences
                outliers.append({
                    'procedure': proc,
                    'excel_rvu': excel_rvu,
                    'matched_type': matched_type,
                    'matched_rvu': matched_rvu,
                    'reason': f'RVU mismatch: Excel has {excel_rvu}, rules match to {matched_rvu}'
                })
        
        sheet_results.append({
            'name': sheet_name,
            'proc_column': proc_col_name,
            'rvu_column': rvu_col_name,
            'procedures': len(procedures_with_rvu),
            'outliers': outliers
        })
        
        all_outliers.extend(outliers)
        total_procedures += len(procedures_with_rvu)
    
    wb.close()
    
    return {
        'sheets': sheet_results,
        'total_procedures': total_procedures,
        'outliers': all_outliers
    }


def generate_report(excel_path, results, output_path):
    """Generate a text report for an Excel file."""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("=" * 100 + "\n")
        f.write(f"RVU COMPARISON REPORT\n")
        f.write("=" * 100 + "\n")
        f.write(f"Excel File: {excel_path}\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 100 + "\n\n")
        
        if 'error' in results:
            f.write(f"ERROR: {results['error']}\n")
            return
        
        f.write(f"Total Procedures Processed: {results['total_procedures']}\n")
        f.write(f"Total Outliers Found: {len(results['outliers'])}\n\n")
        
        # Report by sheet
        for sheet_info in results['sheets']:
            f.write("-" * 100 + "\n")
            f.write(f"Sheet: {sheet_info['name']}\n")
            f.write("-" * 100 + "\n")
            
            if 'error' in sheet_info:
                f.write(f"  ERROR: {sheet_info['error']}\n\n")
                continue
            
            f.write(f"  Procedure Column: {sheet_info.get('proc_column', 'N/A')}\n")
            f.write(f"  RVU Column: {sheet_info.get('rvu_column', 'N/A')}\n")
            f.write(f"  Procedures in Sheet: {sheet_info['procedures']}\n")
            f.write(f"  Outliers in Sheet: {len(sheet_info['outliers'])}\n\n")
            
            if sheet_info['outliers']:
                # Group unique outliers
                unique_outliers = {}
                for outlier in sheet_info['outliers']:
                    key = outlier['procedure']
                    if key not in unique_outliers:
                        unique_outliers[key] = outlier
                
                f.write(f"  Unique Outliers ({len(unique_outliers)}):\n")
                for proc, outlier in sorted(unique_outliers.items()):
                    f.write(f"    Procedure: {outlier['procedure']}\n")
                    f.write(f"      Excel RVU: {outlier['excel_rvu']}\n")
                    f.write(f"      Matched Type: {outlier['matched_type']}\n")
                    if outlier['matched_rvu'] is not None:
                        f.write(f"      Matched RVU: {outlier['matched_rvu']}\n")
                    f.write(f"      Reason: {outlier['reason']}\n")
                    f.write("\n")
            else:
                f.write("  All procedures match the rules!\n\n")
        
        # Summary of all outliers
        if results['outliers']:
            f.write("\n" + "=" * 100 + "\n")
            f.write("SUMMARY OF ALL OUTLIERS\n")
            f.write("=" * 100 + "\n\n")
            
            # Group by unique procedure
            unique_outliers = {}
            for outlier in results['outliers']:
                key = outlier['procedure']
                if key not in unique_outliers:
                    unique_outliers[key] = outlier
            
            f.write(f"Unique Outlier Procedures: {len(unique_outliers)}\n\n")
            for proc, outlier in sorted(unique_outliers.items()):
                f.write(f"  {outlier['procedure']}\n")
                f.write(f"    Excel RVU: {outlier['excel_rvu']}\n")
                f.write(f"    Matched Type: {outlier['matched_type']}\n")
                if outlier['matched_rvu'] is not None:
                    f.write(f"    Matched RVU: {outlier['matched_rvu']}\n")
                f.write(f"    Reason: {outlier['reason']}\n\n")
        else:
            f.write("\n" + "=" * 100 + "\n")
            f.write("SUCCESS: All procedures match the rules!\n")
            f.write("=" * 100 + "\n")


def main():
    """Main function to process all Excel files in current directory."""
    # When frozen, use the directory where the exe is run from
    # When running as script, use the script's directory
    if getattr(sys, 'frozen', False):
        # Running as compiled executable - use current working directory
        work_dir = Path.cwd()
    else:
        # Running as script - use script's directory
        work_dir = Path(__file__).parent.absolute()
        os.chdir(work_dir)
    
    print("RVU Excel File Comparison Tool")
    print("=" * 60)
    print(f"Working directory: {work_dir}\n")
    
    # Load settings
    print("Loading rvu_settings.json...")
    settings = load_rvu_settings()
    print("Settings loaded successfully.\n")
    
    # Find all Excel files in the working directory
    excel_files = list(work_dir.glob('*.xlsx')) + list(work_dir.glob('*.xlsm'))
    
    if not excel_files:
        print("No Excel files (.xlsx or .xlsm) found in current directory.")
        return
    
    print(f"Found {len(excel_files)} Excel file(s):")
    for f in excel_files:
        print(f"  - {f.name}")
    print()
    
    # Process each file
    for excel_file in excel_files:
        print(f"Processing: {excel_file.name}...")
        
        results = process_excel_file(excel_file, settings)
        
        # Generate report in the same directory as the Excel file
        report_file = work_dir / (excel_file.stem + '_rvu_report.txt')
        generate_report(excel_file.name, results, report_file)
        
        print(f"  Procedures: {results['total_procedures']}")
        print(f"  Outliers: {len(results['outliers'])}")
        print(f"  Report saved to: {report_file}\n")
    
    print("Processing complete!")


if __name__ == '__main__':
    main()

