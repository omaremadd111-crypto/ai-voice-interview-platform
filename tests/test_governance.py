"""Cross-cutting project governance checks."""
import ast
import json
from pathlib import Path

import pytest

ARCHITECTURE_FILE = "docs/ARCHITECTURE.md"

REQUIRED_ARCHITECTURE_BOUNDARIES = (
    "presentation layers are presentation only",
    "interviewagentservice",
    "must never become a second interview implementation",
    "must never become a second application pipeline",
    "provider sdks are confined",
    "mock mode is the default",
    "scoring is deterministic",
    "transcript-traceable evidence",
    "computed in python from `config/rubrics.json`",
)

PERMANENT_PROHIBITIONS = (
    "emotion analysis",
    "accent analysis",
    "personality inference",
    "biometric inference",
    "facial analysis",
    "protected-attribute inference",
    "hiring decisions based on voice characteristics",
    "autonomous final hire/reject decisions",
)


def _governed_text(project_root: Path, relative: str) -> str:
    return (project_root / relative).read_text(encoding="utf-8")


@pytest.mark.parametrize("boundary", REQUIRED_ARCHITECTURE_BOUNDARIES)
def test_architecture_document_preserves_system_boundaries(
    project_root: Path, boundary: str,
) -> None:
    text = _governed_text(project_root, ARCHITECTURE_FILE).casefold()
    assert boundary in text, f"{ARCHITECTURE_FILE} dropped '{boundary}'"


@pytest.mark.parametrize("prohibition", PERMANENT_PROHIBITIONS)
def test_governed_docs_retain_permanent_prohibitions(
    project_root: Path, prohibition: str,
) -> None:
    text = _governed_text(project_root, ARCHITECTURE_FILE).casefold()
    assert prohibition in text, (
        f"{ARCHITECTURE_FILE} dropped the prohibition on '{prohibition}'"
    )


def test_voice_is_only_a_transport_channel(project_root: Path) -> None:
    text = _governed_text(project_root, ARCHITECTURE_FILE).casefold()
    assert "only as a transport/conversation channel" in text
    assert "never become a second interview implementation" in text


def test_pstn_calling_is_not_implemented(project_root: Path) -> None:
    text = _governed_text(project_root, ARCHITECTURE_FILE).casefold()
    assert "does not include outbound pstn/telephone calling" in text
    assert "pstn" in text


def test_screening_outcomes_are_not_employment_decisions(project_root: Path) -> None:
    text = _governed_text(project_root, ARCHITECTURE_FILE).casefold()
    assert "initial screening result" in text
    assert '`pass` does not mean "hire"' in text
    assert "human recruiter remains the final decision maker" in text


def test_memory_and_retrieval_are_input_side_only(project_root: Path) -> None:
    """Mem0/Qdrant must never reach scoring -- that would break determinism and
    transcript-traceable evidence, the two invariants the product's credibility rests on."""
    text = _governed_text(project_root, ARCHITECTURE_FILE).casefold()
    assert "input-side only" in text
    assert "never reach the evaluator" in text


def test_auto_pipeline_never_duplicates_the_application_or_interview_pipeline(
    project_root: Path,
) -> None:
    """Mirrors the LiveKit-transport rule: the public application flow adapts the
    existing candidate/CV/interview-plan services and must never grow into a second
    implementation of either the application intake or the interview itself."""
    text = _governed_text(project_root, ARCHITECTURE_FILE).casefold()
    assert "never become a second application pipeline" in text


def test_auto_pipeline_approval_is_position_level(project_root: Path) -> None:
    """SPEC 7's human-in-the-loop guarantee moves from per-candidate to per-position
    for the automated pipeline: a human still approves every question a candidate
    will hear, but once, at the template level, and every applicant's plan is a
    copy of exactly what was approved -- never independently AI-generated content
    that skipped review."""
    text = _governed_text(project_root, ARCHITECTURE_FILE).casefold()
    assert "position-level screening template" in text
    assert "materialized from that approved template" in text


def test_invitation_email_is_documented_as_best_effort(project_root: Path) -> None:
    """The one behaviour guarantee email must never break: a failed or slow send
    can never be the reason a candidate cannot start their interview."""
    text = _governed_text(project_root, ARCHITECTURE_FILE).casefold()
    assert "best-effort and additive" in text
    assert "never block a candidate from starting" in text


def test_automated_reminders_are_not_implemented(project_root: Path) -> None:
    text = _governed_text(project_root, ARCHITECTURE_FILE).casefold()
    assert "automated reminder emails are not implemented" in text


def test_mistral_sdk_imports_are_confined_to_llm_services(project_root: Path) -> None:
    violations: list[str] = []
    for path in project_root.rglob("*.py"):
        relative = path.relative_to(project_root)
        if relative.parts[:2] == ("services", "llm"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == "mistralai" or alias.name.startswith("mistralai.") for alias in node.names):
                    violations.append(str(relative))
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "mistralai" or module.startswith("mistralai."):
                    violations.append(str(relative))

    assert violations == []


def test_email_sending_is_confined_to_email_services(project_root: Path) -> None:
    """SmtpEmailService is stdlib-only (smtplib/email.*) rather than a third-party
    SDK, but the same confinement rule applies: nothing outside services/email/
    may construct or send a message directly. Callers depend on EmailPort only,
    exactly like LLMService."""
    sending_modules = {"smtplib", "email.mime", "email.message"}
    violations: list[str] = []
    for path in project_root.rglob("*.py"):
        relative = path.relative_to(project_root)
        if relative.parts[:2] == ("services", "email") or _excluded_dirs(relative):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name in sending_modules or alias.name.startswith("smtplib.") for alias in node.names):
                    violations.append(str(relative))
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module in sending_modules or module.startswith("smtplib."):
                    violations.append(str(relative))

    assert violations == []


def _module_imports(tree: ast.Module) -> set[str]:
    """Every dotted module name this file imports, at any depth (import X.Y.Z and
    from X.Y import Z both yield 'X.Y.Z')."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _excluded_dirs(relative: Path) -> bool:
    # "tests" is excluded from these layering checks on purpose: test code's job
    # is to exercise internals (ORM rows, sqlalchemy sessions, the FastAPI
    # TestClient) directly for verification. The rule under test is about
    # application code architecture, not test code.
    # "frontend" holds the React app (TypeScript) and its node_modules; these
    # checks are about the Python backend's layering. "outputs" holds
    # generated/vendored artifacts (e.g. a node_modules tree pulled in by some
    # other tool run in this repo) that can bundle arbitrary vendored Python
    # (node-gyp vendors a copy of pip's own packaging module, for one) --
    # never this project's own backend code. node_modules is matched at any
    # depth, not just top-level, for the same reason.
    if "node_modules" in relative.parts:
        return True
    return relative.parts[0] in (
        ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "tests", "frontend",
        "outputs",
    )


def test_fastapi_imports_are_confined_to_the_api_layer(project_root: Path) -> None:
    """Mirrors 'no gr.* import outside ui/': no fastapi/starlette import may
    appear outside api/, so the presentation-only rule in docs/ARCHITECTURE.md applies to
    the API transport exactly like it already does to Gradio."""
    violations: list[str] = []
    for path in project_root.rglob("*.py"):
        relative = path.relative_to(project_root)
        if relative.parts[0] == "api" or _excluded_dirs(relative):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = _module_imports(tree)
        if any(name == "fastapi" or name.startswith("fastapi.") for name in imports):
            violations.append(str(relative))
        if any(name == "starlette" or name.startswith("starlette.") for name in imports):
            violations.append(str(relative))
    assert violations == []


def test_routers_never_import_sqlalchemy_or_orm_models_directly(project_root: Path) -> None:
    """Routers must depend on application services only (validate -> authorize
    -> call the service layer -> serialize); the composition root in
    api/dependencies.py is the one place allowed to wire concrete repositories."""
    violations: list[str] = []
    routers_dir = project_root / "api" / "routers"
    for path in routers_dir.rglob("*.py"):
        relative = path.relative_to(project_root)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = _module_imports(tree)
        if any(name == "sqlalchemy" or name.startswith("sqlalchemy.") for name in imports):
            violations.append(str(relative))
        if any(name.startswith("services.db.") or name == "services.db" for name in imports):
            violations.append(str(relative))
    assert violations == []


def test_orm_models_are_confined_to_the_persistence_layer(project_root: Path) -> None:
    """No caller outside services/db/ may import the raw SQLAlchemy row classes --
    everything else works through the repository interfaces and the Pydantic
    models in models/platform.py and models/session.py."""
    violations: list[str] = []
    for path in project_root.rglob("*.py"):
        relative = path.relative_to(project_root)
        if relative.parts[:2] == ("services", "db") or relative.parts[0] == "migrations" or _excluded_dirs(relative):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = _module_imports(tree)
        if any(name.startswith("services.db.orm_models") for name in imports):
            violations.append(str(relative))
    assert violations == []


def _is_allowed_sqlalchemy_importer(relative: Path) -> bool:
    if relative.parts[:2] == ("services", "db"):
        return True
    if relative.parts[0] == "migrations":
        return True
    # The composition root: the one place outside services/db/ that must know
    # about a Session/sessionmaker type to wire it into application services --
    # see test_routers_never_import_sqlalchemy_or_orm_models_directly for the
    # rule that actually matters, which is that ROUTERS never do this.
    return relative == Path("api") / "dependencies.py"


def test_sqlalchemy_imports_are_confined_to_the_persistence_layer_and_migrations(project_root: Path) -> None:
    """P2's confinement rule must not regress in P3: sqlalchemy/psycopg/alembic
    stay inside services/db/, migrations/, and api/dependencies.py."""
    violations: list[str] = []
    for path in project_root.rglob("*.py"):
        relative = path.relative_to(project_root)
        if _excluded_dirs(relative) or _is_allowed_sqlalchemy_importer(relative):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = _module_imports(tree)
        for sdk in ("sqlalchemy", "psycopg", "alembic"):
            if any(name == sdk or name.startswith(f"{sdk}.") for name in imports):
                violations.append(f"{relative} imports {sdk}")
    assert violations == []


def test_livekit_sdk_is_confined_and_memory_integrations_are_absent(project_root: Path) -> None:
    """P6 allows LiveKit only inside its integration package; Mem0/Qdrant wait."""
    forbidden_import_prefixes = ("mem0", "qdrant_client")
    import_violations: list[str] = []
    for path in project_root.rglob("*.py"):
        relative = path.relative_to(project_root)
        if _excluded_dirs(relative):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = _module_imports(tree)
        if relative.parts[:2] != ("services", "livekit") and any(
            name == "livekit" or name.startswith("livekit.") for name in imports
        ):
            import_violations.append(f"{relative} imports livekit outside services/livekit")
        for forbidden in forbidden_import_prefixes:
            if any(name == forbidden or name.startswith(f"{forbidden}.") for name in imports):
                import_violations.append(f"{relative} imports {forbidden}")
    assert import_violations == []

    path_violations = [
        str(p.relative_to(project_root))
        for p in project_root.rglob("*")
        if not _excluded_dirs(p.relative_to(project_root)) and p.name.lower() == "qdrant"
    ]
    assert path_violations == []


def test_frontend_declares_no_mem0_or_qdrant_dependency(project_root: Path) -> None:
    """P6 allows LiveKit's browser client; memory/vector-store clients still wait."""
    package_json = project_root / "frontend" / "package.json"
    if not package_json.exists():
        pytest.skip("frontend/package.json not present")

    manifest = json.loads(package_json.read_text(encoding="utf-8"))
    declared = {
        *manifest.get("dependencies", {}).keys(),
        *manifest.get("devDependencies", {}).keys(),
    }
    forbidden_markers = ("mem0", "qdrant")
    violations = [
        name for name in declared if any(marker in name.lower() for marker in forbidden_markers)
    ]
    assert violations == []


def test_public_router_has_no_authenticated_dependency(project_root: Path) -> None:
    """api/routers/public.py is the one router reachable with no session at all
    (see application/public_job_service.py's docstring) -- it must never import
    get_current_user the way every recruiter-facing router does. Checked via the
    import statement rather than a raw text search so the module's own docstring
    is free to explain this rule without tripping the check."""
    path = project_root / "api" / "routers" / "public.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "get_current_user" not in imported_names


def test_public_job_schema_never_exposes_recruiter_only_fields(project_root: Path) -> None:
    """api/schemas/public.py's *Response models are the entire "what can a
    stranger see" surface -- checked by field name via AST (no runtime import,
    matching this file's style) so a field added here fails immediately rather
    than depending on an API test remembering to check for it.

    Deliberately scoped to classes named "...Response": ApplyFields is a
    REQUEST model (what an applicant submits about themselves -- their own
    name, email, and phone are the entire point of an apply form, not a leak)
    and must not be checked against the same forbidden set as what the API
    hands back to an arbitrary caller.
    """
    forbidden = {
        "rubric_profile", "pass_score_threshold", "owner_id", "id", "status",
        "cv_text", "cv_filename", "candidate_id", "position_id", "score",
        "overall_score", "evidence_coverage", "screening_outcome", "recommendation",
        "questions", "candidates", "email", "phone",
    }
    path = project_root / "api" / "schemas" / "public.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    field_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name.endswith("Response"):
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    field_names.add(stmt.target.id)
    assert field_names, "expected at least one response model field in api/schemas/public.py"
    assert not (field_names & forbidden), field_names & forbidden


def test_frontend_keeps_api_calls_in_its_api_layer(project_root: Path) -> None:
    """The React app's own layering rule, mirroring the backend's: only
    frontend/src/api/ may call fetch(). Pages and components go through that
    layer, so auth headers and error handling are never re-implemented ad hoc."""
    src = project_root / "frontend" / "src"
    if not src.exists():
        pytest.skip("frontend/src not present")

    violations: list[str] = []
    for path in sorted([*src.rglob("*.ts"), *src.rglob("*.tsx")]):
        relative = path.relative_to(project_root)
        parts = relative.parts
        # frontend/src/api/** is the allowed home for fetch; test files may stub it.
        if parts[:3] == ("frontend", "src", "api"):
            continue
        if path.name.endswith((".test.ts", ".test.tsx")):
            continue
        if "fetch(" in path.read_text(encoding="utf-8"):
            violations.append(str(relative))
    assert violations == []
