"""Excel checker logic - compares Excel payroll files with RVU rules."""

import os
import logging
import openpyxl
from typing import List, Dict, Tuple, Optional
from datetime import datetime

from .study_matcher import match_study_type

logger = logging.getLogger(__name__)

class ExcelChecker:
    """Checks Excel payroll files for RVU discrepancies."""
    
    def __init__(self, rvu_table: dict, classification_rules: dict, direct_lookups: dict):
        self.rvu_table = rvu_table
        self.classification_rules = classification_rules
        self.direct_lookups = direct_lookups
        
    def check_file(self, file_path: str, progress_callback=None) -> dict:
        """Process an Excel file and return a report of outliers.
        
        Args:
            file_path: Path to the .xlsx file
            progress_callback: Function taking (current, total)
            
        Returns:
            Dict containing report data
        """
        try:
            wb = openpyxl.load_workbook(file_path, data_only=True)
            sheet = wb.active
            
            # Find columns
            headers = [cell.value for cell in sheet[1]]
            proc_col = -1
            rvu_col = -1
            
            # Look for StandardProcedureName and wRVU_Matrix
            for i, h in enumerate(headers):
                if h == "StandardProcedureName":
                    proc_col = i + 1
                elif h == "wRVU_Matrix":
                    rvu_col = i + 1
                    
            if proc_col == -1 or rvu_col == -1:
                return {"error": "Missing required columns: StandardProcedureName, wRVU_Matrix"}
                
            outliers = []
            rows = list(sheet.iter_rows(min_row=2))
            total_rows = len(rows)
            
            for i, row in enumerate(rows):
                if progress_callback:
                    progress_callback(i + 1, total_rows)
                    
                proc_text = row[proc_col-1].value
                excel_rvu = row[rvu_col-1].value
                
                if proc_text is None or excel_rvu is None:
                    continue
                    
                matched_type, matched_rvu = match_study_type(
                    str(proc_text), 
                    self.rvu_table, 
                    self.classification_rules, 
                    self.direct_lookups
                )
                
                # Compare (with small epsilon for float comparison)
                if abs(float(excel_rvu) - matched_rvu) > 0.01:
                    outliers.append({
                        "procedure": proc_text,
                        "excel_rvu": float(excel_rvu),
                        "matched_type": matched_type,
                        "matched_rvu": matched_rvu,
                        "row": i + 2
                    })
                    
            return {
                "success": True,
                "total_processed": total_rows,
                "outliers": outliers,
                "file_name": os.path.basename(file_path)
            }
            
        except Exception as e:
            logger.error(f"Error checking Excel file: {e}")
            return {"error": str(e)}

    def generate_report_text(self, results: dict) -> str:
        """Format results into a text report."""
        if "error" in results:
            return f"ERROR: {results['error']}"
            
        outliers = results["outliers"]
        unique_outliers = {}
        for o in outliers:
            key = (o["procedure"], o["excel_rvu"], o["matched_type"], o["matched_rvu"])
            if key not in unique_outliers:
                unique_outliers[key] = 0
            unique_outliers[key] += 1
            
        report = []
        report.append("="*80)
        report.append("RVU COMPARISON REPORT")
        report.append("="*80)
        report.append(f"Excel File: {results['file_name']}")
        report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("-"*80)
        report.append(f"Total Procedures Processed: {results['total_processed']}")
        report.append(f"Total Outliers Found: {len(outliers)}")
        report.append("-"*80)
        
        if not outliers:
            report.append("SUCCESS: All procedures match the rules!")
        else:
            report.append(f"Unique Outlier Procedures: {len(unique_outliers)}")
            report.append("")
            for (proc, e_rvu, m_type, m_rvu), count in unique_outliers.items():
                report.append(f"  Procedure: {proc}")
                report.append(f"    Excel RVU: {e_rvu}")
                report.append(f"    Matched Type: {m_type}")
                report.append(f"    Matched RVU: {m_rvu}")
                report.append(f"    Instances: {count}")
                report.append("")
                
        return "\n".join(report)

__all__ = ['ExcelChecker']
