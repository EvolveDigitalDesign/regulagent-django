from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from apps.public_core.services.rrc_completions_extractor import extract_completions_all_documents
from apps.public_core.services.openai_extraction import classify_document, extract_json_from_pdf, iter_json_sections_for_embedding, _embed_texts
from apps.public_core.services.api_normalization import normalize_api_14digit
from apps.public_core.models import ExtractedDocument, DocumentVector, WellRegistry
from django.db import transaction
from pathlib import Path


class RRCCompletionsExtractView(APIView):
    """Extract and vectorize documents from RRC completion records."""

    def post(self, request):
        api14 = (request.data or {}).get("api14") or (request.query_params.get("api14") if request else None)
        if not api14:
            return Response({"detail": "api14 is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result = extract_completions_all_documents(str(api14))
            # After downloads, run extraction pipeline per file
            files = result.get("files") or []
            api = result.get("api") or str(api14)
            api_normalized = normalize_api_14digit(api)
            well = WellRegistry.objects.filter(api14=api_normalized).first() if api_normalized else None
            created: list[dict] = []
            for f in files:
                path = f.get("path")
                if not path:
                    continue
                doc_type = classify_document(Path(path))
                if doc_type not in ("gau", "w2", "w15", "schematic", "formation_tops"):
                    continue
                ext = extract_json_from_pdf(Path(path), doc_type)
                with transaction.atomic():
                    ed = ExtractedDocument.objects.create(
                        well=well,
                        api_number=api,
                        document_type=doc_type,
                        source_path=path,
                        model_tag=ext.model_tag,
                        status="success" if not ext.errors else "error",
                        errors=ext.errors,
                        json_data=ext.json_data,
                    )
                    # Vectorize required sections
                    try:
                        section_pairs = list(iter_json_sections_for_embedding(doc_type, ext.json_data))
                        if section_pairs:
                            texts = [text for _, text in section_pairs]
                            embeddings = _embed_texts(texts)
                            for (section_name, section_text), emb in zip(section_pairs, embeddings):
                                DocumentVector.objects.create(
                                    well=well,
                                    file_name=Path(path).name,
                                    document_type=doc_type,
                                    section_name=section_name,
                                    section_text=section_text,
                                    embedding=emb,
                                    metadata={"extracted_document_id": str(ed.id)},
                                )
                    except Exception:
                        pass
                created.append({"document_type": doc_type, "extracted_document_id": str(ed.id)})
            result["extracted_documents"] = created
            return Response(result, status=status.HTTP_200_OK)
        except ValueError as ve:
            return Response({"detail": str(ve)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


