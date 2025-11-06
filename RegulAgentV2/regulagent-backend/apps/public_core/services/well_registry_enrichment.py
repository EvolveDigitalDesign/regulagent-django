"""
Service to enrich WellRegistry with data extracted from documents.

Implements fallback logic: W2 -> W15 -> GAU for operator, lat/lon, field, lease, well_number.
"""

import logging
from typing import Optional, Dict, Any, List
from decimal import Decimal

from apps.public_core.models import WellRegistry, ExtractedDocument

logger = logging.getLogger(__name__)


def enrich_well_registry_from_documents(
    well: WellRegistry,
    extracted_documents: List[ExtractedDocument]
) -> bool:
    """
    Enrich WellRegistry fields from extracted documents using fallback order: W2 -> W15 -> GAU.
    
    Fields enriched:
    - operator_name
    - field_name
    - lease_name  
    - well_number
    - lat
    - lon
    
    Args:
        well: WellRegistry instance to enrich
        extracted_documents: List of ExtractedDocument instances for this well
    
    Returns:
        True if any fields were updated, False otherwise
    """
    # Organize docs by type
    docs_by_type = {}
    for doc in extracted_documents:
        if doc.document_type in ['w2', 'w15', 'gau']:
            docs_by_type[doc.document_type] = doc
    
    # Fallback order
    fallback_order = ['w2', 'w15', 'gau']
    
    updated = False
    
    # Extract operator_name
    if not well.operator_name:
        operator = _extract_with_fallback(docs_by_type, fallback_order, 'operator_name')
        if operator:
            well.operator_name = operator[:128]  # Respect max_length
            updated = True
            logger.info(f"Enriched well {well.api14} operator_name: {operator}")
    
    # Extract field_name
    if not well.field_name:
        field = _extract_with_fallback(docs_by_type, fallback_order, 'field')
        if field:
            well.field_name = field[:128]
            updated = True
            logger.info(f"Enriched well {well.api14} field_name: {field}")
    
    # Extract lease_name
    if not well.lease_name:
        lease = _extract_with_fallback(docs_by_type, fallback_order, 'lease')
        if lease:
            well.lease_name = lease[:128]
            updated = True
            logger.info(f"Enriched well {well.api14} lease_name: {lease}")
    
    # Extract well_number
    if not well.well_number:
        well_no = _extract_with_fallback(docs_by_type, fallback_order, 'well_no')
        if well_no:
            well.well_number = str(well_no)[:32]
            updated = True
            logger.info(f"Enriched well {well.api14} well_number: {well_no}")
    
    # Extract lat/lon
    if not well.lat or not well.lon:
        coords = _extract_coordinates_with_fallback(docs_by_type, fallback_order)
        if coords:
            if coords['lat'] and not well.lat:
                well.lat = Decimal(str(coords['lat']))
                updated = True
                logger.info(f"Enriched well {well.api14} lat: {coords['lat']}")
            if coords['lon'] and not well.lon:
                well.lon = Decimal(str(coords['lon']))
                updated = True
                logger.info(f"Enriched well {well.api14} lon: {coords['lon']}")
    
    if updated:
        well.save()
        logger.info(f"Saved enriched WellRegistry for {well.api14}")
    
    return updated


def _extract_with_fallback(
    docs_by_type: Dict[str, ExtractedDocument],
    fallback_order: List[str],
    field_name: str
) -> Optional[str]:
    """
    Extract a field from documents using fallback order.
    
    Field mapping:
    - operator_name: operator_info.name
    - field: well_info.field
    - lease: well_info.lease
    - well_no: well_info.well_no
    """
    for doc_type in fallback_order:
        doc = docs_by_type.get(doc_type)
        if not doc:
            continue
        
        json_data = doc.json_data
        if not json_data:
            continue
        
        # Try to extract the field
        value = None
        if field_name == 'operator_name':
            operator_info = json_data.get('operator_info', {})
            value = operator_info.get('name')
        else:
            well_info = json_data.get('well_info', {})
            value = well_info.get(field_name)
        
        if value and str(value).strip() and str(value).strip().lower() not in ['n/a', 'null', 'none', '']:
            return str(value).strip()
    
    return None


def _extract_coordinates_with_fallback(
    docs_by_type: Dict[str, ExtractedDocument],
    fallback_order: List[str]
) -> Optional[Dict[str, Optional[float]]]:
    """
    Extract lat/lon coordinates from documents using fallback order.
    
    Returns dict with 'lat' and 'lon' keys, or None if not found.
    """
    for doc_type in fallback_order:
        doc = docs_by_type.get(doc_type)
        if not doc:
            continue
        
        json_data = doc.json_data
        if not json_data:
            continue
        
        well_info = json_data.get('well_info', {})
        location = well_info.get('location', {})
        
        lat = location.get('lat')
        lon = location.get('lon')
        
        # Check if we have valid coordinates
        if lat is not None and lon is not None:
            try:
                lat_float = float(lat)
                lon_float = float(lon)
                
                # Sanity check: valid range for Texas coordinates
                # Lat: 25.8° to 36.5° N, Lon: -93.5° to -106.5° W
                if 25.0 <= lat_float <= 37.0 and -107.0 <= lon_float <= -93.0:
                    return {'lat': lat_float, 'lon': lon_float}
            except (ValueError, TypeError):
                continue
    
    return None

