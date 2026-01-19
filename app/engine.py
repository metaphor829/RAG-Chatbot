from __future__ import annotations

from pathlib import Path
from typing import Optional
import os
import logging
import time

from llama_index.core import Settings, VectorStoreIndex, Document
from llama_index.core import StorageContext, load_index_from_storage
from llama_index.llms.openai import OpenAI
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.pipeline.standard_pdf_pipeline import ThreadedPdfPipelineOptions
from docling.datamodel.accelerator_options import AcceleratorOptions
import re


class RagEngine:
    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = storage_dir
        self._index: Optional[VectorStoreIndex] = None
        self._last_doc: Optional[Path] = None
        self._ready = False
        self._indexing = False
        self._docling_converter: Optional[DocumentConverter] = None

        api_key = os.getenv("OPENAI_API_KEY")
        api_base = os.getenv("OPENAI_API_BASE", "https://openrouter.ai/api/v1")
        default_headers = {}
        referer = os.getenv("OPENROUTER_APP_URL")
        app_name = os.getenv("OPENROUTER_APP_NAME")
        if referer:
            default_headers["HTTP-Referer"] = referer
        if app_name:
            default_headers["X-Title"] = app_name

        Settings.llm = OpenAI(
            model=os.getenv("LLM_MODEL", "openai/gpt-4o"),
            api_key=api_key,
            base_url=api_base,
            default_headers=default_headers or None,
        )
        embed_device = os.getenv("EMBED_DEVICE", "cpu")
        Settings.embed_model = HuggingFaceEmbedding(
            model_name=os.getenv("EMBED_MODEL", "BAAI/bge-m3"),
            device=embed_device,
        )

    def _env_bool(self, name: str, default: bool) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    def _build_docling_converter(self) -> DocumentConverter:
        device = os.getenv("DOCLING_DEVICE", "cuda")
        pipeline_options = ThreadedPdfPipelineOptions(
            accelerator_options=AcceleratorOptions(device=device),
            do_ocr=self._env_bool("DOCLING_OCR", False),
            do_table_structure=self._env_bool("DOCLING_TABLES", False),
            do_picture_classification=self._env_bool("DOCLING_PICTURES", False),
            do_picture_description=self._env_bool("DOCLING_PICTURES", False),
            generate_page_images=self._env_bool("DOCLING_PICTURES", False),
            generate_picture_images=self._env_bool("DOCLING_PICTURES", False),
            generate_table_images=self._env_bool("DOCLING_TABLES", False),
        )
        return DocumentConverter(
            allowed_formats=[InputFormat.PDF],
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            },
        )

    def _docling_to_documents(self, pdf_path: Path) -> list[Document]:
        if self._docling_converter is None:
            self._docling_converter = self._build_docling_converter()
        result = self._docling_converter.convert(str(pdf_path))
        doc = result.document
        markdown = doc.export_to_markdown()
        return self._markdown_to_chunks(markdown, source=str(pdf_path))

    def _extract_law_metadata(self, text: str) -> tuple[Optional[str], Optional[str]]:
        law_name = None
        law_id = None
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                law_name = line.lstrip("#").strip()
                break
            law_name = line
            break
        if law_name:
            match = re.search(r"(第[零一二三四五六七八九十百千万\\d]+号)", law_name)
            if match:
                law_id = match.group(1)
        return law_name, law_id

    def _parse_section_number(self, title: str) -> Optional[str]:
        match = re.search(r"(第[零一二三四五六七八九十百千万\\d]+[章节条])", title)
        return match.group(1) if match else None

    def _markdown_to_chunks(self, markdown: str, source: str) -> list[Document]:
        chunk_by_headers = self._env_bool("DOCLING_CHUNK_BY_HEADERS", True)
        if not chunk_by_headers:
            return [Document(text=markdown, metadata={"source": source})]

        law_name, law_id = self._extract_law_metadata(markdown)
        documents: list[Document] = []
        heading_stack: list[tuple[int, str]] = []
        current_title = "前文"
        current_level = 0
        buffer: list[str] = []

        def flush() -> None:
            if not buffer:
                return
            section_text = "\n".join(buffer).strip()
            if not section_text:
                return
            section_path = " > ".join([h[1] for h in heading_stack]) if heading_stack else current_title
            metadata = {
                "source": source,
                "law_name": law_name,
                "law_id": law_id,
                "section_title": current_title,
                "section_level": current_level,
                "section_number": self._parse_section_number(current_title),
                "section_path": section_path,
                "format": "markdown",
            }
            documents.append(Document(text=section_text, metadata=metadata))
            buffer.clear()

        for line in markdown.splitlines():
            if line.startswith("#"):
                flush()
                level = len(line) - len(line.lstrip("#"))
                title = line.lstrip("#").strip() or "無題"
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, title))
                current_title = title
                current_level = level
                continue
            buffer.append(line)

        flush()
        return documents

    async def index_directory(self, directory: Path) -> None:
        self._indexing = True
        self._ready = False
        pdf_files = sorted(directory.glob("*.pdf"))
        if not pdf_files:
            logging.info("No PDF files found in %s", directory)
            self._indexing = False
            return
        logging.info("Indexing %d PDF file(s) from %s", len(pdf_files), directory)
        logging.info("Loading embedding model...")
        start_time = time.perf_counter()
        documents: list[Document] = []
        for path in pdf_files:
            documents.extend(self._docling_to_documents(path))
        logging.info("PDF load completed in %.2fs", time.perf_counter() - start_time)
        logging.info("Building vector index...")
        build_start = time.perf_counter()
        index = VectorStoreIndex.from_documents(documents)
        logging.info("Vector index built in %.2fs", time.perf_counter() - build_start)
        index.storage_context.persist(persist_dir=str(self.storage_dir))
        logging.info("Index persisted to %s", self.storage_dir)

        self._index = index
        self._last_doc = pdf_files[-1]
        self._ready = True
        self._indexing = False

    def status(self) -> dict:
        return {
            "ready": self._ready,
            "indexing": self._indexing,
            "has_index": self._index is not None,
            "last_doc": str(self._last_doc) if self._last_doc else None,
        }

    def has_persisted_index(self) -> bool:
        return self.storage_dir.exists() and any(self.storage_dir.iterdir())

    def load_persisted_index(self) -> bool:
        index = self._load_index()
        if index is None:
            return False
        self._index = index
        self._ready = True
        self._indexing = False
        return True

    def _load_index(self) -> Optional[VectorStoreIndex]:
        if not self.storage_dir.exists():
            return None
        try:
            storage_context = StorageContext.from_defaults(persist_dir=str(self.storage_dir))
            return load_index_from_storage(storage_context)
        except Exception:
            return None

    def get_query_engine(self):
        if self._index is None:
            self._index = self._load_index()
        if self._index is None:
            return None
        return self._index.as_query_engine(streaming=True)
