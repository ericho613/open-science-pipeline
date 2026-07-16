# Open Science Canada PDF Pipeline

Scrapes academic article links from
[open-science.canada.ca](https://open-science.canada.ca/sitemap_index.html),
downloads PDFs, parses them with **GROBID** into TEI/XML sections, extracts
figure/table images, uploads images to **Amazon S3**, generates vector
embeddings (Amazon Bedrock Titan) with metadata + APA citations (Claude Haiku),
and upserts the vectors into **Pinecone**.

## Pipeline

1. **Scrape** sitemap index → child sitemaps → `/items/<uuid>` links.
2. **Resolve** PDF download URLs with a scaled pool of Playwright workers.
3. **Download** PDFs concurrently (asyncio).
4. **Parse** each PDF with GROBID → TEI/XML → sections + figure coordinates.
5. **Crop** figures/tables to PNG with PyMuPDF → upload to S3.
6. **Embed** sections with Titan, generate APA citation with Claude Haiku.
7. **Upsert** vectors into Pinecone (one namespace per article = dedup).
8. **Cleanup** PDF + TEI artifacts.

## Deduplication

Each article is stored under a Pinecone **namespace** equal to its bitstream
UUID. Before processing, the namespace is checked — if vectors already exist,
the article is skipped, guaranteeing single ingestion.

## Setup

### Prerequisites
- [uv](https://github.com/astral-sh/uv)
- Docker + Docker Compose
- AWS credentials with Bedrock + S3 access
- A Pinecone API key

### Install (local, uv)
```bash
uv venv
uv pip install -r requirements.txt
uv run playwright install --with-deps chromium
cp .env-example .env   # fill in values
```

### Run GROBID locally
```bash
docker run -d -p 8070:8070 lfoppiano/grobid:0.8.1
```

### Run the pipeline
```bash
uv run python -m src.main
```

## Run with Docker Compose (recommended)
```bash
cp .env-example .env   # fill in values
docker compose up --build
```
GROBID starts first; the pipeline waits for it to be healthy.

## Environment variables

| Variable | Description |
|---|---|
| `NUM_PDFS_TO_PROCESS` | Number of PDFs to process (blank = all) |
| `MAX_PLAYWRIGHT_WORKERS` | Max Playwright workers (cap 5) |
| `PDFS_PER_WORKER` | +1 worker per N PDFs |
| `GROBID_URL` | GROBID service URL |
| `EMBEDDING_MODEL` | Bedrock embedding model id |
| `CITATION_MODEL` | Bedrock citation model id |
| `S3_BUCKET_NAME` | S3 bucket for figure images |
| `S3_PUBLIC_BASE_URL` | Optional CDN/public base URL |
| `PINECONE_API_KEY` | Pinecone API key |
| `PINECONE_INDEX_NAME` | Pinecone index name |
| `PINECONE_DIMENSION` | Embedding dimension (Titan v2 = 1024) |
| `MAX_DOWNLOAD_CONCURRENCY` | Concurrent PDF downloads |
| `MAX_PROCESS_WORKERS` | Threads for GROBID/embedding work |

## Notes
- Titan v2 supports `dimensions` of 256/512/1024 — keep `PINECONE_DIMENSION`
  matched to it.
- PDFs and TEI artifacts are deleted after each article is processed.