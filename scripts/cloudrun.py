"""Prepare or deploy Foodiary with gcloud. Uses only Python's standard library.

Run init and plan locally; run cloud actions in an authenticated Google Cloud Shell.
Secret values are read from private files, never passed as command arguments.
"""

import argparse
import getpass
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SECRET_KEYS = ("DATABASE_URL", "JWT_SECRET", "SMTP_PASSWORD")
# Optional: when a version is recorded, the password-protected /receipt-test page is deployed.
TEST_PASSWORD_KEY = "RECEIPT_TEST_PASSWORD"
APIS = ("run.googleapis.com", "cloudbuild.googleapis.com", "artifactregistry.googleapis.com",
        "secretmanager.googleapis.com", "iam.googleapis.com", "storage.googleapis.com")


def normalize_database_url(value: str) -> str:
    try:
        url = urlsplit(value.strip())
        if url.scheme not in {"postgres", "postgresql", "postgresql+psycopg"} or not url.hostname or not url.username:
            raise ValueError
        if len(unquote(url.password or "")) < 24 or not url.path.strip("/") or url.fragment:
            raise ValueError
        _ = url.port
        query = dict(parse_qsl(url.query))
        mode = query.setdefault("sslmode", "verify-full")
        if mode not in {"require", "verify-ca", "verify-full"}:
            raise ValueError
        if mode in {"verify-ca", "verify-full"}:
            query.setdefault("sslrootcert", "system")
        query["connect_timeout"] = "10"
        return urlunsplit(("postgresql+psycopg", url.netloc, url.path, urlencode(query), ""))
    except (ValueError, TypeError):
        raise ValueError("Use a PostgreSQL URL with TLS, a database name, and a password of at least 24 characters") from None


def validate_config(config: dict) -> None:
    patterns = {
        "project": r"[a-z][a-z0-9-]{4,28}[a-z0-9]",
        "project_number": r"[0-9]{6,20}",
        "region": r"[a-z]+-[a-z]+[0-9]",
        "service": r"[a-z][a-z0-9-]{0,18}[a-z0-9]",
        "repository": r"[a-z][a-z0-9-]{0,28}[a-z0-9]",
        "bucket": r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]",
    }
    for key, pattern in patterns.items():
        if not re.fullmatch(pattern, str(config.get(key, ""))):
            raise ValueError(f"Invalid or missing {key}")
    if not isinstance(config.get("origins"), list) or not config["origins"]:
        raise ValueError("At least one HTTPS frontend origin is required")
    for origin in config["origins"]:
        url = urlsplit(origin)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment or url.path:
            raise ValueError("Frontend origins must be HTTPS origins without paths or credentials")
    for key in ("smtp_host", "smtp_user", "mail_from"):
        if not config.get(key) or any(c in config[key] for c in "\r\n\x00"):
            raise ValueError(f"Invalid or missing {key}")


def save_private(path: Path, value: str, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        account = subprocess.check_output(["whoami.exe"], text=True).strip()
        subprocess.run(["icacls.exe", str(path.parent), "/inheritance:r", "/grant:r", f"{account}:(OI)(CI)F"],
                       check=True, stdout=subprocess.DEVNULL)
    else:
        path.parent.chmod(0o700)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    with os.fdopen(os.open(path, flags, 0o600), "w", encoding="utf-8", newline="\n") as target:
        target.write(value)


def service_url(config: dict) -> str:
    return f"https://{config['service']}-{config['project_number']}.{config['region']}.run.app"


def environment(config: dict) -> dict[str, str]:
    return dict(
        APP_ENV="production", DEV_TOOLS="false", AUTO_CREATE_TABLES="false",
        TRUSTED_HOSTS=f"{urlsplit(service_url(config)).hostname},localhost,127.0.0.1",
        CORS_ORIGINS=",".join(config["origins"]), UPLOAD_DIR="/app/uploads", MAX_UPLOAD_MB="10",
        OCR_ENGINE="paddle", OCR_LANG="korean", OCR_DET_MAX_SIDE="1280", OCR_CPU_THREADS="4", OCR_ENGINES="2",
        OCR_ORIENTATION_MODEL="PP-LCNet_x1_0_doc_ori",
        OCR_WARMUP="true", OCR_FAIL_CLOSED="true", RATE_LIMIT_ENABLED="true", RATE_LIMIT_SCOPE="global",
        AUTH_REQUESTS_PER_MINUTE="30", OCR_REQUESTS_PER_MINUTE="30", EMAIL_BACKEND="smtp",
        SMTP_HOST=config["smtp_host"], SMTP_PORT="587", SMTP_USER=config["smtp_user"], MAIL_FROM=config["mail_from"],
        TIMEZONE="Asia/Seoul",
    )


def check_recipe_seed() -> None:
    """The Cloud Run image carries the recipe database. Stop before the upload instead of deep inside the Docker build."""
    seed = ROOT / "recipe_data" / "image-seed"
    try:
        pinned = json.loads((ROOT / "recipe-package.json").read_text(encoding="utf-8"))["sha256"]
        manifest = json.loads((seed / "manifest.json").read_text(encoding="utf-8"))
        matches = manifest["sha256"] == pinned and (seed / "recipes.sqlite3").stat().st_size == manifest["database_bytes"]
    except (OSError, KeyError, ValueError):
        matches = False
    if not matches:
        raise RuntimeError("Recipe data is missing or differs from recipe-package.json. "
                           "Run python3 -m scripts.prepare_recipes first (see deploy/CLOUD_RUN.md).")


class Deployment:
    def __init__(self, config: dict, folder: Path):
        validate_config(config)
        self.config, self.folder = config, folder
        self.project, self.region, self.service = (config[k] for k in ("project", "region", "service"))

    def gcloud(self, *arguments: str, capture: bool = False, missing_ok: bool = False) -> str | None:
        executable = shutil.which("gcloud")
        if not executable:
            raise RuntimeError("Run cloud actions in Google Cloud Shell, where gcloud is installed and authenticated")
        result = subprocess.run([executable, *arguments, f"--project={self.project}", "--quiet"],
                                cwd=ROOT, text=True, capture_output=capture or missing_ok)
        if result.returncode:
            error = result.stderr or ""
            if missing_ok and ("NOT_FOUND" in error or "does not exist" in error or "not found" in error):
                return None
            # None of the arguments contains a secret value.
            if error:
                print(error, file=sys.stderr)
            raise RuntimeError(f"gcloud {' '.join(arguments[:3])} failed")
        return (result.stdout or "").strip()

    def account(self, suffix: str) -> str:
        return f"{self.service}-{suffix}@{self.project}.iam.gserviceaccount.com"

    def secret_name(self, key: str) -> str:
        return f"{self.service}-{key.lower().replace('_', '-')}"

    def plan(self) -> dict:
        return dict(project=self.project, region=self.region, service=self.service, url=service_url(self.config),
                    cpu=8, memory="8Gi", min_instances=0, max_instances=1, concurrency=20,
                    billing="request-based", database="external PostgreSQL over TLS (Neon Free recommended)",
                    uploads=f"private gs://{self.config['bucket']} mounted at /app/uploads",
                    secrets=list(SECRET_KEYS), initial_access="IAM private; publish after verification",
                    credit="Claim Pro $10/month separately. This is not a spending cap.")

    def setup(self) -> None:
        number = self.gcloud("projects", "describe", self.project, "--format=value(projectNumber)", capture=True)
        if number != self.config["project_number"]:
            raise RuntimeError("Configured project number does not match the authenticated project")
        self.gcloud("services", "enable", *APIS)
        for suffix in ("run", "migrate", "build"):
            account = self.account(suffix)
            if self.gcloud("iam", "service-accounts", "describe", account, capture=True, missing_ok=True) is None:
                self.gcloud("iam", "service-accounts", "create", f"{self.service}-{suffix}")
        repository = self.config["repository"]
        if self.gcloud("artifacts", "repositories", "describe", repository, f"--location={self.region}",
                       capture=True, missing_ok=True) is None:
            self.gcloud("artifacts", "repositories", "create", repository, "--repository-format=docker",
                        f"--location={self.region}")
        for bucket in (self.config["bucket"], self.build_bucket):
            result = self.gcloud("storage", "buckets", "describe", f"gs://{bucket}", "--format=json",
                                 capture=True, missing_ok=True)
            if result is None:
                self.gcloud("storage", "buckets", "create", f"gs://{bucket}", f"--location={self.region}",
                            "--uniform-bucket-level-access", "--public-access-prevention")
            else:
                details = json.loads(result)
                number = details.get("project_number", details.get("projectNumber"))
                if str(number) != self.config["project_number"]:
                    raise RuntimeError("Could not verify that the existing bucket belongs to this project")
                self.gcloud("storage", "buckets", "update", f"gs://{bucket}",
                            "--uniform-bucket-level-access", "--public-access-prevention")
        self.gcloud("storage", "buckets", "add-iam-policy-binding", f"gs://{self.config['bucket']}",
                    f"--member=serviceAccount:{self.account('run')}", "--role=roles/storage.objectUser")
        self.gcloud("storage", "buckets", "add-iam-policy-binding", f"gs://{self.build_bucket}",
                    f"--member=serviceAccount:{self.account('build')}", "--role=roles/storage.objectViewer")
        self.gcloud("artifacts", "repositories", "add-iam-policy-binding", repository, f"--location={self.region}",
                    f"--member=serviceAccount:{self.account('build')}", "--role=roles/artifactregistry.writer")
        self.gcloud("projects", "add-iam-policy-binding", self.project,
                    f"--member=serviceAccount:{self.account('build')}", "--role=roles/logging.logWriter")
        for key in SECRET_KEYS:
            name = self.secret_name(key)
            if self.gcloud("secrets", "describe", name, capture=True, missing_ok=True) is None:
                self.gcloud("secrets", "create", name, "--replication-policy=automatic")
            for suffix in (("run", "migrate") if key == "DATABASE_URL" else ("run",)):
                self.gcloud("secrets", "add-iam-policy-binding", name,
                            f"--member=serviceAccount:{self.account(suffix)}", "--role=roles/secretmanager.secretAccessor")

    @property
    def build_bucket(self) -> str:
        return f"{self.project}-{self.service}-builds"

    def upload_secrets(self) -> None:
        paths = {key: self.folder / "secrets" / key for key in SECRET_KEYS}
        values = {key: path.read_text(encoding="utf-8").strip() for key, path in paths.items()}
        values["DATABASE_URL"] = normalize_database_url(values["DATABASE_URL"])
        if len(values["JWT_SECRET"]) < 48 or not values["SMTP_PASSWORD"]:
            raise ValueError("Private secret files are incomplete")
        save_private(paths["DATABASE_URL"], values["DATABASE_URL"])
        # The optional test password keeps its own version; test-password manages it.
        versions = {key: value for key, value in self.versions().items() if key == TEST_PASSWORD_KEY}
        for key, path in paths.items():
            name = self.gcloud("secrets", "versions", "add", self.secret_name(key),
                               f"--data-file={path.resolve()}", "--format=value(name)", capture=True)
            versions[key] = name.rsplit("/", 1)[-1]
        save_private(self.folder / "versions.json", json.dumps(versions))
        print("Uploaded three secrets; recorded immutable version numbers without printing their values.")

    def versions(self) -> dict[str, str]:
        path = self.folder / "versions.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    @property
    def test_page(self) -> bool:
        return TEST_PASSWORD_KEY in self.versions()

    def runtime_secret_keys(self) -> tuple[str, ...]:
        return SECRET_KEYS + ((TEST_PASSWORD_KEY,) if self.test_page else ())

    def secret_bindings(self, keys=SECRET_KEYS) -> str:
        versions = self.versions()
        if any(not str(versions.get(key, "")).isdigit() for key in keys):
            raise ValueError("Upload secrets before deploying")
        return ",".join(f"{key}={self.secret_name(key)}:{versions[key]}" for key in keys)

    def set_test_password(self, *, disable: bool = False) -> None:
        versions = self.versions()
        if disable:
            versions.pop(TEST_PASSWORD_KEY, None)
            save_private(self.folder / "versions.json", json.dumps(versions))
            print("The next deploy turns the test page off. Run deploy, verify and publish.")
            return
        # Typed on a phone, sent in an HTTP header: printable ASCII without spaces.
        password = getpass.getpass("New receipt test page password (hidden, 12+ characters): ").strip()
        if not re.fullmatch(r"[\x21-\x7e]{12,}", password):
            raise ValueError("Use at least 12 English letters, digits or symbols without spaces. Nothing was changed.")
        if getpass.getpass("Repeat the password (hidden): ").strip() != password:
            raise ValueError("The two passwords differ. Nothing was changed.")
        path = self.folder / "secrets" / TEST_PASSWORD_KEY
        save_private(path, password)
        name = self.secret_name(TEST_PASSWORD_KEY)
        if self.gcloud("secrets", "describe", name, capture=True, missing_ok=True) is None:
            self.gcloud("secrets", "create", name, "--replication-policy=automatic")
        self.gcloud("secrets", "add-iam-policy-binding", name, f"--member=serviceAccount:{self.account('run')}",
                    "--role=roles/secretmanager.secretAccessor")
        version = self.gcloud("secrets", "versions", "add", name, f"--data-file={path.resolve()}",
                              "--format=value(name)", capture=True)
        versions[TEST_PASSWORD_KEY] = version.rsplit("/", 1)[-1]
        save_private(self.folder / "versions.json", json.dumps(versions))
        print("Stored the test password as a new secret version without printing it. Run deploy, verify and publish.")

    def build(self) -> None:
        check_recipe_seed()
        tag = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        image = f"{self.region}-docker.pkg.dev/{self.project}/{self.config['repository']}/{self.service}:{tag}"
        self.gcloud("builds", "submit", str(ROOT), f"--config={ROOT / 'deploy/cloudbuild.cloudrun.yaml'}",
                    f"--ignore-file={ROOT / '.gcloudignore'}", f"--substitutions=_IMAGE={image}",
                    f"--region={self.region}", "--machine-type=e2-standard-2",
                    f"--gcs-source-staging-dir=gs://{self.build_bucket}/source",
                    f"--service-account=projects/{self.project}/serviceAccounts/{self.account('build')}")
        digest = self.gcloud("artifacts", "docker", "images", "describe", image,
                             "--format=value(image_summary.digest)", capture=True)
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest or ""):
            raise RuntimeError("Could not resolve the built image digest")
        save_private(self.folder / "image.txt", image.rsplit(":", 1)[0] + "@" + digest)

    def image(self) -> str:
        image = (self.folder / "image.txt").read_text(encoding="utf-8").strip()
        prefix = f"{self.region}-docker.pkg.dev/{self.project}/{self.config['repository']}/{self.service}@sha256:"
        if not image.startswith(prefix) or not re.fullmatch(r"[a-f0-9]{64}", image.removeprefix(prefix)):
            raise ValueError("Build the image before deploying; an immutable image digest is required")
        return image

    def migrate(self) -> None:
        self.gcloud("run", "jobs", "deploy", f"{self.service}-migrate", f"--region={self.region}",
                    f"--image={self.image()}", f"--service-account={self.account('migrate')}",
                    "--command=alembic", "--args=upgrade,head", "--tasks=1", "--parallelism=1", "--max-retries=0",
                    "--cpu=1", "--memory=512Mi", "--task-timeout=300s",
                    f"--set-secrets={self.secret_bindings(('DATABASE_URL',))}")
        self.gcloud("run", "jobs", "execute", f"{self.service}-migrate", f"--region={self.region}", "--wait")
        save_private(self.folder / "migration.json", json.dumps(self.migration_signature()))

    def migration_signature(self) -> dict[str, str]:
        return dict(image=self.image(), database_secret=self.secret_bindings(("DATABASE_URL",)))

    def deploy(self) -> None:
        migrated = self.folder / "migration.json"
        if not migrated.exists() or json.loads(migrated.read_text(encoding="utf-8")) != self.migration_signature():
            raise ValueError("Run the migration job successfully for this image before deploying")
        env_file = self.folder / "env.json"
        save_private(env_file, json.dumps(environment(self.config)))
        self.gcloud("run", "deploy", self.service, f"--region={self.region}", f"--image={self.image()}",
                    f"--service-account={self.account('run')}", "--execution-environment=gen2", "--port=8080",
                    "--cpu=8", "--memory=8Gi", "--cpu-throttling", "--cpu-boost", "--concurrency=20",
                    "--min=0", "--max=1", "--min-instances=0", "--max-instances=1", "--timeout=180s",
                    "--no-allow-unauthenticated", f"--env-vars-file={env_file.resolve()}",
                    f"--set-secrets={self.secret_bindings(self.runtime_secret_keys())}",
                    f"--add-volume=name=receipts,type=cloud-storage,bucket={self.config['bucket']},mount-options=uid=10001;gid=10001;file-mode=0600;dir-mode=0700",
                    "--add-volume-mount=volume=receipts,mount-path=/app/uploads",
                    "--startup-probe=tcpSocket.port=8080,periodSeconds=5,timeoutSeconds=5,failureThreshold=48")

    def verify(self, *, private: bool = True) -> None:
        token = self.gcloud("auth", "print-identity-token", capture=True) if private else None
        headers = {"X-Serverless-Authorization": f"Bearer {token}"} if private else {}
        # The test page exists only with a test password, and must refuse recognition without it.
        test_page = self.test_page
        checks = (("GET", "/ready", 200), ("GET", "/fridges", 401), ("GET", "/docs", 404), ("GET", "/redoc", 404),
                  ("GET", "/openapi.json", 404), ("GET", "/dev/receipt-test", 404),
                  ("GET", "/receipt-test", 200 if test_page else 404),
                  ("POST", "/receipt-test", 401 if test_page else 404))
        for method, path, expected in checks:
            request = Request(service_url(self.config) + path, headers=headers, method=method,
                              data=b"" if method == "POST" else None)
            try:
                response = urlopen(request, timeout=180)
            except HTTPError as error:
                response = error
            with response:
                if response.status != expected:
                    raise RuntimeError(f"{method} {path}: expected HTTP {expected}, received {response.status}")
                if response.headers.get("X-Content-Type-Options") != "nosniff":
                    raise RuntimeError("Application security headers missing")
                if path == "/ready":
                    if json.load(response).get("ocr") != "ready" or not response.headers.get("Strict-Transport-Security"):
                        raise RuntimeError("OCR readiness or HTTPS security headers missing")
            print(f"{method} {path}: HTTP {expected}")
        print("Read-only checks passed. Test actual signup email and an authenticated receipt upload before launch.")

    def publish(self) -> None:
        self.verify()
        self.gcloud("run", "services", "add-iam-policy-binding", self.service, f"--region={self.region}",
                    "--member=allUsers", "--role=roles/run.invoker")
        self.verify(private=False)
        print(service_url(self.config))


def initialize(args, config_path: Path) -> None:
    if config_path.exists() or any((config_path.parent / "secrets" / key).exists() for key in SECRET_KEYS):
        raise ValueError("Configuration or private secret files already exist. No existing files were changed.")
    config = dict(project=args.project, project_number=args.project_number, region=args.region,
                  service=args.service, repository="foodiary", bucket=f"{args.project}-{args.service}-uploads",
                  origins=args.origin or [f"https://{args.service}-{args.project_number}.{args.region}.run.app"],
                  smtp_host="smtp.gmail.com", smtp_user=input("SMTP user email: ").strip(), mail_from="")
    config["mail_from"] = input("Mail From (Enter = SMTP user): ").strip() or config["smtp_user"]
    validate_config(config)
    database_url = normalize_database_url(getpass.getpass("External PostgreSQL connection URL (hidden): "))
    smtp_password = getpass.getpass("SMTP app password (hidden): ").strip()
    if not smtp_password:
        raise ValueError("SMTP app password is required")
    folder = config_path.parent
    values = dict(DATABASE_URL=database_url, JWT_SECRET=secrets.token_urlsafe(48), SMTP_PASSWORD=smtp_password)
    for key, value in values.items():
        save_private(folder / "secrets" / key, value, exclusive=True)
    save_private(config_path, json.dumps(config, indent=2) + "\n", exclusive=True)
    print(f"Prepared private configuration at {config_path}. No cloud resources were created.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / ".cloudrun" / "config.json")
    commands = parser.add_subparsers(dest="action", required=True)
    init = commands.add_parser("init", help="Create private local configuration; does not contact Google Cloud")
    init.add_argument("--project", required=True)
    init.add_argument("--project-number", required=True)
    init.add_argument("--region", default="asia-northeast1")
    init.add_argument("--service", default="foodiary")
    init.add_argument("--origin", action="append", help="Frontend HTTPS origin; can be specified multiple times")
    for action in ("plan", "setup", "secrets", "build", "migrate", "deploy", "verify", "publish"):
        commands.add_parser(action)
    test_password = commands.add_parser("test-password", help="Set (or --disable) the /receipt-test page password")
    test_password.add_argument("--disable", action="store_true", help="Turn the test page off at the next deploy")
    args = parser.parse_args()
    try:
        if args.action == "init":
            initialize(args, args.config)
            return
        deployment = Deployment(json.loads(args.config.read_text(encoding="utf-8")), args.config.parent)
        if args.action == "plan":
            print(json.dumps(deployment.plan(), indent=2, ensure_ascii=False))
        elif args.action == "test-password":
            deployment.set_test_password(disable=args.disable)
        else:
            action = deployment.upload_secrets if args.action == "secrets" else getattr(deployment, args.action)
            action()
    except URLError:
        parser.exit(1, "Could not reach the deployed HTTPS service. Check its URL, IAM, TLS and service logs.\n")
    except (OSError, ValueError, RuntimeError) as error:
        # Never print config/secret values or a repr of a failing URL.
        if isinstance(error, OSError):
            parser.exit(1, "Required local configuration or executable is unavailable. Run init first.\n")
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
