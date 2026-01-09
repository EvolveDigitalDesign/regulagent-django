"""
W-2 Revision Consolidation Service

Consolidates multiple W-2 extractions by:
1. Organizing W-2s by tracking_no
2. Detecting revisions via remarks and revisions.revising_tracking_number
3. Applying revisions to original W-2s
4. Merging additional changes when other_changes=true
5. Building final consolidated well history with proper precedence

Used by W-3A orchestrator to build accurate well geometry from RRC completions.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class W2RevisionConsolidator:
    """
    Consolidates multiple W-2 extraction results by detecting and applying revisions.
    
    Algorithm:
    1. Parse all W-2 extractions and organize by tracking_no
    2. For each W-2 with revisions object:
       - If revisions.revising_tracking_number matches another W-2's tracking_no:
         - This is a revision filing
         - If revisions.other_changes == false:
           - Apply only the specific revision mentioned in revisions.revision_reason
           - Ignore all other extracted data from this W-2
         - If revisions.other_changes == true:
           - Apply the revision AND keep additional new data from this W-2
    3. Return consolidated W-2 data organized chronologically
    """

    def __init__(self, w2_extractions: List[Dict[str, Any]]):
        """
        Initialize with a list of W-2 extraction results from OpenAI.
        
        Args:
            w2_extractions: List of dicts with keys:
                - tracking_no: str (unique identifier)
                - json_data: dict (all extracted fields)
                - revisions: dict or None with keys:
                  - revising_tracking_number: str (tracking_no being revised)
                  - revision_reason: str (what was revised)
                  - other_changes: bool (if there are additional changes)
        """
        self.w2_extractions = w2_extractions
        self.consolidated: Dict[str, Dict[str, Any]] = {}
        self.revisions_applied: List[Dict[str, Any]] = []
        
    def consolidate(self) -> Dict[str, Any]:
        """
        Main entry point: consolidate all W-2s and apply revisions.
        
        Returns:
            Dict with:
            {
                "consolidated_w2s": [{tracking_no, json_data, revisions_applied}],
                "revisions_applied": [{tracking_no, revising_tracking_number, revision_reason, other_changes}],
                "errors": [str]
            }
        """
        logger.info("=" * 80)
        logger.info("🔀 W-2 REVISION CONSOLIDATOR - STARTING")
        logger.info("=" * 80)
        
        try:
            # Phase 1: Index all W-2s by tracking_no
            logger.info(f"\n📋 PHASE 1: Indexing {len(self.w2_extractions)} W-2 extractions")
            self._index_w2s()
            
            # Phase 2: Detect and apply revisions
            logger.info(f"\n🔍 PHASE 2: Detecting and applying revisions")
            self._apply_revisions()
            
            # Phase 3: Build output
            logger.info(f"\n📦 PHASE 3: Building consolidated output")
            result = self._build_output()
            
            logger.info("=" * 80)
            logger.info(f"✅ CONSOLIDATION COMPLETE")
            logger.info(f"   - Total W-2s processed: {len(self.w2_extractions)}")
            logger.info(f"   - Revisions applied: {len(self.revisions_applied)}")
            logger.info(f"   - Consolidated records: {len(result['consolidated_w2s'])}")
            logger.info("=" * 80)
            
            return result
            
        except Exception as e:
            logger.error(f"❌ CONSOLIDATION FAILED: {e}", exc_info=True)
            return {
                "consolidated_w2s": [],
                "revisions_applied": [],
                "errors": [str(e)]
            }

    def _index_w2s(self) -> None:
        """Phase 1: Index all W-2s by tracking_no and validate"""
        for i, w2_extraction in enumerate(self.w2_extractions):
            try:
                # Extract tracking_no from the extraction result
                json_data = w2_extraction.get("json_data", {})
                header = json_data.get("header", {})
                tracking_no = header.get("tracking_no")
                
                if not tracking_no:
                    logger.warning(f"   ⚠️  W-2 #{i+1}: No tracking_no found, skipping")
                    continue
                
                # Store the extraction indexed by tracking_no
                self.consolidated[tracking_no] = {
                    "tracking_no": tracking_no,
                    "json_data": json_data,
                    "original_index": i,
                    "revisions": w2_extraction.get("revisions"),
                    "revisions_applied": []  # Track what revisions were applied to this W-2
                }
                
                logger.info(f"   ✅ W-2 #{i+1}: tracking_no={tracking_no}")
                
                # Log revision info if present
                revisions = w2_extraction.get("revisions")
                if revisions:
                    logger.debug(f"      Has revisions object: {revisions}")
                    
            except Exception as e:
                logger.warning(f"   ⚠️  W-2 #{i+1}: Failed to index: {e}")
                continue

    def _apply_revisions(self) -> None:
        """Phase 2: Detect and apply revisions"""
        revision_count = 0
        
        for tracking_no, w2_record in self.consolidated.items():
            revisions = w2_record.get("revisions")
            
            if not revisions:
                logger.debug(f"   Tracking {tracking_no}: No revisions")
                continue
            
            revising_tracking_number = revisions.get("revising_tracking_number")
            revision_reason = revisions.get("revision_reason")
            other_changes = revisions.get("other_changes", False)
            
            if not revising_tracking_number:
                logger.debug(f"   Tracking {tracking_no}: No revising_tracking_number")
                continue
            
            # Check if the target W-2 exists
            target_w2 = self.consolidated.get(revising_tracking_number)
            if not target_w2:
                logger.warning(
                    f"   ⚠️  Tracking {tracking_no}: Revising tracking {revising_tracking_number} "
                    f"NOT FOUND in extracted documents. Cannot apply revision."
                )
                continue
            
            logger.info(f"\n   📝 REVISION DETECTED:")
            logger.info(f"      Tracking {tracking_no} is revising tracking {revising_tracking_number}")
            logger.info(f"      Reason: {revision_reason}")
            logger.info(f"      Other changes: {other_changes}")
            
            # Apply the revision
            try:
                if other_changes:
                    # CASE 1: Revision + additional changes
                    # Apply revision to target, then merge additional data from this W-2
                    logger.info(f"      → CASE 1: Merging revision + additional changes into target")
                    self._apply_revision_with_merge(
                        target_w2=target_w2,
                        revising_w2=w2_record,
                        revision_reason=revision_reason
                    )
                else:
                    # CASE 2: Correction filing (same data, just marking revision)
                    # Only apply the specific revision noted in remarks
                    logger.info(f"      → CASE 2: Applying correction only (same data as target)")
                    self._apply_revision_only(
                        target_w2=target_w2,
                        revising_w2=w2_record,
                        revision_reason=revision_reason
                    )
                
                # Track the revision
                self.revisions_applied.append({
                    "tracking_no": tracking_no,
                    "revising_tracking_number": revising_tracking_number,
                    "revision_reason": revision_reason,
                    "other_changes": other_changes
                })
                
                revision_count += 1
                logger.info(f"      ✅ Revision applied successfully")
                
            except Exception as e:
                logger.warning(f"      ⚠️  Failed to apply revision: {e}")
                continue
        
        if revision_count > 0:
            logger.info(f"\n✅ Applied {revision_count} revisions")
        else:
            logger.info(f"\n ℹ️  No revisions to apply")

    def _apply_revision_with_merge(
        self,
        target_w2: Dict[str, Any],
        revising_w2: Dict[str, Any],
        revision_reason: str
    ) -> None:
        """
        Apply a revision AND merge additional changes.
        
        1. Parse revision_reason to identify what was revised (e.g., "CIBP size")
        2. Extract the old value from target_w2
        3. Extract the new value from revising_w2
        4. Replace the specific field in target_w2
        5. Merge any additional new data from revising_w2
        """
        target_json = target_w2["json_data"]
        revising_json = revising_w2["json_data"]
        
        logger.info(f"         Extracting field to revise from reason: '{revision_reason}'")
        
        # Try to identify and apply the specific revision
        # This is a pattern-based approach; OpenAI has already identified the field
        
        # Common patterns:
        if "cibp" in revision_reason.lower():
            # CIBP revision - likely in casing_record
            logger.info(f"         Detected CIBP revision")
            self._merge_field_revision(target_json, revising_json, "casing_record", revision_reason)
        elif "cement" in revision_reason.lower():
            # Cement revision - could be in cementing_data or casing_record
            logger.info(f"         Detected cement revision")
            self._merge_field_revision(target_json, revising_json, "cementing_data", revision_reason)
            # Also check casing_record for cement_top_ft updates
            self._merge_field_revision(target_json, revising_json, "casing_record", revision_reason)
        elif "perforation" in revision_reason.lower() or "perf" in revision_reason.lower():
            # Perforation revision
            logger.info(f"         Detected perforation revision")
            self._merge_field_revision(target_json, revising_json, "producing_injection_disposal_interval", revision_reason)
        elif "placement" in revision_reason.lower():
            # Placement revision
            logger.info(f"         Detected placement revision")
            self._merge_field_revision(target_json, revising_json, "casing_record", revision_reason)
        else:
            # Generic revision - merge all data from revising_w2
            logger.info(f"         Generic revision - merging all fields")
            self._deep_merge_json(target_json, revising_json)
        
        # Mark that this revision was applied
        revising_w2["revisions_applied"].append({
            "applied_to_tracking_no": target_w2["tracking_no"],
            "revision_reason": revision_reason,
            "merge_type": "revision_with_changes"
        })

    def _apply_revision_only(
        self,
        target_w2: Dict[str, Any],
        revising_w2: Dict[str, Any],
        revision_reason: str
    ) -> None:
        """
        Apply ONLY the revision (correction filing).
        
        The revising_w2 has the same data as target_w2 but with corrected fields.
        We identify the specific fields that differ and update target.
        """
        target_json = target_w2["json_data"]
        revising_json = revising_w2["json_data"]
        
        logger.info(f"         This is a correction filing - identifying changed fields")
        
        # Compare documents to find what changed
        changed_fields = self._find_changed_fields(target_json, revising_json)
        
        if changed_fields:
            logger.info(f"         Found {len(changed_fields)} changed field(s):")
            for field_path in changed_fields:
                logger.info(f"            - {field_path}")
                # Update the field in target
                self._update_nested_field(target_json, field_path, revising_json)
        else:
            logger.info(f"         No significant changes detected")
        
        # Mark that this revision was applied
        revising_w2["revisions_applied"].append({
            "applied_to_tracking_no": target_w2["tracking_no"],
            "revision_reason": revision_reason,
            "merge_type": "correction_only",
            "fields_changed": changed_fields
        })

    def _merge_field_revision(
        self,
        target_json: Dict[str, Any],
        revising_json: Dict[str, Any],
        field_key: str,
        revision_reason: str
    ) -> None:
        """
        Merge a specific field from revising into target.
        
        For array fields (like casing_record), performs smart merging.
        For scalar fields, performs replacement.
        """
        if field_key not in target_json and field_key not in revising_json:
            return
        
        target_value = target_json.get(field_key)
        revising_value = revising_json.get(field_key)
        
        if revising_value is None:
            return
        
        if isinstance(revising_value, list) and isinstance(target_value, list):
            # Array merge: replace target with revising (these are complete re-submissions)
            logger.debug(f"         Replacing array field '{field_key}': {len(target_value)} → {len(revising_value)} items")
            target_json[field_key] = revising_value
        elif isinstance(revising_value, dict) and isinstance(target_value, dict):
            # Dict merge: deep merge
            logger.debug(f"         Deep-merging dict field '{field_key}'")
            self._deep_merge_json(target_value, revising_value)
        else:
            # Scalar replacement
            logger.debug(f"         Replacing scalar field '{field_key}'")
            target_json[field_key] = revising_value

    def _find_changed_fields(
        self,
        original_json: Dict[str, Any],
        revised_json: Dict[str, Any]
    ) -> List[str]:
        """Find top-level fields that differ between original and revised"""
        changed = []
        all_keys = set(original_json.keys()) | set(revised_json.keys())
        
        for key in all_keys:
            orig_val = original_json.get(key)
            rev_val = revised_json.get(key)
            
            if orig_val != rev_val:
                changed.append(key)
        
        return changed

    def _update_nested_field(
        self,
        target_json: Dict[str, Any],
        field_path: str,
        revising_json: Dict[str, Any]
    ) -> None:
        """Update a field in target_json from revising_json"""
        if field_path in revising_json:
            target_json[field_path] = revising_json[field_path]

    def _deep_merge_json(
        self,
        target: Dict[str, Any],
        source: Dict[str, Any]
    ) -> None:
        """
        Recursively merge source into target.
        
        - For arrays: replace target with source
        - For dicts: recursive merge
        - For scalars: replace
        """
        for key, source_value in source.items():
            if key not in target:
                # New key - add it
                target[key] = source_value
            else:
                target_value = target[key]
                
                if isinstance(source_value, dict) and isinstance(target_value, dict):
                    # Recursive merge
                    self._deep_merge_json(target_value, source_value)
                elif isinstance(source_value, list) or isinstance(target_value, list):
                    # Replace arrays
                    target[key] = source_value
                else:
                    # Replace scalars
                    target[key] = source_value

    def _build_output(self) -> Dict[str, Any]:
        """Build consolidated output"""
        consolidated_w2s = []
        
        for tracking_no in sorted(self.consolidated.keys()):
            w2_record = self.consolidated[tracking_no]
            consolidated_w2s.append({
                "tracking_no": tracking_no,
                "json_data": w2_record["json_data"],
                "revisions_applied": w2_record["revisions_applied"]
            })
        
        return {
            "consolidated_w2s": consolidated_w2s,
            "revisions_applied": self.revisions_applied,
            "errors": []
        }


def consolidate_w2_extractions(w2_extractions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Convenience function to consolidate W-2 extractions.
    
    Args:
        w2_extractions: List of W-2 extraction dicts with tracking_no and json_data
        
    Returns:
        Consolidated result dict
    """
    consolidator = W2RevisionConsolidator(w2_extractions)
    return consolidator.consolidate()







