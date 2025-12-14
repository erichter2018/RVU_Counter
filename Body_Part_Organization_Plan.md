# Body Part Organization Plan for RVU Counter

## Executive Summary
Currently, the RVU Counter uses broad "Other" categories (CT Other, MRI Other, XR Other, US Other, NM Other) as catch-all buckets. This leads to poor organization in statistics views where most studies are grouped generically rather than by meaningful anatomical location. This plan proposes a comprehensive reorganization by body part while maintaining backward compatibility and reasonable complexity.

**Implementation Status (2025-12-13):**
✅ **COMPLETED** - Body part organization has been implemented in the `organization` branch.
- Added "By Body Part" view to statistics window
- Implemented hierarchical display with body part grouping
- All study types now map to anatomical categories
- Current study view unchanged (shows specific study types only)

**Update Note (2025-12-13):** 
- For CT studies specifically: three distinct categories
  - **CT Chest**: Chest alone
  - **CT AP**: Abdomen ± pelvis (no chest)
  - **CT Body**: Chest + abdomen combinations (± pelvis) - ONLY this is called "CT Body"
- CT Pelvis alone (without abdomen) is classified as MSK
- For other modalities (MRI, US, NM): abdomen and pelvis can be combined
- XR Pelvis and all pelvis-only studies remain within MSK category

---

## Current State Analysis

### Current Generic Categories
- **CT Other** (1.0 RVU) - Used for all non-specific CT studies
- **MRI Other** (1.75 RVU) - Used for all non-brain MRI studies
- **XR Other** (0.3 RVU) - Used for all non-chest, non-MSK XR studies
- **US Other** (0.68 RVU) - Used for all non-specific ultrasound studies
- **NM Other** (1.0 RVU) - Used for all non-cardiac nuclear medicine studies

### Current Well-Organized Categories
**CT Studies:**
- CT Brain (0.9), CT Brain with Contrast (1.0), CT Brain and Cervical (1.9)
- CTA Brain (1.75), CTA Neck (1.75), CTA Brain and Neck (3.5)
- CT Neck (1.5), CT Neck Chest (2.5)
- CT Chest (1.0)
- CT Spine (1.0), CT Spine Lumbar (2.0), CT Spine Lumbar Recon (1.0), CT TL Spine (2.0)
- CT Face (1.0)
- CT CAP (3.06), CT CAP Angio (3.06), CT CAP Trauma (3.0), etc.
- CT AP (1.68)

**XR Studies:**
- XR Chest (0.3)
- XR Abdomen (0.3), XR Acute Abdomen (0.6)
- XR MSK (0.3), XR MSK Bilateral (0.6)
- XR Exam Unlisted (0.68)

---

## Problem Statement

### Issues with Current Organization
1. **Statistics Ambiguity**: "CT Other" and "MRI Other" dominate statistics views but provide no anatomical insight
2. **Missing Granularity**: Common body parts (pelvis, extremities, abdomen) are not tracked separately
3. **Inconsistent Organization**: CT studies are well-organized, but MRI/US/NM are not
4. **User Experience**: Physicians can't easily analyze their case mix by anatomical region

### Real-World Impact
- A user doing 50 MRI studies might see "MRI Other: 45 studies" instead of meaningful breakdown
- Cannot track trends like "increase in extremity MRIs" or "more abdomen/pelvis ultrasounds"
- Difficult to identify which body parts are taking most time or generating most RVU

---

## Proposed Body Part Organization

### Anatomical Hierarchy
Organize all modalities by these body regions:

#### 1. **Neuro/Head & Neck**
- Brain/Head
- Neck
- Spine (Cervical, Thoracic, Lumbar, TL, Full)
- Face/Sinus

#### 2. **CT-Specific Categories**

##### 2a. CT Chest (chest alone)
- CT Chest, CTA Chest
- Standalone category for chest-only studies
- Does not include any abdomen or pelvis

##### 2b. CT AP (abdomen with or without pelvis, NO chest)
- CT Abdomen (abdomen alone)
- CT AP (abdomen + pelvis)
- Does NOT include chest combinations
- Does NOT include pelvis alone (that's MSK)

##### 2c. CT Body (ONLY chest+abdomen combinations ± pelvis)
- CT CA (chest + abdomen, no pelvis)
- CT CA no P (chest + abdomen, explicitly no pelvis)
- CT CAP (chest + abdomen + pelvis)
- CT CAP Angio, CT CAP Trauma, CT CAP variants
- **This is the ONLY category called "CT Body"**
- Essentially: if chest AND abdomen are both involved, it goes here

**Note for other modalities (MRI, US, NM):** Abdomen and pelvis can be combined into single categories

#### 3. **Musculoskeletal (MSK)**
- Upper Extremity (shoulder, arm, elbow, forearm, wrist, hand)
- Lower Extremity (hip, femur, knee, leg, ankle, foot)
- Pelvis/Hip (XR Pelvis, CT Pelvis alone, hip imaging)
- Joints (specific joint imaging)
- General MSK/Bone Survey
- **Note: CT Pelvis (alone, without abdomen) is MSK**

#### 4. **Vascular**
- Angiography studies (by region)
- Arterial/Venous studies

#### 5. **Other/Special**
- Cardiac
- Breast
- Pelvic (GYN-specific like transvaginal US)
- Soft Tissue
- Truly unclassifiable

---

## Proposed RVU Table Structure

### CT Studies (Enhanced)
```
# Neuro/Head & Neck
"CT Brain": 0.9
"CT Brain with Contrast": 1.0
"CT Brain and Cervical": 1.9
"CTA Brain": 1.75
"CTA Neck": 1.75
"CTA Brain and Neck": 3.5
"CT Neck": 1.5
"CT Neck Chest": 2.5
"CT Face": 1.0
"CT Sinus": 1.0  # NEW
"CT Maxillofacial": 1.0

# Spine
"CT Spine": 1.0
"CT Spine Cervical": 1.0  # NEW
"CT Spine Thoracic": 1.0  # NEW
"CT Spine Lumbar": 2.0
"CT Spine Lumbar Recon": 1.0
"CT TL Spine": 2.0

# CT Chest (standalone - chest alone)
"CT Chest": 1.0
"CTA Chest": 1.75  # NEW (currently falls under CT Other)

# CT AP (abdomen ± pelvis, NO chest)
"CT Abdomen": 1.5  # NEW (abdomen alone)
"CT AP": 1.68  # Abdomen + Pelvis

# CT Body (ONLY chest+abdomen combinations ± pelvis)
"CT CA": 2.0  # Chest + Abdomen (no pelvis)
"CT CA no P": 2.0  # Chest + Abdomen explicitly without Pelvis
"CT CAP": 3.06  # Chest + Abdomen + Pelvis
"CT CAP Angio": 3.06
"CT CAP Angio Combined": 2.68
"CT CAP Trauma": 3.0

# MSK
"CT Pelvis": 1.5  # NEW (pelvis alone, without abdomen - MSK study)
"CT Upper Extremity": 1.0  # NEW (shoulder, arm, elbow, wrist, hand)
"CT Lower Extremity": 1.0  # NEW (hip, femur, knee, ankle, foot)

# Vascular
"CTA Runoff with Abdo/Pelvis": 2.75

# Catch-all (reduced usage)
"CT Other": 1.0  # For truly unclassifiable studies
```

### MRI Studies (New Organization)
```
# Neuro
"MRI Brain": 2.3
"MRI Spine Cervical": 1.75  # NEW
"MRI Spine Thoracic": 1.75  # NEW
"MRI Spine Lumbar": 1.75  # NEW
"MRI Spine Complete": 3.0  # NEW (full spine)

# Body
"MRI Chest": 1.75  # NEW
"MRI Abdomen": 1.75  # NEW
"MRI Pelvis": 1.75  # NEW
"MRI Abdomen Pelvis": 2.5  # NEW

# MSK
"MRI Upper Extremity": 1.75  # NEW (shoulder, elbow, wrist, hand)
"MRI Lower Extremity": 1.75  # NEW (hip, knee, ankle, foot)
"MRI Joint": 1.75  # NEW (specific joint studies)

# Special
"MRI Cardiac": 2.5  # NEW
"MRI Breast": 1.75  # NEW

# Catch-all (reduced usage)
"MRI Other": 1.75  # For truly unclassifiable studies
```

### XR Studies (Enhanced)
```
# Current (keep as-is)
"XR Chest": 0.3
"XR Abdomen": 0.3
"XR Acute Abdomen": 0.6
"XR MSK": 0.3
"XR MSK Bilateral": 0.6
"XR Exam Unlisted": 0.68

# Spine-specific (NEW - break out from XR MSK)
"XR Spine": 0.3  # NEW (cervical, thoracic, lumbar, scoliosis)

# Note: XR Pelvis remains in XR MSK category
# as it's part of musculoskeletal imaging

# Catch-all (reduced usage)
"XR Other": 0.3  # For truly unclassifiable studies
```

### US Studies (New Organization)
```
# Vascular
"US Arterial Lower Extremity": 1.2
"US Arterial Upper Extremity": 1.2  # NEW
"US Venous Lower Extremity": 0.68  # NEW
"US Venous Upper Extremity": 0.68  # NEW

# Abdomen & Pelvis
"US Abdomen": 0.68  # NEW
"US Pelvis": 0.68  # NEW
"Ultrasound transvaginal complete": 1.38

# MSK
"US Soft Tissue": 0.68  # NEW
"US Joint": 0.68  # NEW

# Other
"US Breast": 0.68  # NEW
"US Thyroid": 0.68  # NEW

# Catch-all (reduced usage)
"US Other": 0.68  # For truly unclassifiable studies
```

### NM Studies (New Organization)
```
# Cardiac
"NM Myocardial stress": 1.62

# Skeletal
"NM Bone Scan": 1.0  # NEW
"NM Bone Scan 3-Phase": 1.5  # NEW

# Other organs
"NM Lung VQ": 1.0  # NEW
"NM Renal": 1.0  # NEW
"NM Hepatobiliary": 1.0  # NEW
"NM Thyroid": 1.0  # NEW

# Catch-all (reduced usage)
"NM Other": 1.0  # For truly unclassifiable studies
```

---

## Implementation Strategy

### Phase 1: Update rvu_settings.json

#### 1.1 Add New Study Types to `rvu_table`
- Add all new study types listed above with appropriate RVU values
- Keep existing entries for backward compatibility

#### 1.2 Create New Classification Rules
Add rules for each new study type. Examples:

```json
"CT Spine Cervical": [
  {
    "required_keywords": ["CT", "cervical", "spine"],
    "excluded_keywords": ["thor", "lumb"]
  },
  {
    "required_keywords": ["CT", "c-spine"],
    "excluded_keywords": ["thor", "lumb"]
  }
],

"MRI Spine Lumbar": [
  {
    "required_keywords": ["MRI", "lumbar", "spine"]
  },
  {
    "required_keywords": ["MRI", "l-spine"]
  },
  {
    "required_keywords": ["MR", "lumbar", "spine"]
  }
],

"CT Upper Extremity": [
  {
    "required_keywords": ["CT"],
    "any_of_keywords": [
      "shoulder", "humerus", "arm", "elbow", 
      "forearm", "wrist", "hand", "finger", "clavicle"
    ],
    "excluded_keywords": ["bilateral"]
  }
],

"US Abdomen": [
  {
    "required_keywords": ["US", "abdomen"],
    "excluded_keywords": ["pelvis"]
  },
  {
    "required_keywords": ["ultrasound", "abdomen"],
    "excluded_keywords": ["pelvis"]
  }
]
```

#### 1.3 Rule Ordering Strategy
**Critical**: Rules must be ordered from most specific to least specific:
1. Multi-region combinations (e.g., "CT CAP", "MRI Abdomen Pelvis")
2. Specific body part + modifiers (e.g., "CT Spine Lumbar", "MRI Spine Cervical")
3. General body part (e.g., "CT Spine", "MRI Upper Extremity")
4. Catch-all "Other" categories (last resort)

### Phase 2: Update Display Logic in RVUCounter.pyw

#### 2.1 Create Body Part Grouping Function
Location: Add after `_display_by_study_type` method

```python
def _get_body_part_group(self, study_type: str) -> str:
    """Map study types to anatomical groups for hierarchical display."""
    
    # Neuro/Head & Neck
    if any(keyword in study_type.lower() for keyword in [
        'brain', 'head', 'neck', 'spine', 'cervical', 'thoracic', 
        'lumbar', 'face', 'sinus', 'maxillofacial'
    ]):
        if 'spine' in study_type.lower() or 'cervical' in study_type.lower():
            return "Neuro: Spine"
        elif 'neck' in study_type.lower():
            return "Neuro: Neck"
        elif 'face' in study_type.lower() or 'sinus' in study_type.lower():
            return "Neuro: Face/Sinus"
        else:
            return "Neuro: Brain/Head"
    
    # CT imaging - three distinct categories for CT only
    if study_type.startswith('CT ') or study_type.startswith('CTA '):
        has_chest = 'chest' in study_type.lower()
        has_abdomen = 'abdomen' in study_type.lower() or ' ap' in study_type.lower() or ' cap' in study_type.lower() or ' ca ' in study_type.lower()
        has_pelvis = 'pelvis' in study_type.lower() or ' ap' in study_type.lower() or ' cap' in study_type.lower()
        
        # CT Body - ONLY chest + abdomen (with or without pelvis)
        if has_chest and has_abdomen:
            return "CT Body"
        
        # CT AP - Abdomen (with or without pelvis) - but NO chest
        elif has_abdomen and not has_chest:
            return "CT AP"
        
        # CT Chest - Chest alone (no abdomen, no pelvis)
        elif has_chest and not has_abdomen:
            return "CT Chest"
        
        # CT Pelvis alone (no abdomen, no chest) - goes to MSK
        elif has_pelvis and not has_abdomen and not has_chest:
            return "MSK: Pelvis/Hip"
    
    # Non-CT Abdomen & Pelvis (MRI, US, etc.)
    if any(keyword in study_type.lower() for keyword in ['abdomen', 'pelvis']):
        if 'abdomen' in study_type.lower() and 'pelvis' in study_type.lower():
            return "Abdomen & Pelvis: Combined"
        elif 'pelvis' in study_type.lower() and (study_type.startswith('XR') or study_type.startswith('MRI') or study_type.startswith('CT')):
            # XR/MRI/CT Pelvis alone goes to MSK
            return "MSK: Pelvis/Hip"
        elif 'pelvis' in study_type.lower():
            return "Abdomen & Pelvis: Pelvis"
        else:
            return "Abdomen & Pelvis: Abdomen"
    
    # MSK / Extremities
    if any(keyword in study_type.lower() for keyword in [
        'extremity', 'shoulder', 'elbow', 'wrist', 'hand', 'finger',
        'hip', 'knee', 'ankle', 'foot', 'femur', 'tibia', 'humerus'
    ]):
        if any(kw in study_type.lower() for kw in ['shoulder', 'elbow', 'wrist', 'hand', 'finger', 'humerus', 'forearm', 'clavicle']):
            return "MSK: Upper Extremity"
        else:
            return "MSK: Lower Extremity"
    
    # Vascular
    if 'angio' in study_type.lower() or 'arterial' in study_type.lower() or 'venous' in study_type.lower() or 'runoff' in study_type.lower():
        return "Vascular"
    
    # Special
    if 'cardiac' in study_type.lower() or 'myocardial' in study_type.lower():
        return "Special: Cardiac"
    if 'breast' in study_type.lower():
        return "Special: Breast"
    
    # Default to modality-based grouping
    modality = study_type.split()[0] if study_type else "Unknown"
    return f"{modality}: Other"
```

#### 2.2 Enhance _display_by_study_type Method
Add option to toggle between flat view and hierarchical view:

**Option A: Flat View (Current)**
- Shows all study types in a flat list
- Sorted by RVU or study count

**Option B: Hierarchical View (New)**
- Groups by anatomical region
- Shows collapsible/expandable groups
- Shows subtotals for each anatomical region

#### 2.3 Update Grouping Logic (Lines 11729-11736)
Current code groups specific variants:
```python
if study_type == "CT Spine Lumbar" or study_type == "CT Spine Lumbar Recon":
    grouping_key = "CT Spine"
elif study_type == "CT CAP Angio" or study_type == "CT CAP Angio Combined" ...
    grouping_key = "CT CAP"
```

**Proposed Enhancement:**
1. Keep current grouping logic for variants
2. Add optional hierarchical grouping using `_get_body_part_group`
3. Add user preference in settings: "Group by Body Part" checkbox

### Phase 3: Add UI Enhancements

#### 3.1 Current Study View (Main Window) - NO CHANGES NEEDED

**CONFIRMED:** Current implementation already works correctly.

Current display shows:
```
Current Study:
  CT CAP
  3.06 RVU
```

**Proposed enhancement options:**

**Option A: Add Category Context (Minimal Change)**
```
Current Study:
  CT CAP (CT Body)
  3.06 RVU
```
- Shows body part category in parentheses
- Minimal visual change
- Provides anatomical context at a glance

**Option B: Two-Line Display (More Context)**
```
Current Study:
  CT Body
  CT CAP - 3.06 RVU
```
- First line shows body part category
- Second line shows specific study type and RVU
- More prominent categorization

**Option C: No Change (Statistics Only)**
- Keep current study view as-is
- Body part categorization only appears in statistics
- Simpler implementation, less clutter

**Recommendation: Start with Option A or C** (based on user preference)

#### 3.2 Statistics Window Enhancement (Where Parent Categories Are Used)
Add dropdown/toggle in statistics window:
- **View: Flat** (current behavior - shows all study types)
- **View: By Modality** (current "By Modality" view)
- **View: By Body Part** (NEW - hierarchical view with anatomical grouping)

#### 3.3 Hierarchical Display Implementation (Statistics Window)
```
▼ Neuro: Brain/Head (125 studies, 150.5 RVU)
  ├─ CT Brain: 45 studies (40.5 RVU)
  ├─ CTA Brain: 20 studies (35.0 RVU)
  ├─ MRI Brain: 60 studies (138.0 RVU)

▼ Neuro: Spine (85 studies, 142.5 RVU)
  ├─ CT Spine Lumbar: 30 studies (60.0 RVU)
  ├─ MRI Spine Lumbar: 40 studies (70.0 RVU)
  ├─ MRI Spine Cervical: 15 studies (26.25 RVU)

▼ CT Chest (30 studies, 30.0 RVU)
  ├─ CT Chest: 28 studies (28.0 RVU)
  ├─ CTA Chest: 2 studies (3.5 RVU)

▼ CT AP (50 studies, 84.0 RVU)
  ├─ CT Abdomen: 20 studies (30.0 RVU)
  ├─ CT AP: 30 studies (50.4 RVU)

▼ CT Body (42 studies, 128.5 RVU)
  ├─ CT CA: 5 studies (10.0 RVU)
  ├─ CT CAP: 35 studies (107.1 RVU)
  ├─ CT CAP Angio: 2 studies (6.1 RVU)

▼ MSK: Pelvis/Hip (28 studies, 9.9 RVU)
  ├─ XR Pelvis: 18 studies (5.4 RVU)
  ├─ CT Pelvis: 3 studies (4.5 RVU)
  ├─ XR Hip: 7 studies (2.1 RVU)
```

---

## Migration Strategy

### Backward Compatibility
1. **Do NOT remove any existing study types** - old records must still display correctly
2. **Keep existing classification rules** - add new ones, don't replace
3. **Default behavior unchanged** - new features are opt-in

### Handling Historical Data
- Old records with "CT Other", "MRI Other", etc. will display as-is
- No automatic reclassification of historical records
- `fix_database.py` script can be enhanced to reclassify old records (optional)

### User Migration Path
1. **Phase 1**: Update rvu_settings.json with new rules (transparent to users)
2. **Phase 2**: New studies automatically classified more specifically
3. **Phase 3**: Users can optionally run fix_database.py to reclassify old data
4. **Phase 4**: Users enable "Group by Body Part" view in statistics when ready

---

## Testing Strategy

### 1. Rule Testing
Create test cases for each new classification rule:
- Sample procedure texts that should match each new study type
- Verify RVU values are correct
- Ensure specific rules match before generic ones

### 2. Display Testing
- Verify statistics display correctly in flat view
- Test hierarchical grouping logic
- Ensure all edge cases (Unknown, Multiple, etc.) are handled

### 3. Backward Compatibility Testing
- Load database with old records
- Verify old "Other" categories still display
- Ensure no crashes or data loss

### 4. Performance Testing
- Test with large datasets (1000+ studies)
- Ensure classification rules don't slow down study recording
- Verify statistics generation remains fast

---

---

## Decision Points

### Current Study View Display
**Question:** Should the current study view show body part categorization?

**Options:**
1. **Yes - Minimal (Option A)**: Add category in parentheses
   - Pro: Provides context without major UI changes
   - Con: Adds visual clutter
   
2. **Yes - Prominent (Option B)**: Two-line display with category first
   - Pro: Clear anatomical context
   - Con: Takes more screen space
   
3. **No (Option C)**: Keep current study view as-is, categories only in statistics
   - Pro: Simplest, no UI changes to main window
   - Con: User must open statistics to see body part grouping

**Recommendation:** Decide based on:
- Screen space availability in main window
- How critical anatomical context is during active reading
- User preference for information density

---

## Files to Modify

### 1. rvu_settings.json
- Add ~30-40 new study types to `rvu_table`
- Add ~60-80 new classification rules
- Estimated size increase: +200-300 lines

### 2. RVUCounter.pyw
**Additions:**
- New method: `_get_body_part_group()` (~80 lines)
- New method: `_display_by_body_part()` (~150 lines)
- Update: `_display_by_study_type()` (~20 lines modified)
- Settings integration: Add preference checkbox (~10 lines)

**Line numbers affected:**
- Around line 11684: `_display_by_study_type` method
- Around line 11729: Grouping logic
- New methods added after line 11780

### 3. New Test File: test_body_part_classification.py
- Unit tests for new classification rules
- Test cases for body part grouping logic
- ~200-300 lines

---

## Estimated Effort

### Development Time
- **rvu_settings.json updates**: 3-4 hours
  - Define all new study types
  - Create classification rules
  - Test rule ordering

- **RVUCounter.pyw updates**: 6-8 hours
  - Implement body part grouping function
  - Create hierarchical display view
  - Add UI toggle/dropdown
  - Test integration

- **Testing & Refinement**: 4-6 hours
  - Test with real data
  - Refine classification rules
  - Fix edge cases
  - Performance testing

**Total: 13-18 hours**

### Maintenance Considerations
- Classification rules will need ongoing refinement as new procedure names emerge
- Body part grouping logic may need adjustment based on user feedback
- Performance monitoring for large datasets

---

## Benefits

### For Users
1. **Better Analytics**: Understand case mix by anatomical region
2. **Improved Insights**: Track trends by body part over time
3. **Cleaner Statistics**: No more dominated by "Other" categories
4. **Professional Presentation**: More meaningful data for reporting

### For Development
1. **Scalable**: Easy to add new study types by body part
2. **Backward Compatible**: No breaking changes for existing users
3. **Flexible**: Users can choose flat or hierarchical view
4. **Maintainable**: Clear organization makes future updates easier

---

## Risks & Mitigation

### Risk 1: Rule Complexity
**Risk**: Too many classification rules could slow down study classification
**Mitigation**: 
- Classification happens once per study, not on every display
- Rules are evaluated in order (most specific first)
- Consider caching classification results

### Risk 2: False Positives
**Risk**: New rules might misclassify some procedures
**Mitigation**:
- Extensive testing with real data
- Rule ordering prevents over-matching
- Users can manually fix in database if needed
- `fix_database.py` can reclassify after rule updates

### Risk 3: User Confusion
**Risk**: Too many new categories might overwhelm users
**Mitigation**:
- New hierarchical view is opt-in
- Default behavior unchanged
- Documentation and examples provided
- Gradual rollout (classification first, then display)

### Risk 4: Incomplete Coverage
**Risk**: Some procedures still fall into "Other"
**Mitigation**:
- "Other" categories remain as catch-all
- Logging of "Other" classifications for future rule refinement
- User feedback mechanism to identify missed patterns

---

## Future Enhancements

### Phase 4 (Future)
1. **Export by Body Part**: Export statistics grouped by anatomical region
2. **Time Tracking**: Average time per body part/modality
3. **Trend Analysis**: "More lower extremity MRIs this month vs last"
4. **Custom Groupings**: User-defined body part categories
5. **AI-Assisted Classification**: ML model to suggest study type for ambiguous cases

### Phase 5 (Future)
1. **Visual Dashboard**: Heatmap of body parts by volume/RVU
2. **Comparative Analytics**: Compare your case mix to peers
3. **Predictive Insights**: "Based on trends, expect X CAPs next week"

---

## Recommendation

**Proceed with Implementation** following this phased approach:

1. **Week 1**: Update rvu_settings.json with new study types and rules
2. **Week 2**: Implement body part grouping function and basic testing
3. **Week 3**: Add hierarchical display view and UI toggle
4. **Week 4**: Testing, refinement, and documentation

This plan provides:
- ✅ Clear organization by anatomical region
- ✅ Backward compatibility
- ✅ Opt-in adoption path
- ✅ Extensible for future enhancements
- ✅ Manageable development effort

The investment of 13-18 hours will significantly improve the user experience and provide valuable analytics capabilities for physicians tracking their radiology work.

