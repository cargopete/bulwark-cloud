"""AuditRunner: orchestrates the full audit lifecycle inside the Fargate task.

Directory layout inside the ECS task:
    /tmp/audit/{job_id}/                      ← job_dir   (BULWARK_ROOT, bulwark.toml here)
    /tmp/audit/{job_id}/target/               ← audit_dir (AUDIT_DIR, cloned repo)
    /tmp/audit/{job_id}/target/audit-workspace/  ← bulwark workspace (intermediate files)
    /tmp/audit/{job_id}/target/audit-workspace/final-report.json  ← uploaded to S3 report/
"""
from __future__ import annotations

import os
import re
import subprocess
import textwrap
from pathlib import Path

import boto3
import structlog

from .bulwark_log_parser import BulwarkLogParser
from .config import JobEnv
from .dynamodb_writer import DynamoWriter
from .s3_uploader import S3Uploader

log = structlog.get_logger()

JOB_ROOT = Path("/tmp/audit")

# Must match bulwark's default workspace.path config value
BULWARK_WORKSPACE_REL = "audit-workspace"

# Context generation — bounds on the headless Claude session that writes the
# target-specific context/ files before the bulwark pipeline runs.
CONTEXT_GEN_MAX_TURNS = 40
CONTEXT_GEN_TIMEOUT_S = 600

# The three context files bulwark resolves project-first. ATTACK_PATTERNS.md is
# deliberately excluded — bulwark always sources it from the install dir.
CONTEXT_FILES = ("AUDIT_CONTEXT.md", "PROPERTIES.md", "KNOWN_ISSUES.md")

# Exit codes — see entrypoint.py module docstring
EXIT_SUCCESS = 0
EXIT_JOB_COMPILE_ERROR = 1
EXIT_JOB_UNREACHABLE_REPO = 2
EXIT_TRANSIENT_INFRA = 11
EXIT_TERMINAL_INFRA = 20


class AuditRunner:
    def __init__(self, env: JobEnv) -> None:
        self.env = env
        # job_dir: holds bulwark.toml; passed as BULWARK_ROOT to bulwark
        self.job_dir = JOB_ROOT / env.job_id
        # target_dir: the cloned repo; passed as AUDIT_DIR to bulwark
        self.target_dir = self.job_dir / "target"
        # bulwark_workspace: where bulwark writes all intermediate files + final report
        self.bulwark_workspace = self.target_dir / BULWARK_WORKSPACE_REL
        self.log = log.bind(job_id=env.job_id)

        session = boto3.Session(region_name=env.aws_region)
        self._secrets = session.client("secretsmanager")
        self.dynamo = DynamoWriter(session, env.dynamo_table, env.job_id)
        self.s3 = S3Uploader(session, env.s3_bucket, env.job_id)
        self._parser = BulwarkLogParser(self.dynamo)

    # ── Public entry point ─────────────────────────────────────────────────

    def run(self) -> int:
        try:
            self.dynamo.mark_running()
            api_key = self._fetch_anthropic_key()
        except Exception as exc:
            self.log.error("startup_failed", error=str(exc))
            return EXIT_TERMINAL_INFRA

        try:
            self._prepare_workspace()
        except subprocess.CalledProcessError as exc:
            self.log.error("git_clone_failed", stderr=exc.stderr)
            self.dynamo.mark_failed(reason="UNREACHABLE_REPO", detail=str(exc))
            return EXIT_JOB_UNREACHABLE_REPO
        except Exception as exc:
            self.log.error("workspace_prep_failed", error=str(exc))
            return EXIT_TRANSIENT_INFRA

        # Context first: _render_config reads the generated PROPERTIES.md to set
        # the formal pass's target_properties.
        self._generate_context(api_key)
        self._render_config()

        exit_code = self._run_bulwark(api_key)

        # Always upload whatever artefacts exist, even on partial failure
        try:
            uploaded = self.s3.upload_workspace(self.bulwark_workspace)
            self.s3.upload_report(self.bulwark_workspace)
            self.log.info("artefacts_uploaded", files=uploaded)
        except Exception as exc:
            self.log.error("s3_upload_failed", error=str(exc))
            return EXIT_TRANSIENT_INFRA

        try:
            if exit_code == 0:
                self.dynamo.mark_completed()
            else:
                self.dynamo.mark_failed(
                    reason="BULWARK_ERROR", detail=f"exit_code={exit_code}"
                )
        except Exception as exc:
            self.log.error("dynamo_final_update_failed", error=str(exc))
            return EXIT_TRANSIENT_INFRA

        return exit_code

    # ── Private helpers ────────────────────────────────────────────────────

    def _fetch_anthropic_key(self) -> str:
        resp = self._secrets.get_secret_value(SecretId=self.env.secret_arn_anthropic)
        return str(resp["SecretString"])

    def _prepare_workspace(self) -> None:
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.log.info("git_clone_start", repo=self.env.target_repo, branch=self.env.target_branch)
        subprocess.run(
            [
                "git", "clone",
                "--depth=1",
                "--branch", self.env.target_branch,
                self.env.target_repo,
                str(self.target_dir),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.log.info("git_clone_done")

    def _render_config(self) -> None:
        scope_items = "\n".join(f'  "{s}",' for s in self.env.target_scope)
        config = textwrap.dedent(f"""\
            # Auto-generated by bulwark-cloud for job {self.env.job_id}
            [target]
            repo   = "{self.env.target_repo}"
            branch = "{self.env.target_branch}"
            scope  = [
            {scope_items}
            ]
            core_contracts = []

            model = "{self.env.model}"
        """)

        # Point the formal pass at the properties we actually generated. Without
        # this, bulwark falls back to a hardcoded default property set that is
        # specific to an unrelated protocol, so Halmos verifies nothing.
        property_ids = self._extract_property_ids()
        if property_ids:
            ids = ", ".join(f'"{p}"' for p in property_ids)
            config += textwrap.dedent(f"""
                [passes.formal]
                target_properties = [{ids}]
            """)

        (self.job_dir / "bulwark.toml").write_text(config)
        self.log.info(
            "config_rendered", job_dir=str(self.job_dir), target_properties=property_ids
        )

    def _extract_property_ids(self) -> list[str]:
        """Pull the P-N property identifiers from the generated PROPERTIES.md.

        Matches markdown headers like `## P-1: ...`. Order-preserving and
        de-duplicated so the formal pass targets each property exactly once.
        """
        properties_file = self.job_dir / "context" / "PROPERTIES.md"
        if not properties_file.exists():
            return []
        seen: dict[str, None] = {}
        for line in properties_file.read_text().splitlines():
            match = re.match(r"^#{1,6}\s*(P-\d+)\b", line.strip())
            if match:
                seen.setdefault(match.group(1), None)
        return list(seen)

    def _generate_context(self, api_key: str) -> None:
        """Generate target-specific context files for the cloned repo.

        bulwark resolves context project-first (BULWARK_ROOT/context/) then falls
        back to the install dir — which ships with a hand-written context for an
        unrelated protocol. For arbitrary submitted repos there is no human to
        author context, so we generate it with a headless Claude session that
        reads the in-scope contracts.

        Best-effort: any file the session fails to produce is backfilled with
        neutral content so the pipeline never inherits the install-dir context.
        """
        context_dir = self.job_dir / "context"
        context_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._run_context_session(api_key, context_dir)
        except Exception as exc:
            self.log.error("context_generation_failed", error=str(exc))

        self._backfill_missing_context(context_dir)

    def _run_context_session(self, api_key: str, context_dir: Path) -> None:
        scope_desc = ", ".join(self.env.target_scope) if self.env.target_scope else "the whole repository"
        prompt = textwrap.dedent(f"""\
            You are preparing audit context for an automated smart-contract security
            pipeline. The target repository is cloned at ./target. The in-scope code is:
            {scope_desc}.

            Read the in-scope Solidity/Vyper source under ./target (use Glob/Grep/Read).
            Base everything on the ACTUAL code you find — never invent contract names.

            Write exactly these three files into ./context (overwrite if present):

            1. AUDIT_CONTEXT.md — sections: Protocol Overview, Architecture (real contract
               names and how they interact), Trust Model (privileged roles and powers),
               Attack Surface (where value lives, fund-moving entry points, external deps),
               Economic Parameters. Be concrete and specific to this codebase.

            2. PROPERTIES.md — security invariants as P-1, P-2, ... Each: **Informal**
               one-liner, **Formal** pseudo-code if applicable, **Enforcement** the real
               function(s) involved. Derive 5-12 properties from the actual contracts
               (access control, accounting/solvency, supply conservation, reentrancy,
               arithmetic bounds). Tie every property to concrete code paths.

            3. KNOWN_ISSUES.md — list as KI-1, KI-2, ... only genuine accepted risks evident
               from the code/comments/NatSpec. If none are apparent, write a single line
               stating no known issues were identified from the source.

            Do not run the audit or write any other files. When all three files are
            written, stop.
        """)

        cmd = [
            "claude", "-p", prompt,
            "--max-turns", str(CONTEXT_GEN_MAX_TURNS),
            "--model", self.env.model,
            "--verbose",
        ]
        env = {**os.environ, "ANTHROPIC_API_KEY": api_key}

        self.log.info("context_generation_start", scope=self.env.target_scope, model=self.env.model)
        proc = subprocess.run(
            cmd,
            env=env,
            cwd=str(self.job_dir),
            capture_output=True,
            text=True,
            timeout=CONTEXT_GEN_TIMEOUT_S,
        )
        written = [f for f in CONTEXT_FILES if (context_dir / f).exists()]
        self.log.info(
            "context_generation_done",
            exit_code=proc.returncode,
            files_written=written,
        )

    def _backfill_missing_context(self, context_dir: Path) -> None:
        """Write neutral content for any context file the session didn't produce."""
        scope_desc = ", ".join(self.env.target_scope) if self.env.target_scope else "entire repository"
        fallbacks = {
            "AUDIT_CONTEXT.md": textwrap.dedent(f"""\
                # Audit Context

                ## Protocol Overview

                Automated security audit of {self.env.target_repo} (branch
                {self.env.target_branch}). In scope: {scope_desc}.

                No protocol-specific context was supplied. Audit the contracts found in
                scope on their own merits. Do not assume the presence of any contract that
                is not actually in the repository.

                ## Trust Model

                Infer privileged roles (owner/admin/operator) from the access-control
                modifiers present in the in-scope code.

                ## Attack Surface

                All external and public state-changing functions in the in-scope contracts.
            """),
            "PROPERTIES.md": textwrap.dedent("""\
                # Security Properties

                No protocol-specific invariants were supplied. Verify the standard
                smart-contract security properties against the in-scope code:

                ## P-1: Access control integrity
                **Informal**: Privileged operations are reachable only by authorised roles.

                ## P-2: Accounting solvency
                **Informal**: Tracked balances never exceed actual holdings; no path lets a
                user withdraw more than they are owed.

                ## P-3: Arithmetic safety
                **Informal**: No unintended overflow/underflow or precision-loss path alters
                balances or accounting.

                ## P-4: Reentrancy safety
                **Informal**: State-changing external calls cannot be re-entered to violate
                invariants.

                ## P-5: Supply conservation
                **Informal**: Token mint/burn accounting is conserved across all paths.
            """),
            "KNOWN_ISSUES.md": textwrap.dedent("""\
                # Known Issues and Accepted Risks

                No known issues were supplied for this target.
            """),
        }
        backfilled = []
        for filename, content in fallbacks.items():
            dest = context_dir / filename
            if not dest.exists() or not dest.read_text().strip():
                dest.write_text(content)
                backfilled.append(filename)
        if backfilled:
            self.log.info("context_backfilled", files=backfilled)

    def _run_bulwark(self, api_key: str) -> int:
        self.log.info("bulwark_start")
        env = {
            **os.environ,
            "ANTHROPIC_API_KEY": api_key,
            # AUDIT_DIR: where bulwark looks for the project source code
            "AUDIT_DIR": str(self.target_dir),
            # BULWARK_ROOT: where bulwark finds bulwark.toml and context/ files
            "BULWARK_ROOT": str(self.job_dir),
            # BULWARK_INSTALL is already set in the Docker image (to /home/auditor)
        }

        proc = subprocess.Popen(
            ["bulwark", "run"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,   # combine stderr so parser sees all output
            cwd=str(self.job_dir),      # cwd where bulwark.toml is
            text=True,
        )

        assert proc.stdout is not None
        for line in proc.stdout:
            stripped = line.rstrip()
            # Structured progress lines emitted by bulwark to stdout —
            # updates DynamoDB pass records in real time.
            self._parser.handle_line(stripped)
            self.log.info("bulwark_stdout", line=stripped)

        return proc.wait()
