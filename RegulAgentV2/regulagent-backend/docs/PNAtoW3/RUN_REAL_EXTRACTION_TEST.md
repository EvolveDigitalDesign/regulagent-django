# Real W-3A Extraction Test

This is a **real integration test** that actually sends the W-3A PDF to OpenAI and captures the actual JSON response.

**NOT mock tests** - actual API calls with real data.

---

## Quick Start

### Option 1: Using Django Shell (Simplest)

```bash
cd /Users/ru/Git/JMR/RegulatoryAgent/regulagent-django/RegulAgentV2/regulagent-backend

# Activate virtual environment (if needed)
source ../../../jmr-env/bin/activate

# Run the test
python manage.py shell < apps/public_core/tests/test_w3_extraction_real.py
```

### Option 2: Using Docker

```bash
cd /Users/ru/Git/JMR/RegulatoryAgent/regulagent-django/RegulAgentV2/regulagent-backend

docker-compose exec web python manage.py shell < apps/public_core/tests/test_w3_extraction_real.py
```

### Option 3: Direct Execution

```bash
cd /Users/ru/Git/JMR/RegulatoryAgent/regulagent-django/RegulAgentV2/regulagent-backend

python manage.py shell
# Then in the shell:
exec(open('apps/public_core/tests/test_w3_extraction_real.py').read())
```

---

## What the Test Does

1. **Locates the real W-3A PDF**
   - File: `tmp/W3A_Examples/Approved_W3A_00346118_20250826_214942_.pdf`
   - Actual approved RRC form

2. **Sends to OpenAI**
   - Calls `extract_w3a_from_pdf()`
   - Makes real API request with doc_type="w3a"
   - Gets actual structured JSON response

3. **Validates Response**
   - Checks required sections exist (header, casing_record, perforations, duqw)
   - Validates structure completeness
   - Logs extraction summary

4. **Displays Results**
   - Pretty-prints full extracted JSON
   - Shows summary statistics
   - Saves results to `tmp/w3a_extracted.json`

---

## Expected Output

```
🚀 Starting real W-3A extraction test...
================================================================================
INFO - 📄 Testing real W-3A extraction
INFO -    PDF: /path/to/Approved_W3A_00346118_20250826_214942_.pdf
INFO -    Size: 6891 bytes
INFO - 🔄 Sending PDF to OpenAI...
INFO - ✅ Extraction successful!
INFO -    Response size: 2500 chars

📋 EXTRACTED W-3A DATA:

{
  "header": {
    "api_number": "00346118",
    "well_name": "Test Well",
    "operator": "Test Operator",
    "county": "ANDREWS",
    "rrc_district": "08",
    "field": "SPRABERRY [TREND AREA]",
    ...
  },
  "casing_record": [
    {
      "string_type": "surface",
      "size_in": 11.75,
      "hole_size_in": 14.75,
      "top_ft": 0,
      "bottom_ft": 1717,
      ...
    },
    ...
  ],
  "perforations": [...],
  "plugging_proposal": [...],
  "duqw": {...}
}

📊 EXTRACTION SUMMARY:
   API Number: 00346118
   Well Name: Test Well
   Operator: Test Operator
   County: ANDREWS
   RRC District: 08
   Field: SPRABERRY [TREND AREA]
   Total Depth: 11200 ft
   Casing Strings: 3
      - Surface: 11.75" @ 0-1717 ft
      - Intermediate: 8.625" @ 1717-5532 ft
      - Production: 5.5" @ 5532-11200 ft
   Perforations: 1
      - 10964-10864 ft (Spraberry, open)
   Plugging Proposal: 8 plugs
      - Plug 1: 7990-7890 ft (cement_plug)
      - Plug 2: 7047-6947 ft (cement_plug)
      - Plug 3: 5582-4970 ft (cement_plug)
      - Plug 4: 4500-4300 ft (cement_plug)
      ... and 4 more plugs
   DUQW: 3250 ft (Santa Rosa)

✅ REAL EXTRACTION TEST PASSED!

💾 Saving extracted JSON...
✅ Saved to: /path/to/tmp/w3a_extracted.json
   Size: 2500 bytes

================================================================================
```

---

## Output Files

After running, you'll have:

1. **Console Output**
   - Full extracted JSON
   - Extraction summary
   - Validation results

2. **Saved JSON**
   - File: `tmp/w3a_extracted.json`
   - Full response for inspection
   - Can be used for testing downstream services

---

## Troubleshooting

### "ModuleNotFoundError: No module named 'django'"
**Solution:** Activate virtual environment first
```bash
source ../../../jmr-env/bin/activate
python manage.py shell < apps/public_core/tests/test_w3_extraction_real.py
```

### "PDF not found"
**Solution:** Make sure you're in the correct directory
```bash
# Should be in:
/Users/ru/Git/JMR/RegulatoryAgent/regulagent-django/RegulAgentV2/regulagent-backend
```

### "OpenAI API error"
**Solution:** Check environment variables
```bash
# Make sure OPENAI_API_KEY is set
echo $OPENAI_API_KEY

# If not set:
export OPENAI_API_KEY="your-key-here"
```

### "Connection error"
**Solution:** Check internet connectivity and OpenAI service
- Verify OpenAI API is accessible
- Check API key is valid
- Check quota/rate limits

---

## What Gets Tested

✅ **Real PDF to JSON extraction**
✅ **OpenAI API integration**
✅ **Response structure validation**
✅ **Data completeness**
✅ **Logging and error handling**
✅ **JSON serialization**

---

## Using Extracted Data

The extracted JSON from `tmp/w3a_extracted.json` can be used for:

1. **Testing downstream services** (W3Builder, etc.)
2. **Understanding OpenAI response structure**
3. **Comparing different PDF extractions**
4. **Building test fixtures**

Example:
```python
import json

with open('tmp/w3a_extracted.json') as f:
    w3a_data = json.load(f)

# Use in tests
builder = W3Builder(w3a_data)
w3_form = builder.build_w3_form(events)
```

---

## Next Steps

After successful extraction:
1. Review the extracted JSON structure
2. Update `IMPLEMENTATION_PLAN.md` with actual OpenAI response schema
3. Use extracted JSON for Phase 5 (W3Builder) testing
4. Validate output matches expected W-3A structure









