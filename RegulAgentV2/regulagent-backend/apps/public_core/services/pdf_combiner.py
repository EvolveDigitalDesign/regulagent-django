"""
Service for combining multiple PDF documents into a single temporary file.

Used during W3A document verification flow to show user all sourced documents
before extraction.
"""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Optional
import logging

try:
    from PyPDF2 import PdfMerger, PdfReader
    HAS_PYPDF2 = True
except ImportError:
    HAS_PYPDF2 = False

logger = logging.getLogger(__name__)


class PDFCombinerError(Exception):
    """Raised when PDF combination fails."""
    pass


def combine_pdfs_to_temp(
    pdf_paths: List[str],
    *,
    output_prefix: str = "combined",
    ttl_hours: int = 24,
    add_metadata: bool = True
) -> Dict[str, Any]:
    """
    Combine multiple PDF files into a single temporary PDF.
    
    Args:
        pdf_paths: List of absolute paths to PDF files to combine
        output_prefix: Prefix for the temporary file name
        ttl_hours: How long the file should be kept (for cleanup jobs)
        add_metadata: Whether to add document labels/metadata to combined PDF
    
    Returns:
        Dict with:
            - temp_path: Absolute path to combined PDF
            - file_size: Size in bytes
            - page_count: Total pages in combined PDF
            - source_files: List of source file info
            - ttl_expires_at: ISO timestamp when file should be deleted
    
    Raises:
        PDFCombinerError: If combination fails
    """
    if not HAS_PYPDF2:
        raise PDFCombinerError("PyPDF2 not installed. Install with: pip install PyPDF2")
    
    if not pdf_paths:
        raise PDFCombinerError("No PDF paths provided")
    
    # Validate all files exist
    missing = [p for p in pdf_paths if not os.path.exists(p)]
    if missing:
        raise PDFCombinerError(f"PDF files not found: {missing}")
    
    try:
        merger = PdfMerger()
        source_info = []
        total_pages = 0
        
        # Add each PDF
        for idx, pdf_path in enumerate(pdf_paths):
            try:
                # Read PDF to get metadata
                with open(pdf_path, 'rb') as f:
                    reader = PdfReader(f)
                    page_count = len(reader.pages)
                    total_pages += page_count
                    
                    # Track source file info
                    source_info.append({
                        "index": idx,
                        "filename": os.path.basename(pdf_path),
                        "path": pdf_path,
                        "page_count": page_count,
                        "page_range": [total_pages - page_count + 1, total_pages]
                    })
                
                # Append to merger
                merger.append(pdf_path)
                
                logger.debug(f"Added PDF {idx+1}/{len(pdf_paths)}: {os.path.basename(pdf_path)} ({page_count} pages)")
                
            except Exception as e:
                logger.error(f"Failed to process PDF {pdf_path}: {e}")
                raise PDFCombinerError(f"Failed to process {os.path.basename(pdf_path)}: {e}")
        
        # Create temporary output file in Django's media directory for persistence
        import django.conf
        media_root = getattr(django.conf.settings, "MEDIA_ROOT", tempfile.gettempdir())
        temp_uploads_dir = os.path.join(media_root, "temp_pdfs")
        os.makedirs(temp_uploads_dir, exist_ok=True)
        
        ts = str(int(__import__("time").time()))
        temp_filename = f"{output_prefix}_{ts}.pdf"
        temp_path = os.path.join(temp_uploads_dir, temp_filename)
        
        try:
            # Write combined PDF
            with open(temp_path, 'wb') as output_file:
                merger.write(output_file)
            
            merger.close()
            
            # Get file size
            file_size = os.path.getsize(temp_path)
            
            # Calculate TTL expiry
            import datetime
            expires_at = datetime.datetime.utcnow() + datetime.timedelta(hours=ttl_hours)
            
            logger.info(
                f"✅ Combined {len(pdf_paths)} PDFs into {temp_path} "
                f"({total_pages} pages, {file_size / 1024:.1f} KB)"
            )
            
            return {
                "temp_path": temp_path,
                "file_size": file_size,
                "page_count": total_pages,
                "source_files": source_info,
                "ttl_expires_at": expires_at.isoformat() + "Z",
                "ttl_hours": ttl_hours,
            }
            
        except Exception as e:
            # Clean up temp file on error
            try:
                os.unlink(temp_path)
            except Exception:
                pass
            raise PDFCombinerError(f"Failed to write combined PDF: {e}")
            
    except PDFCombinerError:
        raise
    except Exception as e:
        raise PDFCombinerError(f"Unexpected error combining PDFs: {e}")


def cleanup_temp_pdf(temp_path: str) -> bool:
    """
    Delete a temporary combined PDF file.
    
    Args:
        temp_path: Absolute path to temporary PDF
    
    Returns:
        True if deleted successfully, False otherwise
    """
    try:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
            logger.info(f"🗑️  Deleted temporary PDF: {temp_path}")
            return True
        else:
            logger.warning(f"⚠️  Temp PDF not found (already deleted?): {temp_path}")
            return False
    except Exception as e:
        logger.error(f"❌ Failed to delete temp PDF {temp_path}: {e}")
        return False


def cleanup_expired_temp_pdfs(temp_dir: Optional[str] = None, max_age_hours: int = 48) -> int:
    """
    Clean up expired temporary PDFs (called by periodic cleanup job).
    
    Args:
        temp_dir: Directory to scan (defaults to system temp dir)
        max_age_hours: Delete files older than this many hours
    
    Returns:
        Number of files deleted
    """
    import datetime
    import glob
    
    if temp_dir is None:
        temp_dir = tempfile.gettempdir()
    
    pattern = os.path.join(temp_dir, "combined_*.pdf")
    now = datetime.datetime.utcnow()
    deleted_count = 0
    
    for filepath in glob.glob(pattern):
        try:
            # Get file modification time
            mtime = datetime.datetime.utcfromtimestamp(os.path.getmtime(filepath))
            age_hours = (now - mtime).total_seconds() / 3600
            
            if age_hours > max_age_hours:
                os.unlink(filepath)
                deleted_count += 1
                logger.info(f"🗑️  Cleaned up expired temp PDF: {filepath} (age: {age_hours:.1f}h)")
        except Exception as e:
            logger.warning(f"⚠️  Failed to clean up {filepath}: {e}")
    
    if deleted_count > 0:
        logger.info(f"✅ Cleaned up {deleted_count} expired temp PDFs from {temp_dir}")
    
    return deleted_count


