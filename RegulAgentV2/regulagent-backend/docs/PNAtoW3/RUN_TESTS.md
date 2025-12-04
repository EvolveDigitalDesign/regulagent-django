# Running W-3A Extraction Tests

## Using Django Test Suite (Recommended)

```bash
# Run all W-3A extraction tests
python manage.py test apps.public_core.tests.test_w3_extraction

# Run with verbose output
python manage.py test apps.public_core.tests.test_w3_extraction -v 2

# Run specific test class
python manage.py test apps.public_core.tests.test_w3_extraction.TestValidateW3AStructure

# Run specific test method
python manage.py test apps.public_core.tests.test_w3_extraction.TestValidateW3AStructure.test_valid_w3a_structure
```

## Using pytest (if configured)

```bash
# Run all tests in file
pytest apps/public_core/tests/test_w3_extraction.py -v

# Run with coverage
pytest apps/public_core/tests/test_w3_extraction.py -v --cov=apps.public_core.services.w3_extraction

# Run specific test class
pytest apps/public_core/tests/test_w3_extraction.py::TestValidateW3AStructure -v

# Run specific test method
pytest apps/public_core/tests/test_w3_extraction.py::TestValidateW3AStructure::test_valid_w3a_structure -v
```

## Using Docker (Inside Container)

```bash
# Connect to running container
docker exec -it regulagent_web bash

# Then run tests inside container
cd /app && python manage.py test apps.public_core.tests.test_w3_extraction -v 2

# Or using pytest
cd /app && pytest apps/public_core/tests/test_w3_extraction.py -v
```

## Expected Test Results

All 21 tests should pass:

```
test_valid_w3a_structure ... ok
test_missing_header_section ... ok
test_missing_casing_record_section ... ok
test_missing_api_number ... ok
test_empty_header ... ok
test_successful_extraction ... ok
test_extraction_with_warnings ... ok
test_extraction_missing_required_section ... ok
test_extraction_raises_exception ... ok
test_load_from_regulagent_db ... ok
test_load_from_pdf_upload ... ok
test_load_invalid_reference_type ... ok
test_load_pdf_without_request ... ok
test_missing_w3a_id ... ok
test_database_loading_not_implemented ... ok
test_successful_pdf_upload ... ok
test_missing_w3a_file ... ok
test_temp_file_cleanup ... ok
test_w3a_example_structure_from_pdf_content ... ok

Ran 21 tests in 0.xxx seconds
OK
```

## Test File Location

```
apps/public_core/tests/test_w3_extraction.py
```

## Coverage

21 test methods covering:
- ✅ Structure validation (7 tests)
- ✅ PDF extraction (4 tests)
- ✅ Form loading (4 tests)
- ✅ Database loading (2 tests)
- ✅ PDF upload (3 tests)
- ✅ Integration tests (1 comprehensive test)

## Running from Your Terminal

```bash
# Navigate to project
cd /Users/ru/Git/JMR/RegulatoryAgent/regulagent-django/RegulAgentV2/regulagent-backend

# Run tests
python manage.py test apps.public_core.tests.test_w3_extraction -v 2
```



