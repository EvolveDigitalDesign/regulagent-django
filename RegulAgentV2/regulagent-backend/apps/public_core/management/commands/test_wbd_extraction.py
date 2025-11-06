"""
Test command to extract wellbore schematic data using OpenAI Vision API.

This command demonstrates what data can be extracted from wellbore diagrams
and how to structure it into JSON for perforate & squeeze detection.
"""

import base64
import json
from pathlib import Path
from typing import Dict, Any

from django.core.management.base import BaseCommand
from openai import OpenAI

from apps.public_core.services.openai_config import get_openai_client, DEFAULT_CHAT_MODEL


class Command(BaseCommand):
    help = "Test wellbore schematic extraction using OpenAI Vision API"

    def add_arguments(self, parser):
        parser.add_argument(
            '--image',
            type=str,
            required=True,
            help='Path to wellbore schematic image (relative to project root or absolute)'
        )
        parser.add_argument(
            '--api',
            type=str,
            help='API number for context and W-2 cross-validation'
        )
        parser.add_argument(
            '--validate',
            action='store_true',
            help='Cross-validate extraction against W-2 data if available'
        )

    def handle(self, *args, **options):
        image_path_input = options['image']
        api_number = options.get('api', 'unknown')
        validate_w2 = options.get('validate', False)
        
        # Try multiple path resolution strategies
        image_path = None
        candidates = [
            Path(image_path_input),  # As provided
            Path.cwd() / image_path_input,  # Relative to CWD
            Path('/app') / image_path_input,  # Relative to Docker /app
        ]
        
        for candidate in candidates:
            if candidate.exists():
                image_path = candidate
                break
        
        if not image_path or not image_path.exists():
            self.stdout.write(self.style.ERROR(f'❌ Image not found. Tried:'))
            for c in candidates:
                self.stdout.write(f'   {c}')
            return
        
        self.stdout.write(self.style.SUCCESS(f'📄 Processing: {image_path.name}'))
        self.stdout.write(f'   API: {api_number}')
        
        # Get W-2 data for validation if requested
        w2_data = None
        if validate_w2 and api_number != 'unknown':
            w2_data = self.get_w2_data(api_number)
        
        # Extract data using Vision API
        result = self.extract_wellbore_schematic(image_path)
        
        if result:
            self.stdout.write(self.style.SUCCESS('\n✅ Extraction successful!'))
            
            # Cross-validate with W-2 if available
            if w2_data:
                result = self.validate_against_w2(result, w2_data)
            
            # Recompute annular gaps with corrected data
            result = self.recompute_annular_gaps(result)
            
            self.stdout.write('\n' + '='*80)
            self.stdout.write('📊 EXTRACTED DATA')
            self.stdout.write('='*80)
            self.stdout.write(json.dumps(result, indent=2))
            
            # Analyze for perforate & squeeze candidates
            self.analyze_perf_squeeze_candidates(result)
        else:
            self.stdout.write(self.style.ERROR('❌ Extraction failed'))

    def extract_wellbore_schematic(self, image_path: Path) -> Dict[str, Any]:
        """
        Extract structured data from wellbore schematic using OpenAI Vision API.
        """
        
        # Encode image to base64
        with open(image_path, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')
        
        # Determine image type
        image_type = image_path.suffix.lower().replace('.', '')
        if image_type == 'jpg':
            image_type = 'jpeg'
        
        # Build the extraction prompt
        prompt = self._build_extraction_prompt()
        
        client = get_openai_client()
        
        try:
            self.stdout.write('\n🤖 Calling OpenAI Vision API...')
            
            response = client.chat.completions.create(
                model=DEFAULT_CHAT_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert petroleum engineer specializing in wellbore construction and P&A operations. Extract structured data from wellbore schematics with precision."
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/{image_type};base64,{image_data}",
                                    "detail": "high"  # High detail for technical diagrams
                                }
                            }
                        ]
                    }
                ],
                max_tokens=4000,
                temperature=0.1  # Low temperature for factual extraction
            )
            
            # Parse JSON response
            content = response.choices[0].message.content
            
            # Extract JSON from markdown code blocks if present
            if '```json' in content:
                content = content.split('```json')[1].split('```')[0].strip()
            elif '```' in content:
                content = content.split('```')[1].split('```')[0].strip()
            
            result = json.loads(content)
            
            self.stdout.write(self.style.SUCCESS(f'   Tokens used: {response.usage.total_tokens}'))
            self.stdout.write(f'   Model: {response.model}')
            
            return result
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'   Error: {str(e)}'))
            return None

    def _build_extraction_prompt(self) -> str:
        """
        Build the extraction prompt for wellbore schematic parsing.
        """
        return """Extract all wellbore construction and cement data from this wellbore schematic diagram.

Return a JSON object with the following structure:

{
  "well_info": {
    "api_number": "string",
    "well_name": "string",
    "operator": "string"
  },
  "casing_strings": [
    {
      "string_type": "surface|intermediate|production|liner",
      "size_in": float,
      "weight_ppf": float,
      "grade": "string",
      "top_md_ft": float,
      "bottom_md_ft": float,
      "shoe_md_ft": float,
      "cement_job": {
        "cement_top_md_ft": float,
        "cement_bottom_md_ft": float,
        "sacks": int,
        "job_date": "string",
        "notes": "string"
      }
    }
  ],
  "formations": [
    {
      "name": "string",
      "top_md_ft": float,
      "base_md_ft": float
    }
  ],
  "producing_intervals": [
    {
      "top_md_ft": float,
      "bottom_md_ft": float,
      "formation": "string",
      "type": "perforated|open_hole",
      "status": "active|plugged|abandoned"
    }
  ],
  "historical_interventions": [
    {
      "type": "perforation|squeeze|plug|packer|bridge_plug|cibp",
      "top_md_ft": float,
      "bottom_md_ft": float,
      "date": "string",
      "notes": "string"
    }
  ],
  "annular_gaps": [
    {
      "description": "string",
      "inner_string": "string",
      "outer_string": "string",
      "top_md_ft": float,
      "bottom_md_ft": float,
      "cement_present": bool,
      "requires_isolation": bool
    }
  ],
  "perforations": [
    {
      "top_md_ft": float,
      "bottom_md_ft": float,
      "status": "open|squeezed|plugged"
    }
  ]
}

SPATIAL LAYOUT INSTRUCTIONS:
- Wellbore schematics typically show casing strings as VERTICAL COLUMNS from left to right
- Each column represents ONE casing string with its size, depths, and cement data
- Text annotations (TOC, shoe depth, cement top) are HORIZONTALLY ALIGNED with their column
- DO NOT mix data from different columns - read each column independently
- The rightmost area often has a tabular "Column List" with organized depth data

Instructions:
1. Read ALL text annotations carefully from EVERYWHERE in the diagram:
   - Column list tables (usually right side with depth columns)
   - Side margin notes and callouts
   - Vertical schematic annotations aligned with each casing column
   - Cement job description blocks
   
2. For each casing string (read as vertical columns), extract:
   - Size, weight, grade, top/bottom depths
   - **ACTUAL cement top depth** - this is CRITICAL and may appear as:
     * "TOC @ X ft" or "TOC X ft"
     * "Cement top X ft" or "Cement to X ft"
     * In a "Depth MD" column showing cement extent
     * In side annotations showing cement coverage
   - If you see "Production Casing Cement" or "Surface Casing Cement" WITHOUT a specific depth,
     look for nearby depth annotations - DO NOT assume it means "cemented to surface"
   
3. IMPORTANT: Distinguish between:
   - "Cement job label" (e.g., "Production Casing Cement, Date: 1949-11-09")
   - "Actual cement extent" (e.g., "TOC @ 5298 ft" or "Depth MD 5,298 ft")
   - If a cement job label has NO explicit top depth, set cement_top_md_ft to null

4. Look for cement extent in these specific places:
   - Tabular columns showing "Depth MD", "Length", "Cement" data
   - Visual cement fills (shaded/patterned areas) with depth annotations
   - Side-margin callouts with "TOC" or depth ranges
   - Historical cement squeeze annotations

5. Extract formation names and depths from formation markers

6. Identify producing intervals from perforation annotations or "production" zones

7. Find historical interventions (perfs, squeezes, plugs, packers, CIBPs) with depths
   - Look for annotations like "Perf X-Y ft on [date]" followed by "Cement" or "Squeeze"
   - These indicate historical squeeze jobs

8. After extracting all strings, compute annular_gaps:
   - For each pair of adjacent strings (outer/inner), check if outer cement top < inner string top
   - If there's a gap, flag it as requires_isolation=true

Key patterns to look for:
- "TOC @ X ft" or "TOC X ft" or "Top of cement X ft" or "Cement top X ft" or "Circ to X ft" = cement_top_md_ft
- "Depth MD: X ft" or "MD X.X ft" or "Depth MD X.X-Y.Y ft" = cement extent range
- "Cement X-Y ft" = range from Y (bottom) to X (top)
- "TD X ft" or "Bottom X ft" or "Shoe X ft" = bottom_md_ft/shoe_md_ft
- "5.5 in" or "5½ in" or "5-1/2 in" = production casing (IMPORTANT: look for this specifically)
- "Perf X-Y ft" or "Perforated X-Y ft" = perforation interval
- "Squeeze" or "Sqz" or annotation showing both "Perf" and "Cement" within 50 ft = historical squeeze
- "CIBP" or "Bridge Plug" or "Packer" = mechanical barrier

COMMON SIZES TO LOOK FOR (verify each exists as a separate column):
- Surface casing: typically 9.625" to 13.375", shallow depth (< 2000 ft)
- Intermediate casing: typically 7" to 10.75", mid-depth (2000-5000 ft)  
- Production casing: typically 4.5" to 7", deeper (5000-8000 ft) - LOOK FOR 5.5" specifically
- Liner: typically 2.875" to 5.5", deepest section, hung from inside production casing

CRITICAL SPATIAL RULE:
Associate each TOC/cement annotation with the casing column it is HORIZONTALLY ALIGNED with.
If a cement top annotation (e.g., "5298 ft") appears near a 5.5" casing column, 
assign cement_top_md_ft=5298 to that 5.5" string, NOT to a different column.

CRITICAL: Many old wells have cement that does NOT reach surface on production or intermediate strings.
Do not assume "cement to surface" unless explicitly stated. Look for the ACTUAL measured cement top depth.

Be precise with depths. If a cement top value is unclear or truly missing, set it to null (not 0).
Return ONLY valid JSON, no additional commentary."""

    def analyze_perf_squeeze_candidates(self, data: Dict[str, Any]):
        """
        Analyze extracted data to identify perforate & squeeze candidates.
        
        Uses the recomputed annular_gaps array which has already been validated.
        """
        self.stdout.write('\n' + '='*80)
        self.stdout.write('🔍 PERFORATE & SQUEEZE CANDIDATE ANALYSIS')
        self.stdout.write('='*80)
        
        # Read from the recomputed annular_gaps array
        annular_gaps = data.get('annular_gaps', [])
        
        # Filter for gaps that require isolation
        candidates = [gap for gap in annular_gaps if gap.get('requires_isolation')]
        
        # Display candidates
        if candidates:
            self.stdout.write(self.style.SUCCESS(f'\n✅ Found {len(candidates)} perforate & squeeze candidate(s):'))
            for i, candidate in enumerate(candidates, 1):
                self.stdout.write(f'\n{i}. {candidate.get("description")}')
                self.stdout.write(f'   Gap: {candidate.get("top_md_ft")} - {candidate.get("bottom_md_ft")} ft ({candidate.get("gap_size_ft")} ft)')
                self.stdout.write(f'   Outer string: {candidate.get("outer_string")}')
                self.stdout.write(f'   Inner string: {candidate.get("inner_string")}')
                self.stdout.write(f'   ⚠️  This gap will require perforate & squeeze per SWR-14(g)(2)')
        else:
            self.stdout.write(self.style.WARNING('\n⚠️  No perforate & squeeze candidates identified'))
        
        # Check for producing intervals in uncemented zones (additional analysis)
        producing_intervals = data.get('producing_intervals', [])
        casing_strings = data.get('casing_strings', [])
        
        for prod in producing_intervals:
            prod_bottom = prod.get('bottom_md_ft')
            
            # Find which casing string covers this interval
            for casing in casing_strings:
                casing_bottom = casing.get('bottom_md_ft', 0)
                cement_top = casing.get('cement_job', {}).get('cement_top_md_ft')
                
                if prod_bottom and cement_top and casing_bottom:
                    if prod_bottom <= casing_bottom and prod_bottom > cement_top:
                        candidates.append({
                            'target': 'producing_interval',
                            'formation': prod.get('formation'),
                            'prod_top_ft': prod.get('top_md_ft'),
                            'prod_bottom_ft': prod_bottom,
                            'cement_top_ft': cement_top,
                            'gap_size_ft': prod_bottom - cement_top,
                            'reason': f'Producing interval below cement top (no cement behind casing)'
                        })
        
        # Display candidates
        if candidates:
            self.stdout.write(self.style.SUCCESS(f'\n✅ Found {len(candidates)} perforate & squeeze candidate(s):'))
            for i, candidate in enumerate(candidates, 1):
                self.stdout.write(f'\n{i}. {candidate.get("reason")}')
                for key, value in candidate.items():
                    if key != 'reason':
                        self.stdout.write(f'   {key}: {value}')
        else:
            self.stdout.write(self.style.WARNING('\n⚠️  No perforate & squeeze candidates identified'))
        
        # Display annular gaps from extraction
        annular_gaps = data.get('annular_gaps', [])
        if annular_gaps:
            self.stdout.write('\n' + '='*80)
            self.stdout.write('📊 ANNULAR GAPS (from extraction)')
            self.stdout.write('='*80)
            for gap in annular_gaps:
                self.stdout.write(f'\n• {gap.get("description")}')
                self.stdout.write(f'  {gap.get("top_md_ft")}-{gap.get("bottom_md_ft")} ft')
                self.stdout.write(f'  Cement present: {gap.get("cement_present")}')
                self.stdout.write(f'  Requires isolation: {gap.get("requires_isolation")}')

    def get_w2_data(self, api_number: str) -> Dict[str, Any]:
        """
        Fetch W-2 data for cross-validation.
        """
        try:
            from apps.public_core.models import ExtractedDocument
            
            w2 = ExtractedDocument.objects.filter(
                api_number=api_number,
                document_type='w2'
            ).order_by('-created_at').first()
            
            if w2:
                self.stdout.write(self.style.SUCCESS(f'\n📄 Found W-2 data for validation'))
                return w2.json_data
            else:
                self.stdout.write(self.style.WARNING(f'\n⚠️  No W-2 data found for API {api_number}'))
                return None
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'\n⚠️  Error fetching W-2: {str(e)}'))
            return None

    def validate_against_w2(self, schematic_data: Dict[str, Any], w2_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Cross-validate and correct schematic extraction using trusted W-2 data.
        
        Sanity checks:
        - Casing sizes and depths should match within ±500 ft
        - Cement tops should match within ±500 ft
        - If discrepancy > 500 ft, use W-2 value and flag warning
        """
        self.stdout.write('\n' + '='*80)
        self.stdout.write('🔍 W-2 CROSS-VALIDATION')
        self.stdout.write('='*80)
        
        w2_casing = w2_data.get('casing_record', [])
        schematic_casing = schematic_data.get('casing_strings', [])
        
        corrections_made = []
        
        # Build W-2 lookup by string type
        w2_lookup = {}
        for casing in w2_casing:
            string_type = casing.get('string', '').lower()
            w2_lookup[string_type] = {
                'size': casing.get('size_in'),
                'bottom': casing.get('bottom_ft'),
                'cement_top': casing.get('cement_top_ft')
            }
        
        # Validate each schematic string against W-2
        for i, schem_string in enumerate(schematic_casing):
            string_type = schem_string.get('string_type', '').lower()
            
            if string_type in w2_lookup:
                w2 = w2_lookup[string_type]
                
                # Check size
                schem_size = schem_string.get('size_in')
                w2_size = w2.get('size')
                if schem_size and w2_size and abs(schem_size - w2_size) > 0.5:
                    self.stdout.write(self.style.WARNING(
                        f'  ⚠️  {string_type} size mismatch: schematic={schem_size}", W-2={w2_size}" - using W-2'
                    ))
                    schematic_casing[i]['size_in'] = w2_size
                    corrections_made.append(f'{string_type} size corrected to {w2_size}"')
                
                # Check bottom depth
                schem_bottom = schem_string.get('bottom_md_ft')
                w2_bottom = w2.get('bottom')
                if schem_bottom and w2_bottom and abs(schem_bottom - w2_bottom) > 500:
                    self.stdout.write(self.style.WARNING(
                        f'  ⚠️  {string_type} bottom depth mismatch: schematic={schem_bottom}ft, W-2={w2_bottom}ft - using W-2'
                    ))
                    schematic_casing[i]['bottom_md_ft'] = w2_bottom
                    schematic_casing[i]['shoe_md_ft'] = w2_bottom
                    corrections_made.append(f'{string_type} bottom corrected to {w2_bottom}ft')
                
                # Check cement top (MOST CRITICAL)
                schem_cement = schem_string.get('cement_job', {}).get('cement_top_md_ft')
                w2_cement = w2.get('cement_top')
                if schem_cement is not None and w2_cement is not None:
                    if abs(schem_cement - w2_cement) > 500:
                        self.stdout.write(self.style.WARNING(
                            f'  ⚠️  {string_type} CEMENT TOP mismatch: schematic={schem_cement}ft, W-2={w2_cement}ft - using W-2'
                        ))
                        if 'cement_job' not in schematic_casing[i]:
                            schematic_casing[i]['cement_job'] = {}
                        schematic_casing[i]['cement_job']['cement_top_md_ft'] = w2_cement
                        corrections_made.append(f'{string_type} cement top CORRECTED to {w2_cement}ft')
                elif schem_cement is None and w2_cement is not None:
                    # Schematic missing cement top - use W-2
                    self.stdout.write(self.style.WARNING(
                        f'  ⚠️  {string_type} cement top missing in schematic, using W-2 value: {w2_cement}ft'
                    ))
                    if 'cement_job' not in schematic_casing[i]:
                        schematic_casing[i]['cement_job'] = {}
                    schematic_casing[i]['cement_job']['cement_top_md_ft'] = w2_cement
                    corrections_made.append(f'{string_type} cement top added from W-2: {w2_cement}ft')
        
        if corrections_made:
            self.stdout.write(self.style.SUCCESS(f'\n✅ Made {len(corrections_made)} corrections based on W-2 data'))
            for correction in corrections_made:
                self.stdout.write(f'   • {correction}')
        else:
            self.stdout.write(self.style.SUCCESS('\n✅ Schematic data matches W-2 (no corrections needed)'))
        
        schematic_data['casing_strings'] = schematic_casing
        return schematic_data

    def recompute_annular_gaps(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Recompute annular gaps after W-2 corrections.
        
        Rule: If outer string cement top < inner string top - 50 ft, there's an uncemented gap.
        """
        casing_strings = data.get('casing_strings', [])
        
        if len(casing_strings) < 2:
            return data
        
        # Sort by bottom depth (deepest first)
        casing_strings_sorted = sorted(casing_strings, key=lambda x: x.get('bottom_md_ft', 0), reverse=True)
        
        annular_gaps = []
        
        for i in range(len(casing_strings_sorted) - 1):
            inner = casing_strings_sorted[i]
            outer = casing_strings_sorted[i + 1]
            
            outer_cement_top = outer.get('cement_job', {}).get('cement_top_md_ft')
            inner_top = inner.get('top_md_ft', 0)
            
            if outer_cement_top is not None and inner_top is not None:
                # Check if there's a gap
                if outer_cement_top < inner_top - 50:
                    gap_size = inner_top - outer_cement_top
                    
                    annular_gaps.append({
                        'description': f'Uncemented annulus between {outer.get("string_type")} and {inner.get("string_type")}',
                        'outer_string': outer.get('string_type'),
                        'inner_string': inner.get('string_type'),
                        'top_md_ft': outer_cement_top,
                        'bottom_md_ft': inner_top,
                        'gap_size_ft': gap_size,
                        'cement_present': False,
                        'requires_isolation': True
                    })
        
        data['annular_gaps'] = annular_gaps
        return data

