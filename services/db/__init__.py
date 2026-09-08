"""PostgreSQL integration module.

This is the ONLY package that may import sqlalchemy or psycopg. Everything outside
services/db/ depends on the repository interfaces and Pydantic models they return,
never on the ORM types in orm_models.py.
"""
