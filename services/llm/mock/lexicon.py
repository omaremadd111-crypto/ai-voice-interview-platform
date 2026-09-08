"""Keyword lexicon for deterministic skill/topic extraction in Mock Mode.

Each canonical term maps to one or more literal surface forms, matched as
whole words/phrases (case-insensitive) against job/candidate/answer text.
Reusable across any company or role -- nothing here is FlairsTech-specific.
"""

LEXICON: dict[str, tuple[str, ...]] = {
    "python": ("python",),
    "java": ("java",),
    "javascript": ("javascript", "typescript", "node.js", "nodejs"),
    "sql": ("sql", "postgres", "postgresql", "mysql"),
    "nosql": ("nosql", "mongodb", "dynamodb"),
    "machine_learning": ("machine learning", "scikit-learn", "sklearn", "ml"),
    "deep_learning": ("deep learning", "neural network", "pytorch", "tensorflow"),
    "nlp": ("nlp", "natural language processing", "tokenization", "text classification"),
    "llm": ("llm", "large language model", "gpt", "prompt engineering"),
    "rag": ("rag", "retrieval augmented generation", "retrieval-augmented generation"),
    "vector_database": ("vector database", "vector db", "pinecone", "faiss", "chroma", "embeddings"),
    "retrieval": ("retrieval", "semantic search", "hybrid search", "reranking", "rerank"),
    "evaluation": ("evaluation", "eval set", "benchmark", "hallucination", "ground truth"),
    "api_development": ("api", "api development", "rest api", "fastapi", "flask", "endpoint"),
    "cloud": ("cloud", "aws", "azure", "gcp"),
    "containers": ("docker", "container", "containers", "kubernetes", "k8s"),
    "testing": ("unit test", "pytest", "test coverage", "tdd", "testing"),
    "version_control": ("git", "github", "version control", "pull request"),
    "data_engineering": ("data pipeline", "etl", "data engineering", "airflow"),
    "system_design": ("system design", "architecture", "scalability", "distributed system"),
    "communication": (
        "communication", "communicated", "communicating", "communicate", "presented", "presenting",
        "explained", "explaining", "collaborated", "collaborating", "collaborate", "stakeholder",
    ),
    "leadership": (
        "led the team", "leading the team", "mentored", "mentoring",
        "leadership", "managed a team", "managing a team",
    ),
    "problem_solving": ("problem solving", "debugged", "root cause", "optimized", "troubleshoot"),
}
