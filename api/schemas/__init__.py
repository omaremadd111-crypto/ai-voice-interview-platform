"""FastAPI request/response schemas. Never expose ORM rows or repository record
models directly -- every endpoint has its own explicit schema here, even where
the shape closely mirrors a models/platform.py record."""
