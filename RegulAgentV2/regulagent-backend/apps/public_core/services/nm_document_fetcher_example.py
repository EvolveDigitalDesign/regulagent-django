"""
NM Document Fetcher - Usage Examples

This script demonstrates how to use the NM OCD document fetcher service.
"""

from nm_document_fetcher import NMDocumentFetcher, list_nm_documents


def example_list_documents():
    """Example: List all documents for a well."""
    print("Example 1: List all documents for a well")
    print("-" * 50)

    api = "30-015-28692"  # Example NM well

    # Using the context manager (recommended)
    with NMDocumentFetcher() as fetcher:
        documents = fetcher.list_documents(api)

        print(f"Found {len(documents)} documents for API {api}:")
        for doc in documents:
            print(f"  - {doc.filename} ({doc.doc_type or 'Unknown type'})")
            print(f"    URL: {doc.url}")

    print()


def example_download_single_document():
    """Example: Download a specific document."""
    print("Example 2: Download a specific document")
    print("-" * 50)

    api = "30-015-28692"

    with NMDocumentFetcher() as fetcher:
        documents = fetcher.list_documents(api)

        if documents:
            # Download the first document as an example
            doc = documents[0]
            print(f"Downloading: {doc.filename}")

            content = fetcher.download_document(doc)

            # Save to file
            output_path = f"/tmp/{doc.filename}"
            with open(output_path, "wb") as f:
                f.write(content)

            print(f"Saved to: {output_path} ({len(content):,} bytes)")
        else:
            print("No documents found")

    print()


def example_download_all_documents():
    """Example: Download all documents for a well."""
    print("Example 3: Download all documents for a well")
    print("-" * 50)

    api = "30-015-28692"

    with NMDocumentFetcher() as fetcher:
        results = fetcher.download_all_documents(api)

        print(f"Downloaded {len(results)} documents:")
        for doc, content in results:
            print(f"  - {doc.filename}: {len(content):,} bytes")

            # Save to file
            output_path = f"/tmp/{doc.filename}"
            with open(output_path, "wb") as f:
                f.write(content)
            print(f"    Saved to: {output_path}")

    print()


def example_filter_by_type():
    """Example: Filter documents by type."""
    print("Example 4: Filter documents by type")
    print("-" * 50)

    api = "30-015-28692"

    with NMDocumentFetcher() as fetcher:
        all_documents = fetcher.list_documents(api)

        # Filter for C-103 forms (plugging)
        c103_docs = [doc for doc in all_documents if doc.doc_type == "C-103"]
        print(f"Found {len(c103_docs)} C-103 (Plugging) documents")

        # Filter for C-105 forms (completion)
        c105_docs = [doc for doc in all_documents if doc.doc_type == "C-105"]
        print(f"Found {len(c105_docs)} C-105 (Completion) documents")

        # Filter for C-101 forms (drilling permit)
        c101_docs = [doc for doc in all_documents if doc.doc_type == "C-101"]
        print(f"Found {len(c101_docs)} C-101 (Drilling Permit) documents")

    print()


def example_convenience_function():
    """Example: Use convenience function for quick listing."""
    print("Example 5: Use convenience function")
    print("-" * 50)

    # Quick way to list documents without managing the fetcher instance
    documents = list_nm_documents("30-015-28692")

    print(f"Found {len(documents)} documents using convenience function")
    for doc in documents:
        print(f"  - {doc.filename}")

    print()


def example_get_combined_pdf_url():
    """Example: Get the URL for combined PDF download."""
    print("Example 6: Get combined PDF URL")
    print("-" * 50)

    api = "30-015-28692"

    with NMDocumentFetcher() as fetcher:
        url = fetcher.get_combined_pdf_url(api)
        print(f"Combined PDF URL for {api}:")
        print(f"  {url}")
        print("\nNote: This URL may require form submission or JavaScript")
        print("to actually download the combined PDF.")

    print()


def example_error_handling():
    """Example: Handle errors gracefully."""
    print("Example 7: Error handling")
    print("-" * 50)

    # Invalid API
    try:
        with NMDocumentFetcher() as fetcher:
            documents = fetcher.list_documents("123")  # Too short
    except ValueError as e:
        print(f"Caught error for invalid API: {e}")

    # Handle download failures
    with NMDocumentFetcher() as fetcher:
        results = fetcher.download_all_documents("30-015-28692")

        successful = len(results)
        print(f"Successfully downloaded {successful} documents")
        print("Note: Failed downloads are logged but don't stop the process")

    print()


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("NM OCD Document Fetcher - Usage Examples")
    print("=" * 60 + "\n")

    # Run all examples
    example_list_documents()
    example_get_combined_pdf_url()
    example_filter_by_type()
    example_convenience_function()
    example_error_handling()

    # Note: Download examples are commented out to avoid
    # making actual downloads during demo
    # example_download_single_document()
    # example_download_all_documents()

    print("\n" + "=" * 60)
    print("Examples complete!")
    print("=" * 60 + "\n")
